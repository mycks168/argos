"""Waydroid上のGoogleマップ操作およびGPS追従制御を行うスクリプト。

AIエージェントがGoogleマップのナビ開始、ルート全体表示、拡大・縮小、
GPS追従の一時停止/再開を確実に行うためのCLIインターフェースを提供します。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Waydroid LXCコンテナ名およびパス
WAYDROID_LXC_PATH = "/var/lib/waydroid/lxc"
WAYDROID_CONTAINER_NAME = "waydroid"
GOOGLE_MAPS_PACKAGE = "com.google.android.apps.maps"
GPS_SERVICE_NAME = "waydroid-gps-bridge.service"


def is_container_frozen() -> bool:
    """Waydroidのコンテナが凍結中かどうかを確認する。

    Returns:
        凍結中ならTrue。状態を取得できない場合はFalse
    """
    try:
        res = subprocess.run(["waydroid", "status"], capture_output=True, text=True, timeout=10.0, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Waydroid状態取得エラー: %s", exc)
        return False
    return any(line.split()[:2] == ["Container:", "FROZEN"] for line in res.stdout.splitlines())


def ensure_unfrozen() -> bool:
    """凍結中のWaydroidコンテナを解除する。

    Waydroidは既定でコンテナを凍結し、凍結中はlxc-attachが応答しない。
    `waydroid app launch` は内部で解凍してからアプリを起動するため、sudoなしで解除できる。

    Returns:
        凍結していない状態になったらTrue
    """
    if not is_container_frozen():
        return True
    try:
        subprocess.run(
            ["waydroid", "app", "launch", GOOGLE_MAPS_PACKAGE],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Waydroid解凍エラー: %s", exc)
        return False
    return not is_container_frozen()


def run_waydroid_intent(intent_uri: str, package: str = GOOGLE_MAPS_PACKAGE) -> bool:
    """Waydroid内のAndroidへView Intentを発行する。

    Args:
        intent_uri: 起動対象のURI（例: google.navigation:q=... や geo:0,0?z=14）
        package: 送信先パッケージ名

    Returns:
        成功時True、失敗時False
    """
    # 凍結したままだとlxc-attachが応答しないため、先に解除する。
    ensure_unfrozen()
    cmd = [
        "sudo",
        "lxc-attach",
        "-P",
        WAYDROID_LXC_PATH,
        "-n",
        WAYDROID_CONTAINER_NAME,
        "--",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        intent_uri,
        "-p",
        package,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0, check=False)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Waydroid Intent実行エラー: %s", exc)
        return False


def set_gps_service(active: bool) -> bool:
    """GPS追従サービス（waydroid-gps-bridge）の開始または停止を行う。

    Args:
        active: Trueで起動(start)、Falseで停止(stop)

    Returns:
        成功時True、失敗時False
    """
    action = "start" if active else "stop"
    cmd = ["systemctl", "--user", action, GPS_SERVICE_NAME]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0, check=False)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("GPSサービス制御エラー: %s", exc)
        return False


def is_gps_service_active() -> bool:
    """GPS追従サービスが稼働中かどうかを確認する。

    Returns:
        稼働中ならTrue、停止中ならFalse
    """
    cmd = ["systemctl", "--user", "is-active", GPS_SERVICE_NAME]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0, check=False)
        return res.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, OSError):
        return False


def handle_navigate(destination: str) -> dict[str, Any]:
    """ハンズフリーでターンバイターンナビを開始する。

    画面の「開始」ボタンを押す必要なくダイレクトに案内を開始し、
    GPS追従サービスも自動で再開します。

    Args:
        destination: 目的地（施設名または住所、緯度経度）

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    # GPS追従を再開
    set_gps_service(True)
    # ナビIntent発行
    intent_uri = f"google.navigation:q={destination}"
    success = run_waydroid_intent(intent_uri)

    if success:
        return {
            "success": True,
            "action": "navigate",
            "destination": destination,
            "speech": f"{destination}へのナビゲーションを開始するね。現在地を追従中だよ。",
        }
    return {
        "success": False,
        "action": "navigate",
        "destination": destination,
        "speech": f"{destination}のナビゲーション開始に失敗したみたい。",
    }


def handle_overview(destination: str | None = None) -> dict[str, Any]:
    """ルート全体または周辺広域を表示する。

    Googleマップが現在地に引き戻して勝手に拡大しないよう、
    GPS追従サービスを自動で一時停止します。

    Args:
        destination: 目的地（指定時はそのルート全体、未指定時は現在地周辺の広域）

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    # 勝手にズームインされないようにGPS追従を停止
    set_gps_service(False)

    if destination:
        intent_uri = f"https://www.google.com/maps/dir/?api=1&destination={destination}"
        speech_text = f"現在地の追従を一時停止して、{destination}までのルート全体を表示したよ。"
    else:
        # 広域表示（z=10: 市・地域全体）
        intent_uri = "geo:0,0?z=10"
        speech_text = "現在地の追従を一時停止して、広域地図を表示したよ。"

    success = run_waydroid_intent(intent_uri)
    return {
        "success": success,
        "action": "overview",
        "destination": destination,
        "gps_tracking": False,
        "speech": speech_text if success else "地図の全体表示に失敗したみたい。",
    }


def handle_zoom(level: int) -> dict[str, Any]:
    """地図の縮尺（ズームレベル）を変更する。

    Args:
        level: ズームレベル（1〜21。広域:10〜12、市街地:14〜15、詳細:16〜18）

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    intent_uri = f"geo:0,0?z={level}"
    success = run_waydroid_intent(intent_uri)
    return {
        "success": success,
        "action": "zoom",
        "level": level,
        "speech": f"地図の縮尺をレベル{level}に変更したよ。" if success else "縮尺の変更に失敗したみたい。",
    }


def handle_zoom_in() -> dict[str, Any]:
    """地図を一段階拡大する（詳細表示）。

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    return handle_zoom(16)


def handle_zoom_out() -> dict[str, Any]:
    """地図を一段階縮小する（広域表示）。

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    return handle_zoom(11)


def handle_pause_tracking() -> dict[str, Any]:
    """GPS追従サービスを手動で一時停止する。

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    success = set_gps_service(False)
    return {
        "success": success,
        "action": "pause_tracking",
        "speech": "現在地の追従を一時停止したよ。" if success else "追従の停止に失敗したみたい。",
    }


def handle_resume_tracking() -> dict[str, Any]:
    """GPS追従サービスを手動で再開する。

    Returns:
        実行結果辞書（speechテキスト含む）
    """
    success = set_gps_service(True)
    return {
        "success": success,
        "action": "resume_tracking",
        "speech": "現在地の追従を再開したよ。" if success else "追従の再開に失敗したみたい。",
    }


def handle_status() -> dict[str, Any]:
    """GPS追従サービスの稼働状態を取得する。

    Returns:
        状態辞書
    """
    active = is_gps_service_active()
    return {
        "success": True,
        "gps_tracking": active,
        "speech": "GPS追従は稼働中だよ。" if active else "GPS追従は停止中だよ。",
    }


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数パーサーを構築する。

    Returns:
        ArgumentParserインスタンス
    """
    parser = argparse.ArgumentParser(
        description="Waydroid Googleマップ制御およびGPS追従管理スクリプト"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # navigate
    p_nav = subparsers.add_parser("navigate", help="ターンバイターンナビを開始する")
    p_nav.add_argument("destination", help="目的地名称または住所")

    # overview
    p_over = subparsers.add_parser("overview", help="ルート全体または広域を表示する")
    p_over.add_argument("destination", nargs="?", default=None, help="目的地（省略可）")

    # zoom
    p_zoom = subparsers.add_parser("zoom", help="指定ズームレベルに変更する")
    p_zoom.add_argument("level", type=int, help="ズームレベル (1〜21)")

    # zoom-in
    subparsers.add_parser("zoom-in", help="地図を拡大する")

    # zoom-out
    subparsers.add_parser("zoom-out", help="地図を縮小する")

    # pause-tracking
    subparsers.add_parser("pause-tracking", help="GPS追従を一時停止する")

    # resume-tracking
    subparsers.add_parser("resume-tracking", help="GPS追従を再開する")

    # status
    subparsers.add_parser("status", help="現在の追従状態を取得する")

    return parser


def main() -> None:
    """メインエントリーポイント。引数を解析して適切な処理を呼び出す。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "navigate":
        res = handle_navigate(args.destination)
    elif args.command == "overview":
        res = handle_overview(args.destination)
    elif args.command == "zoom":
        res = handle_zoom(args.level)
    elif args.command == "zoom-in":
        res = handle_zoom_in()
    elif args.command == "zoom-out":
        res = handle_zoom_out()
    elif args.command == "pause-tracking":
        res = handle_pause_tracking()
    elif args.command == "resume-tracking":
        res = handle_resume_tracking()
    elif args.command == "status":
        res = handle_status()
    else:
        res = {"success": False, "error": f"Unknown command: {args.command}"}

    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("success", False):
        sys.exit(1)


if __name__ == "__main__":
    main()
