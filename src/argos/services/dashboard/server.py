"""HDMI ダッシュボード用HTTP APIとSSE配信。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from argos.services.dashboard.location import DEFAULT_GPS_DEVICE_PATH, read_location
from argos.services.dashboard.state import DashboardState
from argos.services.http_base import JsonRequestHandler, bearer_header_matches


log = logging.getLogger(__name__)
MAX_BODY_BYTES = 256 * 1024
DEFAULT_CAMERA_SNAPSHOT_PATH = Path("/tmp/argos/camera-latest.jpg")
DEFAULT_UPLOAD_DIR = Path("/tmp/argos/uploads")
DEFAULT_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_UPLOAD_KEEP = 50
FONT_SIZE_OPTIONS = {"small", "medium", "large"}
# アップロード画像のMIMEタイプと保存拡張子の対応。
UPLOAD_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
# アップロード画像を配信するときの拡張子とMIMEタイプの対応。
UPLOAD_EXTENSION_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _normalize_font_size(value: str) -> str:
    """ダッシュボードのフォントサイズ設定を正規化する。"""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in FONT_SIZE_OPTIONS else "medium"


class DashboardServer:
    """ダッシュボード画面、API、SSEを別スレッドで提供する。"""

    def __init__(
        self,
        state: DashboardState,
        host: str,
        port: int,
        token: str,
        camera_snapshot_path: Path = DEFAULT_CAMERA_SNAPSHOT_PATH,
        gps_device_path: Path = DEFAULT_GPS_DEVICE_PATH,
        screensaver_seconds: float = 300.0,
        default_font_size: str = "medium",
        location_provider: str = "local",
        remote_location_url: str = "",
        remote_location_timeout_seconds: float = 2.0,
        upload_dir: Path = DEFAULT_UPLOAD_DIR,
        upload_max_bytes: int = DEFAULT_UPLOAD_MAX_BYTES,
        upload_keep: int = DEFAULT_UPLOAD_KEEP,
        control_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        event_handler: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        """HTTPサーバー設定を保持する。"""
        self._state = state
        self._host = host
        self._port = port
        self._token = token
        self._camera_snapshot_path = camera_snapshot_path
        self._gps_device_path = gps_device_path
        self._upload_dir = upload_dir
        self._upload_max_bytes = upload_max_bytes
        self._upload_keep = upload_keep
        self._screensaver_seconds = screensaver_seconds
        self._default_font_size = _normalize_font_size(default_font_size)
        self._location_provider = location_provider
        self._remote_location_url = remote_location_url
        self._remote_location_timeout_seconds = remote_location_timeout_seconds
        self._control_handler = control_handler
        self._event_handler = event_handler
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
        handler = _create_handler(
            self._state,
            self._token,
            self._camera_snapshot_path,
            self._gps_device_path,
            self._screensaver_seconds,
            self._default_font_size,
            self._location_provider,
            self._remote_location_url,
            self._remote_location_timeout_seconds,
            self._upload_dir,
            self._upload_max_bytes,
            self._upload_keep,
            self._control_handler,
            self._event_handler,
        )
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
    gps_device_path: Path = DEFAULT_GPS_DEVICE_PATH,
    screensaver_seconds: float = 300.0,
    default_font_size: str = "medium",
    location_provider: str = "local",
    remote_location_url: str = "",
    remote_location_timeout_seconds: float = 2.0,
    upload_dir: Path = DEFAULT_UPLOAD_DIR,
    upload_max_bytes: int = DEFAULT_UPLOAD_MAX_BYTES,
    upload_keep: int = DEFAULT_UPLOAD_KEEP,
    control_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    event_handler: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """状態とトークンを束縛したHTTPハンドラーを作成する。"""

    class DashboardHandler(JsonRequestHandler):
        """ダッシュボードHTTPリクエストを処理する。"""

        def do_GET(self) -> None:
            """画面、状態、ヘルスチェック、SSEを返す。"""
            path = urlparse(self.path).path
            if path == "/":
                self._send_html()
            elif path.startswith("/static/"):
                self._send_static_file(path)
            elif path == "/api/health":
                self._send_json({"status": "ok"})
            elif path == "/api/state":
                self._send_json(state.snapshot())
            elif path == "/api/location":
                self._send_json(
                    read_location(
                        location_provider,
                        gps_device_path,
                        remote_location_url,
                        remote_location_timeout_seconds,
                    )
                )
            elif path == "/api/stream":
                self._send_sse()
            elif path == "/camera/latest.jpg":
                self._send_camera_snapshot()
            elif path.startswith("/uploads/"):
                self._send_upload(path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            """Bearer認証付き更新APIを処理する。"""
            path = urlparse(self.path).path
            if path == "/api/uploads":
                if not self._require_token():
                    return
                self._handle_upload()
                return
            if path not in {"/api/events", "/api/control"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not self._require_token():
                return
            try:
                payload = self._read_json(MAX_BODY_BYTES)
                if path == "/api/events":
                    response = _apply_event(state, payload)
                    if event_handler is not None:
                        event_handler(payload, response)
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

        def _require_token(self) -> bool:
            """更新系APIのBearer認証を検証する。"""
            if not token:
                self._send_json({"error": "ARGOS_DASHBOARD_TOKEN が未設定です"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return False
            if not bearer_header_matches(self.headers.get("Authorization", ""), token):
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
            html_text = html_text.replace("__ARGOS_DASHBOARD_SCREENSAVER_SECONDS__", json.dumps(screensaver_seconds))
            html_text = html_text.replace("__ARGOS_DASHBOARD_DEFAULT_FONT_SIZE__", json.dumps(_normalize_font_size(default_font_size)))
            html = html_text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def _send_static_file(self, path: str) -> None:
            """static ディレクトリ内の静的ファイルを返す。"""
            filename = path.replace("/static/", "", 1)
            if ".." in filename or filename.startswith("/") or filename.startswith("."):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            try:
                content = files("argos.services.dashboard.static").joinpath(filename).read_bytes()
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            mime_type = "application/octet-stream"
            if filename.endswith(".html"):
                mime_type = "text/html; charset=utf-8"
            elif filename.endswith(".js"):
                mime_type = "application/javascript; charset=utf-8"
            elif filename.endswith(".css"):
                mime_type = "text/css; charset=utf-8"
            elif filename.endswith(".png"):
                mime_type = "image/png"
            elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
                mime_type = "image/jpeg"

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

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

        def _handle_upload(self) -> None:
            """通知用画像を受け取り、保存してURLを返す。"""
            content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            extension = UPLOAD_MIME_EXTENSIONS.get(content_type)
            if extension is None:
                self._send_json({"error": "対応していない画像形式です"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "Content-Length が不正です"}, HTTPStatus.BAD_REQUEST)
                return
            if length <= 0 or length > upload_max_bytes:
                self._send_json({"error": "画像サイズが不正です"}, HTTPStatus.BAD_REQUEST)
                return
            data = self.rfile.read(length)
            name = f"{uuid.uuid4().hex}{extension}"
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / name).write_bytes(data)
            _prune_uploads(upload_dir, upload_keep)
            self._send_json({"url": f"/uploads/{name}"}, HTTPStatus.CREATED)

        def _send_upload(self, path: str) -> None:
            """アップロード済みの通知画像を配信する。"""
            name = path.replace("/uploads/", "", 1)
            if not name or "/" in name or ".." in name or name.startswith("."):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            file_path = upload_dir / name
            try:
                content = file_path.read_bytes()
            except (FileNotFoundError, IsADirectoryError, OSError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime_type = UPLOAD_EXTENSION_MIMES.get(file_path.suffix.lower(), "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

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
        display = _optional_text(payload, "display", 20) or "toast"
        if display not in {"toast", "center"}:
            raise ValueError(f"無効な display です: {display}")
        # target（宛先ラベル）は将来の一斉通知向け。本体は受理して無視する。
        notification_id = state.add_notification(
            title=_required_text(payload, "title", 120),
            text=_optional_text(payload, "text", 2000),
            source=_optional_text(payload, "source", 80),
            priority=_optional_text(payload, "priority", 20) or "normal",
            image_url=_optional_text(payload, "image_url", 2000),
            link_url=_optional_text(payload, "link_url", 2000),
            display=display,
            duration_seconds=_optional_number(payload, "duration_seconds"),
        )
        return {"id": notification_id}
    if event_type == "clear_center_alert":
        state.clear_center_alert()
        return {"status": "cleared"}
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
    if event_type == "overlay":
        target_slot = _optional_text(payload, "target_slot", 20) or "right"
        if target_slot not in {"center", "right"}:
            raise ValueError(f"無効な target_slot です: {target_slot}")
        state.push_overlay(
            target_slot=target_slot,
            overlay_type=_required_text(payload, "overlay_type", 40),
            title=_required_text(payload, "title", 120),
            content=_optional_text(payload, "content", 64000),
            url=_optional_text(payload, "url", 2000),
            options=payload.get("options"),
            replace_top=bool(payload.get("replace_top", False)),
        )
        return {"status": "overlay_updated"}
    if event_type == "clear_overlay":
        target_slot = _optional_text(payload, "target_slot", 20) or "all"
        if target_slot not in {"center", "right", "all"}:
            raise ValueError(f"無効な target_slot です: {target_slot}")
        state.pop_overlay(target_slot)
        return {"status": "overlay_cleared"}
    if event_type == "swap_slots":
        state.swap_slots()
        return {"status": "slots_swapped"}
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


def _optional_number(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    """任意の非負数を検証して返す。"""
    if key not in payload:
        return default
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} は数値で指定してください")
    if value < 0:
        raise ValueError(f"{key} は0以上で指定してください")
    return float(value)


def _prune_uploads(directory: Path, keep: int) -> None:
    """アップロード画像を新しい順に keep 件だけ残し、古いものを削除する。"""
    if keep <= 0:
        return
    try:
        entries = [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    for stale in entries[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue
