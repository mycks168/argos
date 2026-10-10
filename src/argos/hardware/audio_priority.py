"""他アプリ（Waydroidのナビ音声など）の再生中、ARGOSの発話を保留するための監視。

PipeWireのイベント(`pw-dump -m`)から、指定したアプリ名の再生ストリームが
動いているかを追い、動き始めたら保留、止まって少し待ったら再開を通知する。
"""

from __future__ import annotations

import json
import logging
import os
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

log = logging.getLogger(__name__)

PLAYBACK_CLASS = "Stream/Output/Audio"


class StreamActivityTracker:
    """pw-dumpの要素を受け取り、指定アプリの再生ストリームが動いているかを追う。"""

    def __init__(self, app_names: Iterable[str]) -> None:
        """監視するアプリ名（大文字小文字は区別しない）を保持する。"""
        self._app_names = {name.strip().lower() for name in app_names if name.strip()}
        self._props: dict[int, dict[str, Any]] = {}
        self._states: dict[int, str] = {}

    @property
    def active(self) -> bool:
        """対象アプリの再生ストリームが1つでも動いていればTrueを返す。"""
        return any(self._is_target(node_id) and state == "running" for node_id, state in self._states.items())

    def reset(self) -> None:
        """監視の再接続時に、覚えているストリームを全て捨てる。"""
        self._props.clear()
        self._states.clear()

    def feed(self, element: dict[str, Any]) -> bool:
        """1つのオブジェクトの更新を反映し、動作中かどうかが変わったらTrueを返す。

        削除は `info` が null で届く。ストリームの属性は作成時に届き、状態
        (suspended/running/idle など)はその後の更新で届くため、両方を覚えて突き合わせる。
        """
        before = self.active
        node_id = element.get("id")
        info = element.get("info")
        if not isinstance(node_id, int):
            return False
        if info is None:
            self._props.pop(node_id, None)
            self._states.pop(node_id, None)
            return before != self.active
        props = info.get("props")
        if isinstance(props, dict) and props:
            self._props.setdefault(node_id, {}).update(props)
        state = info.get("state")
        if isinstance(state, str):
            self._states[node_id] = state
        return before != self.active

    def _is_target(self, node_id: int) -> bool:
        """対象アプリの再生ストリームならTrueを返す。"""
        props = self._props.get(node_id, {})
        if props.get("media.class") != PLAYBACK_CLASS:
            return False
        return str(props.get("application.name", "")).strip().lower() in self._app_names


def iter_json_elements(buffer: str) -> Iterator[tuple[dict[str, Any], str]]:
    """閉じていないJSON配列の先頭から、完成した要素を順に取り出す。

    `pw-dump -m` は配列を閉じずに要素を流し続けるため、要素ごとに読み出す。
    各要素と、その要素より後ろの残りの文字列を返す。
    """
    decoder = json.JSONDecoder()
    rest = buffer
    while True:
        text = rest.lstrip(" \r\n\t,[]")
        if not text:
            return
        try:
            element, end = decoder.raw_decode(text)
        except ValueError:
            return
        rest = text[end:]
        if isinstance(element, dict):
            yield element, rest


class PriorityStreamWatcher:
    """対象アプリの再生を監視し、開始・終了をコールバックで通知するスレッド。

    再生が途切れて `release_delay` 秒たってから終了を通知する。ナビの案内が
    続けて鳴るときの短いすき間に、ARGOSの発話が割り込まないようにするため。
    """

    def __init__(
        self,
        app_names: Iterable[str],
        on_active: Callable[[], None],
        on_idle: Callable[[], None],
        *,
        release_delay: float = 0.6,
        restart_delay: float = 3.0,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """監視対象と通知先、時間の設定を保持する。"""
        self._tracker = StreamActivityTracker(app_names)
        self._on_active = on_active
        self._on_idle = on_idle
        self._release_delay = max(0.0, release_delay)
        self._restart_delay = restart_delay
        self._popen = popen
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        self._holding = False
        self._idle_since: float | None = None

    @property
    def holding(self) -> bool:
        """保留を通知している最中ならTrueを返す。"""
        return self._holding

    def start(self) -> None:
        """監視スレッドを開始する。pw-dumpがない環境では何もしない。"""
        if shutil.which("pw-dump") is None:
            log.warning("pw-dumpが見つからないため、他アプリの音声再生の監視を無効化します")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-priority-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """監視を止め、保留中なら解除する。"""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._release()

    def handle(self, element: dict[str, Any]) -> None:
        """pw-dumpの1要素を処理する。動き始めたら保留を通知する。"""
        self._tracker.feed(element)
        self.evaluate()

    def evaluate(self) -> None:
        """現在の動作状況から、保留の開始・終了を判断して通知する。"""
        if self._tracker.active:
            self._idle_since = None
            if not self._holding:
                self._holding = True
                log.info("他アプリの音声再生を検知したため、ARGOSの発話を一時停止します")
                self._safe_call(self._on_active)
            return
        if not self._holding:
            return
        now = self._clock()
        if self._idle_since is None:
            self._idle_since = now
        if now - self._idle_since >= self._release_delay:
            self._release()

    def _release(self) -> None:
        """保留を解除して通知する。保留していなければ何もしない。"""
        self._idle_since = None
        if not self._holding:
            return
        self._holding = False
        log.info("他アプリの音声再生が終わったため、ARGOSの発話を再開します")
        self._safe_call(self._on_idle)

    def _safe_call(self, callback: Callable[[], None]) -> None:
        """コールバックの例外で監視を止めないよう、記録だけして続ける。"""
        try:
            callback()
        except Exception:  # noqa: BLE001 - 監視は他の機能の失敗で止めない
            log.exception("音声の保留・再開の通知に失敗しました")

    def _command(self) -> list[str]:
        """pw-dumpの起動コマンドを返す。出力を行ごとに受け取るため、あればstdbufを使う。"""
        command = ["pw-dump", "-m"]
        if shutil.which("stdbuf"):
            command = ["stdbuf", "-oL", *command]
        return command

    def _run(self) -> None:
        """pw-dumpを起動して出力を読み続ける。異常終了したら、保留を解除して再接続する。"""
        while not self._stop.is_set():
            try:
                self._proc = self._popen(self._command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError:
                log.exception("pw-dumpを起動できませんでした")
                self._release()
                if self._stop.wait(self._restart_delay):
                    return
                continue
            self._tracker.reset()
            self._read(self._proc)
            self._release()
            if self._stop.wait(self._restart_delay):
                return

    def _read(self, proc: Any) -> None:
        """pw-dumpの出力を読み、要素ごとに処理する。終了か停止で戻る。"""
        stream = proc.stdout
        fd = stream.fileno()
        decoder_buffer = b""
        text = ""
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    return
                decoder_buffer += chunk
                # 途中で切れたUTF-8の続きは次回へ回す。
                try:
                    text += decoder_buffer.decode("utf-8")
                    decoder_buffer = b""
                except UnicodeDecodeError:
                    continue
                for element, rest in iter_json_elements(text):
                    self.handle(element)
                    text = rest
            else:
                # イベントがなくても、再開の待ち時間は進める。
                self.evaluate()
            if proc.poll() is not None and not ready:
                return
