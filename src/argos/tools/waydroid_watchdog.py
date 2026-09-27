"""Waydroidの音声の部品が壊れたら、原因の記録を残して、自動で再起動する見張り。

Androidの音声サーバー(audioserver)と音声HALが消えると、地図などの音声を使うアプリが
起動の途中で止まる。この状態は自動では戻らないため、検知して再起動する。
あわせて、落ちる直前のホストの状態を短い間隔で覚えておき、落ちたときに記録として残す
（原因の調査用）。
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("argos.waydroid_watchdog")

LXC = ["sudo", "-n", "lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--"]
AUDIO_PROCESSES = ("audioserver", "android.hardware.audio.service")
STATE_DIR = Path.home() / ".local/state/argos"
INCIDENT_DIR = STATE_DIR / "waydroid-incidents"


def run_text(command: list[str], timeout: float = 8.0) -> str | None:
    """コマンドを実行して標準出力を返す。失敗・タイムアウトならNoneを返す。"""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def parse_status(text: str | None) -> dict[str, str]:
    """`waydroid status` の出力を、項目名と値の辞書にする。"""
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition(":")
        if value:
            values[key.strip()] = value.strip()
    return values


class Watchdog:
    """Waydroidの音声の部品の状態を見張り、壊れたら記録を残して再起動する。

    - 起動直後（grace_seconds以内）や、凍結中は、判断しない。
    - 部品が消えた状態が、連続でfail_threshold回続いたら、壊れたと判断する。
    - 再起動は、max_restarts回/window_secondsまでに制限し、繰り返し落ちても暴走しない。
    外部コマンドと時計は、外から渡せるので、実機なしで動作を検証できる。
    """

    def __init__(
        self,
        *,
        run: Callable[..., str | None] = run_text,
        restart: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        incident_dir: Path = INCIDENT_DIR,
        fail_threshold: int = 2,
        grace_seconds: float = 120.0,
        max_restarts: int = 3,
        window_seconds: float = 3600.0,
        history_size: int = 60,
        sample_host: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """外部とのやり取りと、判断の基準を保持する。"""
        self._run = run
        self._restart = restart
        self._clock = clock
        self._wall = wall
        self._incident_dir = incident_dir
        self._fail_threshold = fail_threshold
        self._grace = grace_seconds
        self._max_restarts = max_restarts
        self._window = window_seconds
        self._sample_host = sample_host or self.default_host_sample
        self._history: collections.deque[dict[str, Any]] = collections.deque(maxlen=history_size)
        self._failures = 0
        self._restarts: list[float] = []
        self._session_seen_at: float | None = None

    @staticmethod
    def default_host_sample() -> dict[str, Any]:
        """落ちる直前の状態を調べるための、ホストの様子を1回分、集める。"""
        sample: dict[str, Any] = {}
        try:
            sample["loadavg"] = os.getloadavg()
            with open("/proc/meminfo", encoding="utf-8") as handle:
                info = dict(line.split(":", 1) for line in handle if ":" in line)
            sample["mem_available_kb"] = int(info["MemAvailable"].split()[0])
            sample["swap_free_kb"] = int(info["SwapFree"].split()[0])
        except (OSError, KeyError, ValueError):
            pass
        streams = run_text(["pw-dump"], timeout=5.0)
        if streams:
            try:
                nodes = []
                for item in json.loads(streams):
                    props = item.get("info", {}).get("props", {})
                    if item.get("type", "").endswith("Node") and "Stream" in props.get("media.class", ""):
                        nodes.append([props.get("application.name"), props.get("media.class"), item["info"].get("state")])
                sample["streams"] = nodes
            except (ValueError, AttributeError):
                sample["streams"] = "解析できません"
        else:
            sample["streams"] = "pw-dumpが応答しません"
        return sample

    def check_audio(self) -> bool | None:
        """音声の部品が、両方、動いているかを返す。判断できない状態（凍結中など）ならNoneを返す。"""
        status = parse_status(self._run(["waydroid", "status"]))
        if status.get("Session") != "RUNNING":
            self._session_seen_at = None
            return None
        now = self._clock()
        if self._session_seen_at is None:
            self._session_seen_at = now
        if status.get("Container") != "RUNNING" or now - self._session_seen_at < self._grace:
            return None
        output = self._run([*LXC, "sh", "-c", "pidof " + " ".join(AUDIO_PROCESSES)], timeout=6.0)
        if output is None:
            # 何も見つからないとpidofは失敗を返す。コンテナに入れないだけの可能性も、ここで見分ける。
            reachable = self._run([*LXC, "true"], timeout=6.0)
            return False if reachable is not None else None
        return len(output.split()) >= len(AUDIO_PROCESSES)

    def tick(self) -> str:
        """1回分の見張り。行った処理の名前（ok/skip/suspect/restarted/limited）を返す。"""
        self._history.append({"time": self._wall(), **self._sample_host()})
        healthy = self.check_audio()
        if healthy is None:
            self._failures = 0
            return "skip"
        if healthy:
            self._failures = 0
            return "ok"
        self._failures += 1
        if self._failures < self._fail_threshold:
            return "suspect"
        now = self._clock()
        self._restarts = [moment for moment in self._restarts if now - moment < self._window]
        self.save_incident("limited" if len(self._restarts) >= self._max_restarts else "restart")
        if len(self._restarts) >= self._max_restarts:
            log.error("Waydroidの音声の部品が壊れていますが、再起動の回数の上限に達したため、再起動しません")
            return "limited"
        self._restarts.append(now)
        self._failures = 0
        self._session_seen_at = None
        log.warning("Waydroidの音声の部品が壊れているため、再起動します")
        self._restart()
        return "restarted"

    def save_incident(self, action: str) -> Path | None:
        """壊れた時点の記録（直前のホストの様子、Androidの落ちた原因）を、ファイルに残す。"""
        directory = self._incident_dir / time.strftime("%Y%m%d-%H%M%S", time.localtime(self._wall()))
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "host-history.json").write_text(json.dumps(list(self._history), ensure_ascii=False, default=str), encoding="utf-8")
            (directory / "summary.txt").write_text(f"action={action}\n", encoding="utf-8")
            probes = {
                "logcat-audio.txt": "logcat -d -v threadtime 2>/dev/null | grep -i -E 'audio|TimeCheck|died|MediaPlayer' | tail -200",
                "tombstone.txt": "T=$(ls -t /data/tombstones 2>/dev/null | grep -v pb | head -1); "
                "[ -n \"$T\" ] && head -c 4000 /data/tombstones/$T",
                "processes.txt": "ps -A -o pid,etime,name 2>/dev/null | head -80",
            }
            for name, command in probes.items():
                output = self._run([*LXC, "sh", "-c", command], timeout=15.0)
                (directory / name).write_text(output if output is not None else "取得できませんでした\n", encoding="utf-8")
            host = self._run(["sudo", "-n", "journalctl", "-k", "--since", "-3 min", "--no-pager"], timeout=10.0)
            (directory / "kernel.txt").write_text(host or "取得できませんでした\n", encoding="utf-8")
        except OSError:
            log.exception("記録を保存できませんでした")
            return None
        return directory


def recover() -> None:
    """Waydroidを、今の描画サイズのまま再起動し、画面配置を元に戻す。"""
    from argos.tools import window_layout

    size = window_layout.android_size()
    if not size:
        raise RuntimeError("Androidの描画サイズを取得できないため、再起動できません")
    window_layout.restart_android(size)
    subprocess.run(
        [os.environ.get("ARGOS_PYTHON", "python3"), "-m", "argos.tools.window_layout", "show"],
        check=False,
        timeout=600,
    )


def main(argv: list[str] | None = None) -> int:
    """見張りを続ける。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=10.0, help="確認の間隔(秒)")
    parser.add_argument("--once", action="store_true", help="1回だけ確認して終了する")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dog = Watchdog(restart=recover)
    while True:
        try:
            result = dog.tick()
        except Exception:  # noqa: BLE001 - 見張りは、再起動の失敗などで止めない
            log.exception("見張りの処理に失敗しました")
            result = "error"
        if args.once:
            print(result)
            return 0 if result in ("ok", "skip", "suspect") else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
