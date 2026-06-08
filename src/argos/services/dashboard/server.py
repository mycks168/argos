"""HDMI ダッシュボード用HTTP APIとSSE配信。"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from argos.services.dashboard.state import DashboardState


log = logging.getLogger(__name__)
MAX_BODY_BYTES = 256 * 1024
DEFAULT_CAMERA_SNAPSHOT_PATH = Path("/tmp/argos/camera-latest.jpg")


class DashboardServer:
    """ダッシュボード画面、API、SSEを別スレッドで提供する。"""

    def __init__(
        self,
        state: DashboardState,
        host: str,
        port: int,
        token: str,
        camera_snapshot_path: Path = DEFAULT_CAMERA_SNAPSHOT_PATH,
        control_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """HTTPサーバー設定を保持する。"""
        self._state = state
        self._host = host
        self._port = port
        self._token = token
        self._camera_snapshot_path = camera_snapshot_path
        self._control_handler = control_handler
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        """起動済みサーバーのアドレスを返す。"""
        if self._server is None:
            return self._host, self._port
        return self._server.server_address

    def start(self) -> None:
        """HTTPサーバーをバックグラウンドで起動する。"""
        handler = _create_handler(self._state, self._token, self._camera_snapshot_path, self._control_handler)
        self._server = ThreadingHTTPServer((self._host, self._port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("HDMIダッシュボード起動: http://%s:%d", *self.address)

    def stop(self) -> None:
        """HTTPサーバーを停止する。"""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None


def _create_handler(
    state: DashboardState,
    token: str,
    camera_snapshot_path: Path,
    control_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """状態とトークンを束縛したHTTPハンドラーを作成する。"""

    class DashboardHandler(BaseHTTPRequestHandler):
        """ダッシュボードHTTPリクエストを処理する。"""

        def do_GET(self) -> None:
            """画面、状態、ヘルスチェック、SSEを返す。"""
            path = urlparse(self.path).path
            if path == "/":
                self._send_html()
            elif path == "/api/health":
                self._send_json({"status": "ok"})
            elif path == "/api/state":
                self._send_json(state.snapshot())
            elif path == "/api/stream":
                self._send_sse()
            elif path == "/camera/latest.jpg":
                self._send_camera_snapshot()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            """Bearer認証付き更新APIを処理する。"""
            path = urlparse(self.path).path
            if path not in {"/api/events", "/api/control"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not self._require_token():
                return
            try:
                payload = self._read_json()
                if path == "/api/events":
                    response = _apply_event(state, payload)
                    status = HTTPStatus.CREATED
                else:
                    response = self._apply_control(payload)
                    status = HTTPStatus.OK
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(response, status)

        def log_message(self, format: str, *args: object) -> None:
            """標準HTTPログをアプリログへ流す。"""
            log.info("dashboard http: " + format, *args)

        def _read_json(self) -> dict[str, Any]:
            """リクエストボディをJSONオブジェクトとして読む。"""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length が不正です") from exc
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("リクエストサイズが不正です")
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as exc:
                raise ValueError("JSONが不正です") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSONオブジェクトを送信してください")
            return payload

        def _require_token(self) -> bool:
            """更新系APIのBearer認証を検証する。"""
            if not token:
                self._send_json({"error": "ARGOS_DASHBOARD_TOKEN が未設定です"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return False
            if self.headers.get("Authorization", "") != f"Bearer {token}":
                self._send_json({"error": "認証に失敗しました"}, HTTPStatus.UNAUTHORIZED)
                return False
            return True

        def _apply_control(self, payload: dict[str, Any]) -> dict[str, Any]:
            """ダッシュボード操作をARGOS本体へ渡す。"""
            if control_handler is None:
                raise ValueError("コントロールAPIは無効です")
            _required_text(payload, "action", 40)
            return control_handler(payload)

        def _send_html(self) -> None:
            """ダッシュボードHTMLを返す。"""
            html_text = files("argos.services.dashboard.static").joinpath("dashboard.html").read_text(encoding="utf-8")
            html_text = html_text.replace("__ARGOS_DASHBOARD_TOKEN__", json.dumps(token, ensure_ascii=False))
            html = html_text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            """JSONレスポンスを返す。"""
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_camera_snapshot(self) -> None:
            """最新のカメラ静止画を返す。"""
            try:
                image = camera_snapshot_path.read_bytes()
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)

        def _send_sse(self) -> None:
            """状態更新をServer-Sent Eventsで配信する。"""
            subscriber = state.subscribe()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b"event: ready\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        revision = subscriber.get(timeout=15)
                        body = json.dumps({"revision": revision}).encode("utf-8")
                        self.wfile.write(b"event: update\ndata: " + body + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                state.unsubscribe(subscriber)

    return DashboardHandler


def _apply_event(state: DashboardState, payload: dict[str, Any]) -> dict[str, Any]:
    """外部表示イベントを状態へ反映する。"""
    event_type = _required_text(payload, "type", 40)
    if event_type == "notification":
        notification_id = state.add_notification(
            title=_required_text(payload, "title", 120),
            text=_optional_text(payload, "text", 2000),
            source=_optional_text(payload, "source", 80),
            priority=_optional_text(payload, "priority", 20) or "normal",
            image_url=_optional_text(payload, "image_url", 2000),
            link_url=_optional_text(payload, "link_url", 2000),
        )
        return {"id": notification_id}
    if event_type in {"user_message", "agent_message"}:
        role = "user" if event_type == "user_message" else "assistant"
        message_id = state.add_message(role, _required_text(payload, "text", 8000))
        return {"id": message_id}
    if event_type == "status":
        state.set_status(_required_text(payload, "code", 40), _required_text(payload, "label", 80))
        return {"status": "updated"}
    if event_type == "clear_notifications":
        state.clear_notifications()
        return {"status": "cleared"}
    raise ValueError(f"未対応のイベント種別です: {event_type}")


def _required_text(payload: dict[str, Any], key: str, max_length: int) -> str:
    """必須文字列を検証して返す。"""
    value = _optional_text(payload, key, max_length)
    if not value:
        raise ValueError(f"{key} は必須です")
    return value


def _optional_text(payload: dict[str, Any], key: str, max_length: int) -> str:
    """任意文字列を検証して返す。"""
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列で指定してください")
    if len(value) > max_length:
        raise ValueError(f"{key} が長すぎます")
    return value
