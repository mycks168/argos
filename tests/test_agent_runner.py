from __future__ import annotations

import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import Event, Thread

import requests

from argos.config import AgentSlot, Settings
from argos.services.agent.runner import AgentJobStore, AgentRunner, AgentRunnerServer, AgentSlotBusyError
from argos.services.agent.runner_client import RunnerAgentClient, RunnerSlotBusyError


def _settings(tmp_path: Path) -> Settings:
    """Runnerテスト用の最小設定を返す。"""
    return Settings(
        agent_provider="codex",
        agent_state_path=str(tmp_path / "agent-sessions.json"),
        agent_slots=(AgentSlot("作業", "codex", str(tmp_path)),),
        stt_gateway_url="",
        stt_language="ja",
        stt_gateway_token="",
        tts_filter_url="",
        tts_filter_token="",
        tts_delimiters="。！？!?",
        voicevox_url="",
        voicevox_speaker=2,
        voicevox_sample_rate=48000,
        voicevox_speed_scale=1.0,
        audio_input_device="in",
        audio_output_device="out",
        audio_output_card="",
        audio_output_volume=90,
        audio_sample_rate=16000,
        lcd_enabled=False,
        lcd_width=76,
        lcd_height=284,
        lcd_x_offset=82,
        lcd_y_offset=18,
        lcd_dc_pin="D25",
        lcd_cs_pin="D5",
        lcd_reset_pin="D24",
        lcd_baudrate=4_000_000,
        lcd_font_path="",
        lcd_font_size=16,
        dashboard_enabled=False,
        dashboard_host="127.0.0.1",
        dashboard_port=8765,
        dashboard_token="",
        ptt_gpio=17,
        silence_rms_threshold=200,
        dry_run=True,
        codex_home="",
        codex_model="",
        codex_sandbox="workspace-write",
        codex_bypass_sandbox=False,
        codex_approval_policy="on-request",
        codex_extra_args=(),
        antigravity_command="agy",
        antigravity_home=str(tmp_path / "ag"),
        antigravity_extra_args=(),
        agent_runner_url="http://127.0.0.1:28765",
        agent_runner_token="token",
        agent_runner_state_dir=str(tmp_path / "runner"),
    )


class FakeAgentClient:
    """Runnerテスト用の偽エージェント。"""

    reset_count = 0

    @property
    def current_name(self) -> str:
        """現在スロット名を返す。"""
        return "作業"

    @property
    def current_provider(self) -> str:
        """現在providerを返す。"""
        return "codex"

    def next_slot(self) -> str:
        """次スロットへ切り替える。"""
        return "作業"

    def reset_current(self) -> None:
        """リセット回数を記録する。"""
        FakeAgentClient.reset_count += 1

    def ask(self, prompt: str) -> str:
        """応答を返す。"""
        return "".join(self.ask_stream(prompt))

    def ask_stream(self, prompt: str):
        """応答チャンクを返す。"""
        yield f"応答:{prompt}"


def test_agent_runner_persists_completed_job(tmp_path):
    """Runnerがジョブ結果と配信状態を保存できる。"""
    store = AgentJobStore(tmp_path / "runner")
    runner = AgentRunner(_settings(tmp_path), store, client_factory=lambda _settings, _slot: FakeAgentClient())

    job = runner.start_job("作業", "codex", "こんにちは")
    for _ in range(20):
        current = runner.get_job(job.job_id)
        if current and current.status == "completed":
            break
        time.sleep(0.05)

    current = runner.get_job(job.job_id)
    assert current is not None
    assert current.status == "completed"
    assert Path(current.result_path).read_text(encoding="utf-8") == "応答:こんにちは"
    assert runner.list_undelivered()[0].job_id == job.job_id

    delivered = runner.mark_delivered(job.job_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert runner.list_undelivered() == []


def test_agent_runner_rejects_new_job_while_same_slot_is_busy(tmp_path, monkeypatch):
    """同一スロットで実行中のジョブがある間は新規ジョブを競合として拒否する。"""
    store = AgentJobStore(tmp_path / "runner")
    started = Event()
    release = Event()

    class SlowAgentClient:
        def ask_stream(self, prompt: str):
            started.set()
            release.wait(timeout=5)
            yield "応答"

        def reset_current(self) -> None:
            pass

    runner = AgentRunner(_settings(tmp_path), store, client_factory=lambda _settings, _slot: SlowAgentClient())

    first = runner.start_job("作業", "codex", "1回目")
    started.wait(timeout=5)
    try:
        runner.start_job("作業", "codex", "2回目")
    except AgentSlotBusyError as exc:
        assert exc.job.job_id == first.job_id
    else:
        raise AssertionError("AgentSlotBusyError が発生しませんでした")

    release.set()
    for _ in range(20):
        current = runner.get_job(first.job_id)
        if current and current.status == "completed":
            break
        time.sleep(0.05)
    assert runner.get_job(first.job_id).status == "completed"


def test_agent_runner_marks_interrupted_jobs_failed_on_startup(tmp_path):
    """Runner再起動後は実行スレッドを失った未完了ジョブを失敗扱いにする。"""
    store = AgentJobStore(tmp_path / "runner")
    settings = _settings(tmp_path)
    job = store.create(settings.agent_slots[0], "古い発話")
    job.status = "running"
    store.save(job)

    runner = AgentRunner(settings, store, client_factory=lambda _settings, _slot: FakeAgentClient())

    recovered = runner.get_job(job.job_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert "再起動" in Path(recovered.error_path).read_text(encoding="utf-8")


def test_agent_runner_resets_slot(tmp_path):
    """Runner経由でスロットのセッションリセットを呼べる。"""
    FakeAgentClient.reset_count = 0
    store = AgentJobStore(tmp_path / "runner")
    runner = AgentRunner(_settings(tmp_path), store, client_factory=lambda _settings, _slot: FakeAgentClient())

    runner.reset_slot("作業", "codex")

    assert FakeAgentClient.reset_count == 1


def test_runner_agent_client_polls_until_completed(monkeypatch, tmp_path):
    """ARGOS側Runnerクライアントがジョブ完了までポーリングする。"""
    calls: list[tuple[str, str, dict[str, object]]] = []

    class Response:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        """Runner APIの偽レスポンスを返す。"""
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/api/jobs"):
            return Response({"job_id": "job-1", "status": "queued"})
        if method == "GET" and url.endswith("/api/jobs/job-1"):
            return Response({"job_id": "job-1", "status": "completed", "result": "完了"})
        if method == "POST" and url.endswith("/api/jobs/job-1/deliver"):
            return Response({"job_id": "job-1", "status": "delivered"})
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    client = RunnerAgentClient(_settings(tmp_path))

    assert list(client.ask_stream("やって")) == ["完了"]
    assert calls[0][2]["headers"]["Authorization"] == "Bearer token"
    assert calls[-1][1].endswith("/api/jobs/job-1/deliver")


def test_runner_agent_client_streams_partial_output(monkeypatch, tmp_path):
    """実行中でも逐次flushされたoutputの差分をチャンクとして返す。"""
    calls: list[tuple[str, str]] = []
    states = iter(
        [
            {"job_id": "job-1", "status": "running", "output": "こん"},
            {"job_id": "job-1", "status": "running", "output": "こんにち"},
            {"job_id": "job-1", "status": "completed", "output": "こんにちは", "result": "こんにちは"},
        ]
    )

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = 200
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **_kwargs: object) -> Response:
        """段階的に出力が増えるジョブ状態の偽レスポンスを返す。"""
        calls.append((method, url))
        if method == "POST" and url.endswith("/api/jobs"):
            return Response({"job_id": "job-1", "status": "queued"})
        if method == "GET" and url.endswith("/api/jobs/job-1"):
            return Response(next(states))
        if method == "POST" and url.endswith("/api/jobs/job-1/deliver"):
            return Response({"job_id": "job-1", "status": "delivered"})
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    monkeypatch.setattr("argos.services.agent.runner_client.time.sleep", lambda _seconds: None)
    client = RunnerAgentClient(_settings(tmp_path))

    assert list(client.ask_stream("やって")) == ["こん", "にち", "は"]


def test_runner_agent_client_retries_transient_polling_error(monkeypatch, tmp_path):
    """ポーリングが一時的に失敗しても、ジョブが生きていれば諦めずに継続する。"""
    calls: list[tuple[str, str]] = []
    get_attempts = {"count": 0}

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = 200
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **_kwargs: object) -> Response:
        """GETの最初の2回だけ接続エラーを起こす偽レスポンスを返す。"""
        calls.append((method, url))
        if method == "POST" and url.endswith("/api/jobs"):
            return Response({"job_id": "job-1", "status": "queued"})
        if method == "GET" and url.endswith("/api/jobs/job-1"):
            get_attempts["count"] += 1
            if get_attempts["count"] <= 2:
                raise requests.exceptions.ReadTimeout("read timed out")
            return Response({"job_id": "job-1", "status": "completed", "result": "完了"})
        if method == "POST" and url.endswith("/api/jobs/job-1/deliver"):
            return Response({"job_id": "job-1", "status": "delivered"})
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    monkeypatch.setattr("argos.services.agent.runner_client.time.sleep", lambda _seconds: None)
    client = RunnerAgentClient(_settings(tmp_path))

    assert list(client.ask_stream("やって")) == ["完了"]
    assert get_attempts["count"] == 3


def test_runner_agent_client_reports_busy_slot(monkeypatch, tmp_path):
    """Runnerが409を返した場合は現在処理中であることを例外へ含める。"""

    class Response:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **_kwargs: object) -> Response:
        """ジョブ作成で競合を返す。"""
        if method == "POST" and url.endswith("/api/jobs"):
            return Response({"error": "スロット 作業 は既に応答処理中です"}, status_code=409)
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    client = RunnerAgentClient(_settings(tmp_path))

    try:
        list(client.ask_stream("やって"))
    except RunnerSlotBusyError as exc:
        assert "応答処理中" in str(exc)
    else:
        raise AssertionError("RunnerSlotBusyError が発生しませんでした")


def test_runner_agent_client_handles_failed_job(monkeypatch, tmp_path):
    """Runnerジョブ失敗時は配信済みにして例外を返す。"""
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **_kwargs: object) -> Response:
        """失敗ジョブの偽レスポンスを返す。"""
        calls.append((method, url))
        if method == "POST" and url.endswith("/api/jobs"):
            return Response({"job_id": "job-1", "status": "queued"})
        if method == "GET" and url.endswith("/api/jobs/job-1"):
            return Response({"job_id": "job-1", "status": "failed", "error": "失敗しました"})
        if method == "POST" and url.endswith("/api/jobs/job-1/deliver"):
            return Response({"job_id": "job-1", "status": "failed_delivered"})
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    client = RunnerAgentClient(_settings(tmp_path))

    try:
        list(client.ask_stream("やって"))
    except RuntimeError as exc:
        assert "失敗しました" in str(exc)
    else:
        raise AssertionError("RuntimeError が発生しませんでした")
    assert calls[-1][1].endswith("/api/jobs/job-1/deliver")


def test_runner_agent_client_lists_and_marks_undelivered(monkeypatch, tmp_path):
    """未配信ジョブ一覧の取得と配信済み更新ができる。"""
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **_kwargs: object) -> Response:
        """未配信ジョブAPIの偽レスポンスを返す。"""
        calls.append((method, url))
        if method == "GET" and url.endswith("/api/jobs"):
            return Response({"jobs": [{"job_id": "job-2", "status": "completed"}]})
        if method == "POST" and url.endswith("/api/jobs/job-2/deliver"):
            return Response({"job_id": "job-2", "status": "delivered"})
        raise AssertionError(url)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    client = RunnerAgentClient(_settings(tmp_path))

    assert client.list_undelivered() == [{"job_id": "job-2", "status": "completed"}]
    client.mark_delivered("job-2")

    assert calls[-1][1].endswith("/api/jobs/job-2/deliver")


def test_runner_agent_client_switches_and_resets_current_slot(monkeypatch, tmp_path):
    """Runnerクライアントがスロット切替と現在スロットのリセットを行える。"""
    calls: list[tuple[str, str, dict[str, object]]] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = 200
            self.text = str(payload)

        def json(self) -> dict[str, object]:
            """JSONレスポンスを返す。"""
            return self._payload

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        """スロットリセットAPIの偽レスポンスを返す。"""
        calls.append((method, url, kwargs))
        return Response({"ok": True})

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", fake_request)
    settings = Settings(
        **{
            **_settings(tmp_path).__dict__,
            "agent_slots": (
                AgentSlot("作業", "codex", "/tmp/a"),
                AgentSlot("調査", "hermes", "/tmp/b"),
            ),
        }
    )
    client = RunnerAgentClient(settings)

    assert client.current_name == "作業"
    assert client.current_provider == "codex"
    assert client.next_slot() == "調査"
    assert client.current_provider == "hermes"
    client.reset_current()

    assert calls[-1][1].endswith("/api/slots/reset")
    assert calls[-1][2]["json"] == {"slot_name": "調査", "provider": "hermes"}


def test_runner_agent_client_validates_settings(tmp_path):
    """Runner接続設定の不足を起動時に検出する。"""
    missing_url = Settings(**{**_settings(tmp_path).__dict__, "agent_runner_url": ""})
    try:
        RunnerAgentClient(missing_url)
    except ValueError as exc:
        assert "URL" in str(exc)
    else:
        raise AssertionError("ValueError が発生しませんでした")

    missing_slots = Settings(**{**_settings(tmp_path).__dict__, "agent_slots": ()})
    try:
        RunnerAgentClient(missing_slots)
    except ValueError as exc:
        assert "スロット" in str(exc)
    else:
        raise AssertionError("ValueError が発生しませんでした")


def test_runner_agent_client_rejects_bad_api_response(monkeypatch, tmp_path):
    """Runner APIのHTTPエラーとJSON形式不正を検出する。"""

    class Response:
        def __init__(self, payload: object, status_code: int = 200) -> None:
            """偽HTTPレスポンスを作る。"""
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self) -> object:
            """JSONレスポンスを返す。"""
            return self._payload

    def http_error_request(*_args: object, **_kwargs: object) -> Response:
        """HTTPエラーを返す。"""
        return Response({"error": "bad"}, status_code=500)

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", http_error_request)
    client = RunnerAgentClient(_settings(tmp_path))
    try:
        client.list_undelivered()
    except RuntimeError as exc:
        assert "500" in str(exc)
    else:
        raise AssertionError("RuntimeError が発生しませんでした")

    def list_response_request(*_args: object, **_kwargs: object) -> Response:
        """dictではないJSONを返す。"""
        return Response(["bad"])

    monkeypatch.setattr("argos.services.agent.runner_client.requests.request", list_response_request)
    try:
        client.list_undelivered()
    except RuntimeError as exc:
        assert "レスポンス形式" in str(exc)
    else:
        raise AssertionError("RuntimeError が発生しませんでした")


def test_agent_runner_server_handles_job_lifecycle(tmp_path):
    """Runner HTTP APIでジョブ作成、取得、配信済み更新ができる。"""
    store = AgentJobStore(tmp_path / "runner")
    runner = AgentRunner(_settings(tmp_path), store, client_factory=lambda _settings, _slot: FakeAgentClient())
    api = AgentRunnerServer(runner, token="token")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api._build_handler())
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    headers = {"Authorization": "Bearer token"}
    try:
        unauthorized = requests.get(f"{base_url}/api/jobs", timeout=2)
        assert unauthorized.status_code == 401

        created = requests.post(
            f"{base_url}/api/jobs",
            json={"slot_name": "作業", "provider": "codex", "prompt": "こんにちは"},
            headers=headers,
            timeout=2,
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]

        for _ in range(20):
            current = requests.get(f"{base_url}/api/jobs/{job_id}", headers=headers, timeout=2)
            if current.json()["status"] == "completed":
                break
            time.sleep(0.05)

        current_payload = current.json()
        assert current_payload["result"] == "応答:こんにちは"

        pending = requests.get(f"{base_url}/api/jobs", headers=headers, timeout=2)
        assert pending.json()["jobs"][0]["job_id"] == job_id

        delivered = requests.post(f"{base_url}/api/jobs/{job_id}/deliver", headers=headers, timeout=2)
        assert delivered.status_code == 200
        assert delivered.json()["status"] == "delivered"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
