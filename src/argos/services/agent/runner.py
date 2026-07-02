"""エージェント実行をARGOS本体から分離するRunner。"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from argos.config import AgentSlot, Settings
from argos.services.agent.client import AgentClient, create_provider_client
from argos.services.http_base import JsonRequestHandler, bearer_header_matches


log = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024


@dataclass
class AgentJob:
    """Runnerが管理するエージェント実行ジョブ。"""

    job_id: str
    slot_name: str
    provider: str
    cwd: str
    status: str
    prompt_path: str
    output_path: str
    error_path: str
    result_path: str
    created_at: float
    updated_at: float
    delivered_to_argos: bool = False
    delivered_at: float | None = None


class AgentJobStore:
    """ジョブ台帳と入出力ファイルを永続化する。"""

    def __init__(self, state_dir: Path) -> None:
        """保存先ディレクトリを初期化する。"""
        self._state_dir = state_dir.expanduser()
        self._jobs_dir = self._state_dir / "jobs"
        self._lock = threading.Lock()

    def create(self, slot: AgentSlot, prompt: str) -> AgentJob:
        """新しいジョブを作成し、プロンプトを保存する。"""
        now = time.time()
        job_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + uuid.uuid4().hex[:8]
        job_dir = self._jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = job_dir / "prompt.txt"
        output_path = job_dir / "output.txt"
        error_path = job_dir / "error.txt"
        result_path = job_dir / "result.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        job = AgentJob(
            job_id=job_id,
            slot_name=slot.name,
            provider=slot.provider,
            cwd=slot.cwd,
            status="queued",
            prompt_path=str(prompt_path),
            output_path=str(output_path),
            error_path=str(error_path),
            result_path=str(result_path),
            created_at=now,
            updated_at=now,
        )
        self.save(job)
        return job

    def save(self, job: AgentJob) -> None:
        """ジョブ状態をJSONへ保存する。"""
        with self._lock:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            job_dir = self._jobs_dir / job.job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            job.updated_at = time.time()
            path = job_dir / "job.json"
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)

    def load(self, job_id: str) -> AgentJob | None:
        """ジョブIDから状態を読み込む。"""
        path = self._jobs_dir / job_id / "job.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        try:
            return AgentJob(**data)
        except TypeError:
            log.warning("ジョブ状態JSONの形式が不正です: %s", path)
            return None

    def mark_delivered(self, job_id: str) -> AgentJob | None:
        """ジョブをARGOSへ配信済みにする。"""
        job = self.load(job_id)
        if job is None:
            return None
        job.delivered_to_argos = True
        job.delivered_at = time.time()
        if job.status == "completed":
            job.status = "delivered"
        elif job.status == "failed":
            job.status = "failed_delivered"
        self.save(job)
        return job

    def list_undelivered(self) -> list[AgentJob]:
        """完了済みでARGOSへ未配信のジョブ一覧を返す。"""
        if not self._jobs_dir.exists():
            return []
        jobs: list[AgentJob] = []
        for path in self._jobs_dir.glob("*/job.json"):
            job = self.load(path.parent.name)
            if job and job.status in {"completed", "failed"} and not job.delivered_to_argos:
                jobs.append(job)
        return sorted(jobs, key=lambda item: item.created_at)

    def find_active(self, slot_name: str, provider: str, active_job_ids: set[str] | None = None) -> AgentJob | None:
        """指定スロットで現在プロセスが実行中のジョブを返す。"""
        if not self._jobs_dir.exists():
            return None
        candidates: list[AgentJob] = []
        for path in self._jobs_dir.glob("*/job.json"):
            job = self.load(path.parent.name)
            if (
                job
                and job.slot_name == slot_name
                and job.provider == provider
                and job.status in {"queued", "running"}
                and (active_job_ids is None or job.job_id in active_job_ids)
            ):
                candidates.append(job)
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.created_at)[-1]

    def mark_interrupted_active_jobs_failed(self) -> list[AgentJob]:
        """Runner再起動で実行スレッドを失ったジョブを失敗扱いにする。"""
        if not self._jobs_dir.exists():
            return []
        failed: list[AgentJob] = []
        for path in self._jobs_dir.glob("*/job.json"):
            job = self.load(path.parent.name)
            if job is None or job.status not in {"queued", "running"}:
                continue
            message = "Agent Runnerが再起動したため、未完了ジョブを失敗扱いにしました。"
            try:
                Path(job.error_path).write_text(message, encoding="utf-8")
            except OSError:
                log.exception("中断ジョブのエラー保存に失敗しました: %s", job.job_id)
            job.status = "failed"
            self.save(job)
            failed.append(job)
        return failed


class AgentSlotBusyError(RuntimeError):
    """同一スロットで別ジョブが実行中の場合のエラー。"""

    def __init__(self, job: AgentJob) -> None:
        """実行中ジョブを保持してエラーメッセージを作る。"""
        self.job = job
        super().__init__(f"スロット {job.slot_name} は既に応答処理中です: job_id={job.job_id}")


class AgentRunner:
    """ジョブを受け付けて別スレッドでエージェントを実行する。"""

    def __init__(
        self,
        settings: Settings,
        store: AgentJobStore,
        client_factory: Callable[[Settings, AgentSlot], AgentClient] = create_provider_client,
    ) -> None:
        """設定、ジョブストア、providerクライアント作成関数を保持する。"""
        self._settings = settings
        self._store = store
        self._client_factory = client_factory
        self._slots = {_slot_key(slot): slot for slot in settings.agent_slots}
        self._active_job_ids: set[str] = set()
        recovered = self._store.mark_interrupted_active_jobs_failed()
        if recovered:
            log.warning("Runner再起動で中断されたジョブを失敗扱いにしました: count=%s", len(recovered))

    def start_job(self, slot_name: str, provider: str, prompt: str) -> AgentJob:
        """指定スロットのジョブを開始する。

        同一スロットでこのRunnerプロセスが実行中のジョブがある場合は409相当の
        エラーにする。新しい発話を既存ジョブへ吸収すると、ユーザー入力を失うため。
        """
        slot = self._slots.get(_slot_key_values(slot_name, provider))
        if slot is None:
            raise ValueError(f"未定義のスロットです: {slot_name}/{provider}")
        existing = self._store.find_active(slot.name, slot.provider, self._active_job_ids)
        if existing is not None:
            log.warning(
                "スロット %s は既にジョブ実行中のため新規ジョブを拒否します: job_id=%s",
                slot.name,
                existing.job_id,
            )
            raise AgentSlotBusyError(existing)
        job = self._store.create(slot, prompt)
        self._active_job_ids.add(job.job_id)
        thread = threading.Thread(target=self._run_job, args=(job, slot), daemon=True)
        thread.start()
        return job

    def get_job(self, job_id: str) -> AgentJob | None:
        """ジョブ状態を返す。"""
        return self._store.load(job_id)

    def mark_delivered(self, job_id: str) -> AgentJob | None:
        """ジョブを配信済みにする。"""
        return self._store.mark_delivered(job_id)

    def reset_slot(self, slot_name: str, provider: str) -> None:
        """指定スロットの保存済みセッションを削除する。"""
        slot = self._slots.get(_slot_key_values(slot_name, provider))
        if slot is None:
            raise ValueError(f"未定義のスロットです: {slot_name}/{provider}")
        client = self._client_factory(_settings_for_slot(self._settings, slot), slot)
        client.reset_current()

    def list_undelivered(self) -> list[AgentJob]:
        """未配信ジョブを返す。"""
        return self._store.list_undelivered()

    def _run_job(self, job: AgentJob, slot: AgentSlot) -> None:
        """エージェントを実行し、結果をジョブディレクトリへ保存する。"""
        job.status = "running"
        self._store.save(job)
        slot_settings = _settings_for_slot(self._settings, slot)
        try:
            client = self._client_factory(slot_settings, slot)
            chunks: list[str] = []
            with Path(job.output_path).open("w", encoding="utf-8") as output:
                for chunk in client.ask_stream(Path(job.prompt_path).read_text(encoding="utf-8")):
                    chunks.append(chunk)
                    output.write(chunk)
                    output.flush()
            result = "".join(chunks)
            Path(job.result_path).write_text(result, encoding="utf-8")
            job.status = "completed"
        except Exception as exc:
            log.exception("Agent Runnerジョブに失敗しました: %s", job.job_id)
            Path(job.error_path).write_text(str(exc), encoding="utf-8")
            job.status = "failed"
        finally:
            self._active_job_ids.discard(job.job_id)
            self._store.save(job)


class AgentRunnerServer:
    """AgentRunnerをHTTP APIとして公開する。"""

    def __init__(self, runner: AgentRunner, token: str = "") -> None:
        """RunnerとBearerトークンを保持する。"""
        self._runner = runner
        self._token = token
        self._server: ThreadingHTTPServer | None = None

    def serve_forever(self, host: str, port: int) -> None:
        """HTTPサーバーを起動する。"""
        handler = self._build_handler()
        self._server = ThreadingHTTPServer((host, port), handler)
        log.info("Agent Runner 起動: http://%s:%s", host, port)
        self._server.serve_forever()

    def stop(self) -> None:
        """HTTPサーバーを停止する。"""
        if self._server is not None:
            self._server.shutdown()

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        """Runnerインスタンスを閉じ込めたリクエストハンドラーを作る。"""
        runner = self._runner
        token = self._token

        class Handler(JsonRequestHandler):
            """Agent Runner HTTPハンドラー。"""

            def do_GET(self) -> None:
                """ジョブ状態や未配信ジョブを返す。"""
                if not _is_authorized(self.headers.get("Authorization", ""), token):
                    self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/api/jobs":
                    self._send_json({"jobs": [_job_payload(job) for job in runner.list_undelivered()]}, HTTPStatus.OK)
                    return
                prefix = "/api/jobs/"
                if parsed.path.startswith(prefix):
                    job = runner.get_job(parsed.path.removeprefix(prefix).strip("/"))
                    if job is None:
                        self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(_job_payload(job), HTTPStatus.OK)
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                """ジョブ作成や配信済み更新を受け付ける。"""
                if not _is_authorized(self.headers.get("Authorization", ""), token):
                    self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/api/jobs":
                    try:
                        payload = self._read_json(MAX_BODY_BYTES, allow_empty=True)
                        job = runner.start_job(
                            str(payload.get("slot_name", "")),
                            str(payload.get("provider", "")),
                            str(payload.get("prompt", "")),
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                        return
                    except AgentSlotBusyError as exc:
                        self._send_json(
                            {
                                "error": str(exc),
                                "active_job_id": exc.job.job_id,
                            },
                            HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json(_job_payload(job), HTTPStatus.ACCEPTED)
                    return
                suffix = "/deliver"
                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith(suffix):
                    job_id = parsed.path.removeprefix("/api/jobs/")[: -len(suffix)].strip("/")
                    job = runner.mark_delivered(job_id)
                    if job is None:
                        self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(_job_payload(job), HTTPStatus.OK)
                    return
                if parsed.path == "/api/slots/reset":
                    try:
                        payload = self._read_json(MAX_BODY_BYTES, allow_empty=True)
                        runner.reset_slot(str(payload.get("slot_name", "")), str(payload.get("provider", "")))
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json({"ok": True}, HTTPStatus.OK)
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            def log_message(self, format: str, *args: object) -> None:
                """標準エラーではなくloggingへHTTPアクセスログを出す。"""
                log.info("agent-runner http: " + format, *args)

        return Handler


def _settings_for_slot(settings: Settings, slot: AgentSlot) -> Settings:
    """指定スロットだけを持つ設定へ変換する。"""
    from dataclasses import replace

    return replace(settings, agent_provider=slot.provider, agent_slots=(slot,), agent_runner_url="")


def _slot_key(slot: AgentSlot) -> str:
    """スロット識別キーを返す。"""
    return _slot_key_values(slot.name, slot.provider)


def _slot_key_values(name: str, provider: str) -> str:
    """スロット名とproviderから識別キーを返す。"""
    return f"{provider.strip().lower()}\0{name.strip()}"


def _is_authorized(header: str, token: str) -> bool:
    """Bearer認証が有効ならヘッダーを検証する。トークン未設定なら無認証で通す。"""
    if not token:
        return True
    return bearer_header_matches(header, token)


def _job_payload(job: AgentJob) -> dict[str, object]:
    """HTTPレスポンス用にジョブ状態と結果を辞書へ変換する。"""
    payload = asdict(job)
    if Path(job.output_path).exists():
        # 実行中も逐次flushされる出力。ポーリング側がここから差分を取り出す。
        payload["output"] = Path(job.output_path).read_text(encoding="utf-8")
    if Path(job.result_path).exists():
        payload["result"] = Path(job.result_path).read_text(encoding="utf-8")
    if Path(job.error_path).exists():
        payload["error"] = Path(job.error_path).read_text(encoding="utf-8")
    return payload
