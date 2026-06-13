from __future__ import annotations

import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import Thread

import requests

from argos.config import AgentSlot, Settings
from argos.services.agent.runner import AgentJobStore, AgentRunner, AgentRunnerServer
from argos.services.agent.runner_client import RunnerAgentClient


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
