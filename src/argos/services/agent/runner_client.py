"""Agent Runner HTTP APIを使うエージェントクライアント。"""

from __future__ import annotations

import time
from collections.abc import Iterable

import requests

from argos.config import AgentSlot, Settings


class RunnerAgentClient:
    """ARGOS本体からAgent Runnerへジョブを依頼するクライアント。"""

    def __init__(self, settings: Settings) -> None:
        """設定からRunner接続情報とスロットを初期化する。"""
        if not settings.agent_runner_url.strip():
            raise ValueError("ARGOS_AGENT_RUNNER_URL が未設定です")
        self._settings = settings
        self._base_url = settings.agent_runner_url.rstrip("/")
        self._token = settings.agent_runner_token
        self._slots = settings.agent_slots
        if not self._slots:
            raise ValueError("エージェントスロットが設定されていません")
        self._index = 0
        self._active_job_ids: set[str] = set()

    @property
    def current_name(self) -> str:
        """現在の会話スロット名を返す。"""
        return self._slots[self._index].name

    @property
    def current_provider(self) -> str:
        """現在の会話スロットのprovider名を返す。"""
        return self._slots[self._index].provider

    def next_slot(self) -> str:
        """次の会話スロットへ切り替え、名前を返す。"""
        self._index = (self._index + 1) % len(self._slots)
        return self.current_name

    def reset_current(self) -> None:
        """現在スロットの保存済みセッションをRunner側で削除する。"""
        slot = self._slots[self._index]
        self._request("POST", "/api/slots/reset", json={"slot_name": slot.name, "provider": slot.provider})

    def ask(self, prompt: str) -> str:
        """現在スロットへプロンプトを送り、最終応答を返す。"""
        return "".join(self.ask_stream(prompt))

    def ask_stream(self, prompt: str) -> Iterable[str]:
        """Runnerにジョブを作成し、完了結果を返す。"""
        slot = self._slots[self._index]
        job = self._request(
            "POST",
            "/api/jobs",
            json={
                "slot_name": slot.name,
                "provider": slot.provider,
                "prompt": prompt,
            },
        )
        job_id = str(job["job_id"])
        self._active_job_ids.add(job_id)
        while True:
            state = self._request("GET", f"/api/jobs/{job_id}")
            status = str(state.get("status", ""))
            if status in {"completed", "delivered"}:
                result = str(state.get("result", ""))
                if result:
                    yield result
                self._request("POST", f"/api/jobs/{job_id}/deliver", json={})
                self._active_job_ids.discard(job_id)
                return
            if status in {"failed", "failed_delivered"}:
                error = str(state.get("error", "")).strip() or "Agent Runnerジョブに失敗しました"
                self._request("POST", f"/api/jobs/{job_id}/deliver", json={})
                self._active_job_ids.discard(job_id)
                raise RuntimeError(error)
            time.sleep(0.5)

    def list_undelivered(self) -> list[dict[str, object]]:
        """現在のARGOS処理外で完了した未配信ジョブを返す。"""
        payload = self._request("GET", "/api/jobs")
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            return []
        return [
            job
            for job in jobs
            if isinstance(job, dict) and str(job.get("job_id", "")) not in self._active_job_ids
        ]

    def mark_delivered(self, job_id: str) -> None:
        """指定ジョブを配信済みにする。"""
        self._request("POST", f"/api/jobs/{job_id}/deliver", json={})
        self._active_job_ids.discard(job_id)

    def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        """Runner APIへHTTPリクエストを送る。"""
        headers = dict(kwargs.pop("headers", {}) or {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = requests.request(method, f"{self._base_url}{path}", headers=headers, timeout=5, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Agent Runner APIエラー {response.status_code}: {response.text[:500]}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Agent Runner APIのレスポンス形式が不正です")
        return data
