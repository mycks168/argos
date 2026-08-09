import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

from argos.services.dashboard import location as dashboard_location
from argos.services.dashboard import server as dashboard_server
from argos.services.dashboard.location import parse_gpsd_tpv, parse_nmea_location, parse_remote_location
from argos.services.dashboard.server import DashboardServer, _apply_event
from argos.services.dashboard.state import DashboardState


def _read_json(url, method="GET", payload=None, token=""):
    """HTTP APIを呼び出してJSONレスポンスを返す。"""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_dashboard_state_keeps_messages_notifications_and_status():
    """画面へ表示する状態をまとめて保持できる。"""
    state = DashboardState()
    state.set_status("thinking", "考え中")
    state.set_agent("アンチグラビティ", "antigravity", "Gemini Test")
    state.set_audio_muted(True)
    state.set_audio_volume(64)
    state.set_microphone_enabled(False)
    state.set_wifi_status(
        {
            "connected": True,
            "interface": "wlan0",
            "ssid": "車内WiFi",
            "quality": 71,
            "level_dbm": -45,
        }
    )
    state.set_agent_usage(
        "antigravity",
        {
            "available": True,
            "label": "利用枠",
            "five_hour": {"label": "5時間", "remain_percentage": 95.0, "use_percentage": 5.0, "reset_at": "10:00"},
            "weekly": {"label": "週間", "remain_percentage": 34.0, "use_percentage": 66.0, "reset_at": "06/19 06:59"},
            "other_text": "878 credits",
            "error": "",
        },
    )
    message_id = state.add_message("assistant", "", streaming=True)
    state.append_message(message_id, "返答")
    state.finish_message(message_id)
    state.add_notification("メール", "新着があります", source="mail")

    snapshot = state.snapshot()

    assert snapshot["status"]["code"] == "thinking"
    assert snapshot["agent"]["name"] == "アンチグラビティ"
    assert snapshot["agent"]["provider"] == "antigravity"
    assert snapshot["agent"]["model"] == "Gemini Test"
    assert snapshot["slots"][0]["name"] == "アンチグラビティ"
    assert snapshot["slots"][0]["active"] is True
    assert snapshot["audio"]["muted"] is True
    assert snapshot["audio"]["volume"] == 64
    assert snapshot["microphone"]["enabled"] is False
    assert snapshot["network"]["wifi"]["connected"] is True
    assert snapshot["network"]["wifi"]["ssid"] == "車内WiFi"
    assert snapshot["network"]["wifi"]["quality"] == 71
    assert snapshot["agent_usage"]["current"]["weekly"]["remain_percentage"] == 34.0
    assert snapshot["display_activity"]["sequence"] == 0
    assert snapshot["messages"][0]["text"] == "返答"
    assert snapshot["messages"][0]["streaming"] is False
    assert snapshot["notifications"][0]["title"] == "メール"


def test_dashboard_state_uses_remote_slot_usage_provider():
    """リモートスロットでも接続先プロバイダの利用枠を現在値として返す。"""
    state = DashboardState()
    state.set_agent_usage("codex", {"available": True, "label": "Codex"})

    state.set_agent("mint-codex", "remote", usage_provider="codex")

    snapshot = state.snapshot()
    assert snapshot["agent"]["provider"] == "remote"
    assert snapshot["agent"]["usage_provider"] == "codex"
    assert snapshot["agent_usage"]["current"]["provider"] == "codex"


def test_dashboard_state_wake_display_updates_activity():
    """音声再生などで画面を起こすためのアクティビティを更新できる。"""
    state = DashboardState()

    state.wake_display()
    snapshot = state.snapshot()

    assert snapshot["display_activity"]["sequence"] == 1
    assert snapshot["display_activity"]["updated_at"]


def test_dashboard_state_notifies_subscribers():
    """状態更新をSSE用キューへ通知する。"""
    state = DashboardState()
    subscriber = state.subscribe()

    state.add_message("user", "こんにちは")

    assert subscriber.get(timeout=1) > 0
    state.unsubscribe(subscriber)


def test_dashboard_state_keeps_messages_per_agent_slot():
    """会話履歴は現在スロットごとに分けて表示する。"""
    state = DashboardState()
    state.set_agent("作業", "codex")
    first_id = state.add_message("assistant", "作業スロット")

    state.set_agent("調査", "antigravity")
    assert state.snapshot()["messages"] == []
    second_id = state.add_message("assistant", "調査スロット", streaming=True)

    state.set_agent("作業", "codex")
    snapshot = state.snapshot()
    assert [message["text"] for message in snapshot["messages"]] == ["作業スロット"]

    state.append_message(second_id, " 続き")
    state.finish_message(second_id)
    state.set_agent("調査", "antigravity")
    snapshot = state.snapshot()
    assert [message["text"] for message in snapshot["messages"]] == ["調査スロット 続き"]
    assert snapshot["messages"][0]["streaming"] is False
    assert first_id != second_id


def test_dashboard_state_streams_message_to_inactive_slot():
    """選択状態を変えず指定スロットの応答を逐次更新できる。"""
    state = DashboardState()
    state.set_agent("作業", "codex")
    message_id = state.add_message_to_slot(
        "調査",
        "claude",
        "assistant",
        "",
        streaming=True,
    )

    state.append_message(message_id, "回答")
    assert state.slot_messages("調査", "claude")[0]["text"] == "回答"
    assert state.slot_messages("調査", "claude")[0]["streaming"] is True


def test_dashboard_state_tracks_slot_unread_and_busy():
    """スロットごとの未読と処理中状態を保持する。"""
    state = DashboardState()
    state.set_slots([("作業", "codex", "gpt-test"), ("調査", "antigravity", "Gemini Test")])
    state.set_agent("作業", "codex")
    state.set_slot_busy("調査", "antigravity", True)
    state.set_slot_unread("調査", "antigravity", True)

    snapshot = state.snapshot()
    slots = {slot["name"]: slot for slot in snapshot["slots"]}

    assert slots["作業"]["active"] is True
    assert slots["調査"]["busy"] is True
    assert slots["調査"]["unread"] is True
    assert slots["作業"]["model"] == "gpt-test"

    state.set_agent("調査", "antigravity")
    slots = {slot["name"]: slot for slot in state.snapshot()["slots"]}
    assert slots["調査"]["active"] is True
    assert slots["調査"]["unread"] is False


def test_dashboard_state_deduplicates_consecutive_internal_errors():
    """同一の内部エラーが連続しても通知を増やさない。"""
    state = DashboardState()

    first_id = state.add_error_notification("VOICEVOX", "接続できません")
    second_id = state.add_error_notification("VOICEVOX", "接続できません")

    snapshot = state.snapshot()
    assert first_id == second_id
    assert len(snapshot["notifications"]) == 1
    assert snapshot["notifications"][0]["priority"] == "high"


def test_apply_event_supports_messages_status_and_clear():
    """外部APIから会話、状態、通知削除を更新できる。"""
    state = DashboardState()

    _apply_event(state, {"type": "user_message", "text": "ユーザー発話"})
    _apply_event(state, {"type": "agent_message", "text": "ARGOS返答"})
    _apply_event(state, {"type": "status", "code": "thinking", "label": "考え中"})
    state.add_notification("通知", "本文")
    _apply_event(state, {"type": "clear_notifications"})

    snapshot = state.snapshot()
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["status"]["code"] == "thinking"
    assert snapshot["notifications"] == []


def test_apply_event_rejects_unknown_type():
    """未対応イベントはエラーにする。"""
    with pytest.raises(ValueError, match="未対応"):
        _apply_event(DashboardState(), {"type": "unknown"})


def test_dashboard_server_serves_html_snapshot_and_authenticated_events(tmp_path):
    """画面配信とBearer認証付きイベントAPIを利用できる。"""
    state = DashboardState()
    snapshot_path = tmp_path / "camera-latest.jpg"
    snapshot_path.write_bytes(b"jpeg-data")
    server = DashboardServer(
        state, "127.0.0.1", 0, "secret", camera_snapshot_path=snapshot_path, screensaver_seconds=12.5, default_font_size="small"
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with urlopen(base_url + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "ARGOS Dashboard" in html
        assert "cursor: none" in html
        assert "cursor: none !important" in html
        assert "nextNotifications !== previousNotifications" in html
        assert "previousMessages = \"\";" in html
        assert "previousNotifications = \"\";" in html
        assert "renderSlots(state);" in html
        assert "touch-action: pan-y" in html
        assert "followLatestMessage" in html
        assert "const visibleMessages = state.messages;" in html
        assert "state.notifications.slice().reverse()" in html
        assert "state.notifications.slice(-4)" not in html
        assert ".notifications::-webkit-scrollbar" in html
        assert "id=\"splash\"" in html
        assert "showSplash()" in html
        assert 'pageQuery.get("from")' in html
        assert 'window.history.replaceState({}, "", window.location.pathname)' in html
        assert "if (skipInitialSplash)" in html
        assert 'data-code="booting"' in html
        assert 'stream.addEventListener("open", refresh)' in html
        assert 'data-code="locked"' in html
        assert 'data-code="alert"' in html
        assert 'body[data-status-code="listening"]::before' in html
        assert 'body[data-status-code="transcribing"]::before' in html
        assert 'body[data-status-code="transcribing"]::after' in html
        assert 'body[data-status-code="thinking"]::after' in html
        assert ".status[data-code=\"transcribing\"] .status-dot" in html
        assert "status-frame-flow" in html
        assert 'document.body.dataset.statusCode = displayStatus.code || "ready";' in html
        assert "CURRENT SLOT" in html
        assert 'id="agent-name"' in html
        assert 'id="agent-usage"' in html
        assert "renderAgentUsage(state.agent_usage?.current)" in html
        assert "formatUsageBucket(bucket)" in html
        assert 'id="agent-selector"' in html
        assert 'id="slot-menu"' in html
        assert 'agentSelector.addEventListener("click"' in html
        assert 'slotMenu.addEventListener("click"' in html
        assert 'id="session-compact-button"' in html
        assert "state.capabilities?.conversation_memory" in html
        assert "availableSlots = state.slots || []" in html
        assert "state.agent?.provider" in html
        assert "state.agent?.model" in html
        assert 'class="brand-row"' in html
        assert 'class="brand-controls"' in html
        assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in html
        assert "@media (min-width: 761px) and (max-width: 900px)" in html
        assert ".notifications-panel { grid-column: auto; min-height: 0; }" in html
        assert '.network-status[data-connected="false"]' in html
        assert "display: none;" in html
        assert 'id="mute-button"' in html
        assert 'id="microphone-button"' in html
        assert 'id="volume-slider"' in html
        assert 'aria-label="読み上げ音量"' in html
        assert 'sendControl("set_volume", {volume})' in html
        assert 'id="session-reset-button"' in html
        assert "セッションリセット" in html
        assert "もう一度で実行" in html
        assert 'sendControl("reset_agent_session")' in html
        assert 'aria-label="フォントサイズ"' in html
        assert 'data-font-size-option="small"' in html
        assert 'data-font-size-option="medium"' in html
        assert 'data-font-size-option="large"' in html
        assert 'const fontSizeStorageKey = "argos-dashboard-font-size";' in html
        assert "const defaultFontSize = \"small\";" in html
        assert "applyFontSize(localStorage.getItem(fontSizeStorageKey) || defaultFontSize)" in html
        assert '--message-font-size: 14px;' in html
        assert '--notice-title-font-size: 13px;' in html
        assert 'body[data-font-size="large"]' in html
        assert ">ミュート</button>" in html
        assert ">マイクOFF</button>" in html
        assert 'sendControl(microphoneEnabled ? "disable_microphone" : "enable_microphone")' in html
        assert "applyControlResult(await response.json())" in html
        assert 'muted ? "ミュート中" : "ミュート"' in html
        assert "border-radius: 8px" in html
        assert "opacity: 0.72" in html
        assert "const dashboardToken = \"secret\";" in html
        assert "const screensaverTimeoutMs = Math.max(0, Number(12.5) * 1000);" in html
        assert 'id="screensaver"' in html
        assert "resetScreensaver()" in html
        assert 'const activeStates = new Set(["listening", "transcribing", "thinking", "speaking", "authenticating", "auth_listening", "followup"]);' in html
        assert '"locked"]);' not in html
        assert "state.display_activity?.sequence" in html
        assert "showScreensaver" in html
        assert '"pointermove"' not in html
        assert 'data-code="muted"' in html
        assert 'id="slot-center"' in html
        assert 'id="slot-right"' in html
        assert 'id="iframe-center"' in html
        assert 'id="iframe-right"' in html
        assert 'id="swap-button"' in html
        assert 'sendEvent("clear_overlay", { target_slot: slot })' in html
        assert 'fetch("/api/terminal/slots/select"' in html
        assert 'selectAgentSlot(selected.name || "", selected.provider || "")' in html
        assert 'id="text-composer"' in html
        assert 'id="text-input"' in html
        assert '"Content-Type": "text/plain; charset=utf-8"' in html
        assert '"Accept": "text/event-stream"' in html
        assert 'fetch("/api/terminal/turn"' in html
        assert 'id="voice-input-button"' in html
        assert '"audio/mp4;codecs=mp4a.40.2"' in html
        assert "navigator.mediaDevices.getUserMedia" in html
        assert '\"Accept\": \"text/event-stream, audio/wav\"' in html
        assert "consumeVoiceTurnEvents(response.body, playbackGeneration)" in html
        assert "const inFlightTextSlots = new Map();" in html
        assert "isSlotTurnInFlight(currentAgentSlotKey)" in html
        assert "textInput.disabled = false;" in html
        # 送信が母艦へ届かなかったときに理由を画面へ出す（端末のエラー表示に合わせる）。
        assert 'id="text-composer-error"' in html
        assert "setTextComposerError(describeTurnSendError(error));" in html
        assert '"母艦に繋がらないみたい"' in html
        assert "await describeTurnResponseError(response)" in html
        assert "let pttActiveKey = null;" in html
        assert 'const isPttKey = event.code === "Space"' in html
        assert 'setVoiceState("recording");' in html
        assert 'voiceState === "recording"' in html
        assert 'statusLabel.textContent = "録音中";' in html
        assert 'if (!currentAgentSlotKey) return;' in html
        assert 'voiceState !== "recording" && currentAgentSlotKey' in html
        assert 'async function startVoiceRecording(isPushToTalk = false)' in html
        assert 'async function stopVoiceRecording(isPushToTalk = false)' in html
        assert 'let pttReleasePending = false;' in html
        assert 'sendControl("cancel_audio")' in html
        assert "new ArgosBrowserAudio.AudioPlayer" in html
        assert "voicePlayer.cancel()" in html
        assert "voicePlayer.enqueueBase64(base64Data, playbackGeneration)" in html
        assert "await voicePlaybackChain" not in html
        assert 'recording: {code: "listening", label: "録音中"}' in html
        assert 'playing: {code: "speaking", label: "回答を再生中"}' in html
        assert '<script src="/static/browser_audio.js"></script>' in html
        assert 'data-layout="standard"' in html

        with urlopen(base_url + "/sp", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
            sp_html = response.read().decode("utf-8")
        assert 'data-layout="sp"' in sp_html
        assert 'id="sp-status-button"' in sp_html
        assert 'body[data-layout="sp"].sp-left-pinned' in sp_html
        assert 'window.visualViewport?.addEventListener("resize", updateSpViewport)' in sp_html
        assert 'body[data-layout="sp"] .dashboard' in sp_html
        assert 'const spNotificationSeenAtStorageKey = "argos-sp-notification-seen-at";' in sp_html
        assert "if (rightOpen) markSpNotificationsSeen();" in sp_html
        assert "latestNotifications.filter" in sp_html
        assert 'id="layout-switch-grid"' in html
        assert 'data-default-layout-option="grid"' in html

        with urlopen(base_url + "/grid", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
            grid_html = response.read().decode("utf-8")
        assert "ARGOS スロット一覧" in grid_html
        assert "/api/terminal/turn?${query}" in grid_html
        assert 'Accept: "text/event-stream, audio/wav"' in grid_html
        assert '(slot.type || "local") === "local"' in grid_html
        assert 'const dashboardToken = "secret";' in grid_html
        assert "height: 100dvh" in grid_html
        assert "grid-template-rows: auto minmax(0, 1fr)" in grid_html
        assert "grid-auto-rows: minmax(360px, 1fr)" in grid_html
        assert ".messages { min-height: 0; overflow: auto; padding: 6px 8px; }" in grid_html
        assert ".message { width: fit-content; max-width: 92%; margin: 3px 0; padding: 4px 8px;" in grid_html
        assert 'const text = String(message.text ?? "").trim();' in grid_html
        assert '>${escapeHtml(text)}</div>`' in grid_html
        assert '#69b7e4' in grid_html
        assert '#f4d35e0d' in grid_html
        assert 'background: #070b0f' in grid_html
        assert '"#00b140"' in grid_html
        assert '"#ff4d4d"' in grid_html
        assert 'tile.dataset.attention = "true"' in grid_html
        assert 'tile.querySelector(".tile-status").textContent = "回答完了"' in grid_html
        assert 'tile.querySelector(".tile-status").textContent = "実行中"' in grid_html
        assert 'state.status.code !== "ready"' in grid_html
        assert "consumeTurnEvents(response, key, playbackGeneration)" in grid_html
        assert "if (result.done && pending.trim()) processBlock(pending);" in grid_html
        assert "const processBlock = block =>" in grid_html
        assert "tile.tabIndex = 0" in grid_html
        assert 'tile.setAttribute("role", "group")' in grid_html
        assert 'tile.focus({preventScroll: true})' in grid_html
        assert 'class="maximize"' in grid_html
        assert 'title="最大化">⛶</button>' in grid_html
        assert 'class="tile-actions"' in grid_html
        assert 'id="usage-button"' in grid_html
        assert 'id="settings-button"' in grid_html
        assert 'href="/?from=grid&amp;open=settings">⚙️ 設定</a>' in grid_html
        assert "shouldOpenSettings" in html
        assert 'height: 36px' in grid_html
        assert 'id="usage-dialog"' in grid_html
        assert 'latestState?.agent_usage?.providers' in grid_html
        assert 'class="usage-progress-bar"' in grid_html
        assert 'usageBucketMarkup("5時間"' in grid_html
        assert 'usageBucketMarkup("週間"' in grid_html
        assert 'messages.scrollTop = messages.scrollHeight' in grid_html
        assert 'requestAnimationFrame(scrollToLatest)' in grid_html
        assert 'window.setTimeout(scrollToLatest, 100)' in grid_html
        assert 'grid.scrollTop = gridScroll.top' in grid_html
        assert 'event.type === "dblclick"' not in grid_html
        assert 'tile.addEventListener("dblclick"' in grid_html
        assert 'classList.toggle("is-maximized")' in grid_html
        assert 'event.key !== "Escape"' in grid_html
        assert 'id="mute-button"' in grid_html
        assert 'event.code === "Space"' in grid_html
        assert 'const status = slotElements.get(key)?.querySelector(".tile-status");' in grid_html
        assert 'if (status) status.textContent = "録音中";' in grid_html
        assert 'if (isPushToTalk && pttReleasePending)' in grid_html
        assert '/^F(1[3-9]|2[0-4])$/' in grid_html
        assert "playPendingAudio(key)" in grid_html
        assert 'event.event === "audio" && event.data' in grid_html
        assert 'localStorage.setItem("argos-grid-muted"' in grid_html
        assert 'action: "cancel_audio"' in grid_html
        assert "function cancelGridAudio()" in grid_html
        assert "new ArgosBrowserAudio.AudioPlayer" in grid_html
        assert "audioPlayer.enqueueBase64(base64Data, playbackGeneration)" in grid_html
        assert '<script src="/static/browser_audio.js"></script>' in grid_html
        assert 'recordingSlotKey === key && recorder?.state === "recording"' in grid_html
        assert '!element.classList.contains("voice")' in grid_html
        assert 'audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}' in grid_html

        with urlopen(base_url + "/static/browser_audio.js", timeout=2) as response:
            assert response.headers["Content-Type"] == "application/javascript; charset=utf-8"
            browser_audio = response.read().decode("utf-8")
        assert "class AudioPlayer" in browser_audio
        assert "function parsePcm16Wav" in browser_audio

        with urlopen(base_url + "/camera/latest.jpg", timeout=2) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.read() == b"jpeg-data"

        status, body = _read_json(base_url + "/api/events", "POST", {"type": "notification", "title": "テスト"}, "secret")
        assert status == 201
        assert body["id"]

        status, body = _read_json(base_url + "/api/state")
        assert status == 200
        assert body["notifications"][0]["title"] == "テスト"
    finally:
        server.stop()


def test_dashboard_control_api_calls_handler():
    """ダッシュボード操作APIはBearer認証後にハンドラーを呼ぶ。"""
    calls = []

    def handle_control(payload):
        calls.append(payload)
        return {"muted": payload["action"] == "mute", "volume": payload.get("volume", 90)}

    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret", control_handler=handle_control)
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/control"
    try:
        status, body = _read_json(url, "POST", {"action": "mute", "volume": 55}, "secret")
    finally:
        server.stop()

    assert status == 200
    assert body == {"muted": True, "volume": 55}
    assert calls == [{"action": "mute", "volume": 55}]


def test_dashboard_settings_page_and_authenticated_config_api(tmp_path, monkeypatch):
    """設定画面を配信し、認証付きAPIでYAMLを更新できる。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("audio:\n  output_volume: 70\n", encoding="utf-8")
    monkeypatch.setattr(
        dashboard_server,
        "list_audio_devices",
        lambda: {"inputs": [{"value": "mic", "label": "USBマイク"}], "outputs": []},
    )
    server = DashboardServer(
        DashboardState(),
        "127.0.0.1",
        0,
        "secret",
        config_path=config_path,
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with urlopen(base_url + "/settings", timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "ARGOS 設定" in html
        assert 'const dashboardToken = "secret";' in html
        assert "config.yamlの全項目" in html
        assert 'href="/?from=settings"' in html
        assert "設定名や説明を検索" in html
        assert "test-microphone" in html
        assert 'document.addEventListener("pointerdown"' in html
        assert 'target.closest("input, select, textarea, button, a, label, summary")' in html
        assert 'window.scrollBy({top: deltaY, left: 0, behavior: "auto"})' in html
        assert "body.drag-scrolling" in html
        assert "-webkit-user-select: none" in html
        assert "input, textarea { user-select: text" in html
        assert "startInertiaScroll(finished.velocity)" in html
        assert "requestAnimationFrame(step)" in html
        assert "Math.min(2.4, initialVelocity)" in html
        assert "stopInertiaScroll()" in html

        with pytest.raises(HTTPError) as error:
            _read_json(base_url + "/api/config")
        assert error.value.code == 401

        status, body = _read_json(base_url + "/api/config", token="secret")
        assert status == 200
        assert any(field["description"] for field in body["fields"])

        status, body = _read_json(base_url + "/api/config/audio-devices", token="secret")
        assert status == 200
        assert body["inputs"][0]["label"] == "USBマイク"

        status, body = _read_json(
            base_url + "/api/config",
            "PUT",
            {"values": {"audio.output_volume": 45}},
            "secret",
        )
        assert status == 200
        assert body["saved"] is True
        assert body["restart_required"] is True
    finally:
        server.stop()

    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["audio"]["output_volume"] == 45


def test_dashboard_event_api_calls_handler():
    """外部イベントAPIは状態更新後にイベントハンドラーを呼ぶ。"""
    calls = []

    def handle_event(payload, response):
        calls.append((payload, response))

    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret", event_handler=handle_event)
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/events"
    try:
        status, body = _read_json(url, "POST", {"type": "notification", "title": "予定", "speak": True}, "secret")
    finally:
        server.stop()

    assert status == 201
    assert body["id"]
    assert calls == [({"type": "notification", "title": "予定", "speak": True}, body)]


def test_dashboard_server_rejects_invalid_token():
    """外部イベントAPIはBearer認証なしで更新できない。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret")
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/events"
    try:
        try:
            _read_json(url, "POST", {"type": "notification", "title": "テスト"})
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("認証エラーになりませんでした")
    finally:
        server.stop()


def test_dashboard_state_supports_overlay():
    """オーバーレイの表示状態を設定・消去できる。"""
    state = DashboardState()

    # 初期状態
    snapshot = state.snapshot()
    assert snapshot["overlay"]["active"] is False
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "conversation"
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "notifications"

    # 設定 (後方互換の set_overlay を経由)
    state.set_overlay(
        overlay_type="map",
        title="周辺地図",
        content="地図テスト",
        url="http://127.0.0.1/map",
        options={"lat": 35.6, "lng": 139.6}
    )
    snapshot = state.snapshot()
    assert snapshot["overlay"]["active"] is True
    assert snapshot["overlay"]["type"] == "map"
    assert snapshot["overlay"]["title"] == "周辺地図"
    assert snapshot["overlay"]["content"] == "地図テスト"
    assert snapshot["overlay"]["url"] == "http://127.0.0.1/map"
    assert snapshot["overlay"]["options"] == {"lat": 35.6, "lng": 139.6}
    # 後方互換で right スロットに積まれていること
    assert len(snapshot["slot_stacks"]["right"]) == 2
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "map"

    # 消去 (後方互換の clear_overlay を経由)
    state.clear_overlay()
    snapshot = state.snapshot()
    assert snapshot["overlay"]["active"] is False
    assert len(snapshot["slot_stacks"]["right"]) == 1

    # 新仕様スロットスタックの直接テスト
    state.push_overlay("center", "map", "中央地図", url="http://map")
    snapshot = state.snapshot()
    assert len(snapshot["slot_stacks"]["center"]) == 2
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "map"
    assert snapshot["slot_stacks"]["center"][-1]["title"] == "中央地図"

    # スロット入れ替え
    state.swap_slots()
    snapshot = state.snapshot()
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "notifications"
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "map"
    assert snapshot["slot_stacks"]["right"][-1]["title"] == "中央地図"

    # スロットPop
    state.pop_overlay("right")
    snapshot = state.snapshot()
    assert len(snapshot["slot_stacks"]["right"]) == 1
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "conversation"  # swapしたので底はconversation


def test_apply_event_supports_overlay():
    """外部イベントAPIを介してオーバーレイの表示・消去イベントを適用できる。"""
    state = DashboardState()

    # overlayイベントの適用 (target_slot 省略時は right)
    _apply_event(state, {
        "type": "overlay",
        "overlay_type": "markdown",
        "title": "タスクリスト",
        "content": "# タスクリスト\n- [ ] 開発",
        "url": "/static/reader.html",
        "options": {"foo": "bar"}
    })
    snapshot = state.snapshot()
    assert snapshot["overlay"]["active"] is True
    assert snapshot["overlay"]["type"] == "markdown"
    assert snapshot["overlay"]["title"] == "タスクリスト"
    assert snapshot["overlay"]["content"] == "# タスクリスト\n- [ ] 開発"
    assert snapshot["overlay"]["url"] == "/static/reader.html"
    assert snapshot["overlay"]["options"] == {"foo": "bar"}
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "markdown"

    # target_slot="center" での overlay イベントの適用
    _apply_event(state, {
        "type": "overlay",
        "target_slot": "center",
        "overlay_type": "map",
        "title": "中央地図",
        "url": "http://map"
    })
    snapshot = state.snapshot()
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "map"

    # replace_top 付きの overlay イベントは同じスロットの最前面を差し替える
    _apply_event(state, {
        "type": "overlay",
        "target_slot": "center",
        "overlay_type": "nav",
        "title": "ナビ",
        "url": "/static/nav.html?zoom=14",
        "replace_top": True,
    })
    snapshot = state.snapshot()
    assert len(snapshot["slot_stacks"]["center"]) == 2
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "nav"
    assert snapshot["slot_stacks"]["center"][-1]["url"] == "/static/nav.html?zoom=14"

    # swap_slots イベントの適用
    _apply_event(state, {"type": "swap_slots"})
    snapshot = state.snapshot()
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "markdown"
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "nav"

    # clear_overlay イベントの適用 (centerスロットをpop)
    _apply_event(state, {"type": "clear_overlay", "target_slot": "center"})
    snapshot = state.snapshot()
    # centerは notifications が pop できないのでそのまま
    assert snapshot["slot_stacks"]["center"][-1]["type"] == "notifications"

    # clear_overlay イベントの適用 (rightスロットをpop)
    _apply_event(state, {"type": "clear_overlay", "target_slot": "right"})
    snapshot = state.snapshot()
    assert snapshot["slot_stacks"]["right"][-1]["type"] == "conversation"  # 地図が消えてデフォルトに戻る
    assert snapshot["overlay"]["active"] is False


def test_dashboard_server_serves_static_files():
    """/static/* 経由でパッケージ内の静的ファイルを返せる。"""
    state = DashboardState()
    server = DashboardServer(state, "127.0.0.1", 0, "secret")
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        # reader.html を取得してみる
        with urlopen(base_url + "/static/reader.html", timeout=2) as response:
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            html = response.read().decode("utf-8")
            assert "Markdown Reader" in html

        # map.html を取得してみる
        with urlopen(base_url + "/static/map.html", timeout=2) as response:
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            html = response.read().decode("utf-8")
            assert "Map Viewer" in html
            assert "current-location-marker" in html
            assert "destination-marker" in html
            assert "bindTooltip" in html
            assert "labelMode" in html
            assert "label_mode" in html

        # nav.html を取得してみる
        with urlopen(base_url + "/static/nav.html", timeout=2) as response:
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            html = response.read().decode("utf-8")
            assert "Navigation Map" in html
            assert "現在地追従中" in html
            assert 'params.get("orientation")' in html
            assert "進行方向" in html
            assert "/api/location" in html

        # 存在しないファイルは404
        with pytest.raises(HTTPError) as exc:
            urlopen(base_url + "/static/nonexistent.html", timeout=2)
        assert exc.value.code == 404

        # ディレクトリトラバーサルのガードテスト
        with pytest.raises(HTTPError) as exc:
            urlopen(base_url + "/static/../server.py", timeout=2)
        assert exc.value.code == 403
    finally:
        server.stop()


def test_parse_nmea_location_from_rmc():
    """RMC文から現在地、速度、進行方向を取り出せる。"""
    location = parse_nmea_location("$GPRMC,054543.20,A,3713.69073,N,13950.85569,E,26.529,242.71,120626,,,D*57")

    assert location is not None
    assert location["available"] is True
    assert location["lat"] == pytest.approx(37.2281788)
    assert location["lng"] == pytest.approx(139.8475948)
    assert location["speed_kmh"] == 49.1
    assert location["course"] == 242.71


def test_parse_gpsd_tpv():
    """gpsdのTPV JSONから現在地を取り出せる。"""
    location = parse_gpsd_tpv(
        '{"class":"TPV","mode":3,"lat":37.110871441,"lon":139.720415378,"speed":0.029,"track":347.2306}'
    )

    assert location is not None
    assert location["available"] is True
    assert location["lat"] == pytest.approx(37.110871441)
    assert location["lng"] == pytest.approx(139.720415378)
    assert location["speed_kmh"] == 0.1
    assert location["course"] == 347.2306


def test_parse_remote_location_from_car_logger_latest():
    """カーロガーの /api/latest レスポンスを現在地形式へ変換できる。"""
    location = parse_remote_location(
        {
            "point": {
                "lat": 37.110871441,
                "lon": 139.720415378,
                "speed_kmh": 12.3,
                "course": 242.7,
                "recorded_at": "2026-06-12T12:34:56+09:00",
            }
        }
    )

    assert location["available"] is True
    assert location["lat"] == pytest.approx(37.110871441)
    assert location["lng"] == pytest.approx(139.720415378)
    assert location["speed_kmh"] == 12.3
    assert location["course"] == 242.7
    assert location["updated_at"] == "2026-06-12T12:34:56+09:00"
    assert location["source"] == "remote"


def test_parse_remote_location_from_car_logger_gps():
    """カーロガーの /gps レスポンスを現在地形式へ変換できる。"""
    location = parse_remote_location(
        {
            "has_fix": True,
            "lat": 35.681236,
            "lon": 139.767125,
            "speed_kmh": 0.0,
            "last_fix_at": "2026-06-12T22:09:08+00:00",
        }
    )

    assert location["available"] is True
    assert location["lat"] == pytest.approx(35.681236)
    assert location["lng"] == pytest.approx(139.767125)
    assert location["speed_kmh"] == 0.0
    assert location["updated_at"] == "2026-06-12T22:09:08+00:00"
    assert location["source"] == "remote"
    assert location["has_fix"] is True


def test_dashboard_server_serves_location(monkeypatch, tmp_path):
    """GPSデバイス相当のNMEAファイルから現在地APIを返せる。"""
    monkeypatch.setattr(dashboard_location, "read_gpsd_location", lambda timeout_seconds=1.2: {"available": False, "error": "gpsdなし"})
    gps_file = tmp_path / "gps.nmea"
    gps_file.write_text(
        "$GPRMC,054543.20,A,3713.69073,N,13950.85569,E,26.529,242.71,120626,,,D*57\n",
        encoding="ascii",
    )
    state = DashboardState()
    server = DashboardServer(state, "127.0.0.1", 0, "secret", gps_device_path=gps_file)
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        _, payload = _read_json(base_url + "/api/location")
    finally:
        server.stop()

    assert payload["available"] is True
    assert payload["lat"] == pytest.approx(37.2281788)
    assert payload["lng"] == pytest.approx(139.8475948)


def test_dashboard_server_serves_remote_location(monkeypatch):
    """リモートGPS設定時は指定URLから現在地を返す。"""
    calls = []

    def fake_read_location(provider, device_path, remote_url, timeout_seconds, remote_token=""):
        calls.append((provider, remote_url, timeout_seconds, remote_token))
        return {
            "available": True,
            "lat": 35.0,
            "lng": 139.0,
            "updated_at": "2026-06-12T12:34:56+09:00",
        }

    monkeypatch.setattr(dashboard_server, "read_location", fake_read_location)
    state = DashboardState()
    server = DashboardServer(
        state,
        "127.0.0.1",
        0,
        "secret",
        location_provider="remote",
        remote_location_url="http://example.test/gps",
        remote_location_timeout_seconds=1.5,
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        _, payload = _read_json(base_url + "/api/location")
    finally:
        server.stop()

    assert payload["available"] is True
    assert payload["lat"] == 35.0
    assert calls == [("remote", "http://example.test/gps", 1.5, "")]


def test_dashboard_state_center_alert_for_center_display():
    """display=center の通知は中央アラート状態を更新し、clearで消える。"""
    state = DashboardState()
    state.add_notification("ご飯だよ", "食卓へどうぞ", source="kitchen", display="center", duration_seconds=30)

    snapshot = state.snapshot()
    assert snapshot["center_alert"]["active"] is True
    assert snapshot["center_alert"]["title"] == "ご飯だよ"
    assert snapshot["center_alert"]["duration_seconds"] == 30
    # 通知履歴にも display / duration_seconds が入る
    assert snapshot["notifications"][0]["display"] == "center"

    state.clear_center_alert()
    assert state.snapshot()["center_alert"]["active"] is False


def test_dashboard_state_toast_display_keeps_center_alert_inactive():
    """既定の toast 通知では中央アラートは表示されない。"""
    state = DashboardState()
    state.add_notification("メール", "新着があります", source="mail")

    snapshot = state.snapshot()
    assert snapshot["center_alert"]["active"] is False
    assert snapshot["notifications"][0]["display"] == "toast"


def test_apply_event_notification_supports_center_and_clear():
    """通知イベントの display=center と clear_center_alert を扱える。"""
    state = DashboardState()
    _apply_event(state, {
        "type": "notification",
        "title": "全員集合",
        "text": "リビングへ",
        "display": "center",
        "duration_seconds": 15,
        "target": "living",  # 宛先ラベルは受理して無視する
    })
    assert state.snapshot()["center_alert"]["active"] is True

    _apply_event(state, {"type": "clear_center_alert"})
    assert state.snapshot()["center_alert"]["active"] is False


def test_apply_event_notification_rejects_invalid_display_and_duration():
    """不正な display や負の duration_seconds は拒否する。"""
    state = DashboardState()
    with pytest.raises(ValueError):
        _apply_event(state, {"type": "notification", "title": "x", "display": "banner"})
    with pytest.raises(ValueError):
        _apply_event(state, {"type": "notification", "title": "x", "duration_seconds": -1})
    with pytest.raises(ValueError):
        _apply_event(state, {"type": "notification", "title": "x", "duration_seconds": "soon"})


def test_dashboard_upload_stores_and_serves_image(tmp_path):
    """通知画像をアップロードして保存・配信できる。"""
    upload_dir = tmp_path / "uploads"
    server = DashboardServer(
        DashboardState(), "127.0.0.1", 0, "secret", upload_dir=upload_dir, upload_keep=2
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        request = Request(
            base_url + "/api/uploads",
            data=b"\xff\xd8\xff\xd9",
            headers={"Content-Type": "image/jpeg", "Authorization": "Bearer secret"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 201
            url = json.loads(response.read())["url"]
        assert url.startswith("/uploads/") and url.endswith(".jpg")

        with urlopen(base_url + url, timeout=2) as response:
            assert response.status == 200
            assert response.read() == b"\xff\xd8\xff\xd9"

        # パストラバーサルは拒否する
        try:
            urlopen(base_url + "/uploads/..%2Fsecret", timeout=2)
        except HTTPError as exc:
            assert exc.code in (403, 404)
        else:
            raise AssertionError("パストラバーサルを拒否しませんでした")
    finally:
        server.stop()


def test_dashboard_upload_requires_token_and_valid_type(tmp_path):
    """アップロードはBearer認証と画像MIMEを必須とする。"""
    server = DashboardServer(
        DashboardState(), "127.0.0.1", 0, "secret", upload_dir=tmp_path / "uploads"
    )
    server.start()
    url = f"http://{server.address[0]}:{server.address[1]}/api/uploads"
    try:
        # 認証なし
        request = Request(url, data=b"x", headers={"Content-Type": "image/png"}, method="POST")
        try:
            urlopen(request, timeout=2)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("認証エラーになりませんでした")

        # 非対応のContent-Type
        request = Request(
            url,
            data=b"x",
            headers={"Content-Type": "text/plain", "Authorization": "Bearer secret"},
            method="POST",
        )
        try:
            urlopen(request, timeout=2)
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("不正な形式を拒否しませんでした")
    finally:
        server.stop()


def test_prune_uploads_keeps_newest(tmp_path):
    """アップロード画像は新しい順に keep 件だけ残す。"""
    for index in range(4):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(b"data")
        os.utime(path, (index, index))
    dashboard_server._prune_uploads(tmp_path, keep=2)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["2.jpg", "3.jpg"]


def test_dashboard_view_key_guards_read_endpoints():
    """閲覧キー設定時は画面・状態・SSEなど閲覧系に認証を要求する。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret", view_key="viewkey")
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        # キーなしはページも状態APIも拒否する
        for path in ("/", "/api/state"):
            try:
                urlopen(base_url + path, timeout=2)
            except HTTPError as exc:
                assert exc.code == 401
            else:
                raise AssertionError(f"{path} が認証なしで閲覧できました")

        # 誤ったキーも拒否する
        try:
            urlopen(base_url + "/?key=wrong", timeout=2)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("誤ったキーで閲覧できました")

        # ヘルスチェックは常に開放する
        status, _ = _read_json(base_url + "/api/health")
        assert status == 200

        # 正しいキー付きはHTMLとCookieを返す
        with urlopen(base_url + "/?key=viewkey", timeout=2) as response:
            assert response.status == 200
            cookie_header = response.headers.get("Set-Cookie", "")
            assert "argos_view_key=viewkey" in cookie_header
            assert "HttpOnly" in cookie_header
            assert "ARGOS Dashboard" in response.read().decode("utf-8")

        # 発行済みCookieだけで状態APIを読める
        request = Request(base_url + "/api/state", headers={"Cookie": "argos_view_key=viewkey"})
        with urlopen(request, timeout=2) as response:
            assert response.status == 200

        # Bearerトークンでも閲覧できる（スクリプト用）
        status, _ = _read_json(base_url + "/api/state", token="secret")
        assert status == 200
    finally:
        server.stop()


def test_dashboard_without_view_key_stays_open():
    """閲覧キー未設定なら従来通り認証なしで閲覧できる。"""
    server = DashboardServer(DashboardState(), "127.0.0.1", 0, "secret")
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with urlopen(base_url + "/", timeout=2) as response:
            assert response.status == 200
            assert response.headers.get("Set-Cookie") is None
        status, _ = _read_json(base_url + "/api/state")
        assert status == 200
    finally:
        server.stop()
