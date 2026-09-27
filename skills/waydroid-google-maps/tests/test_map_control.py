"""Waydroid Googleマップ制御スクリプトの単体テストモジュール。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "map_control.py"
spec = importlib.util.spec_from_file_location("map_control", SCRIPT_PATH)
assert spec is not None
map_control = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(map_control)


@pytest.fixture(autouse=True)
def _no_freeze_check(request):
    """Intent系テストでは凍結確認を常に「凍結なし」にする。凍結確認自体のテストは除く。"""
    if "freeze" in request.node.name or "frozen" in request.node.name:
        yield
        return
    with patch.object(map_control, "ensure_unfrozen", return_value=True):
        yield


def test_run_waydroid_intent_success() -> None:
    """Waydroid Intent発行が成功する場合のテスト。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = map_control.run_waydroid_intent("google.navigation:q=Tokyo")
        assert res is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "Tokyo" in args[-3]


def test_run_waydroid_intent_failure() -> None:
    """Waydroid Intent発行が失敗する場合のテスト。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        res = map_control.run_waydroid_intent("invalid_uri")
        assert res is False


def test_run_waydroid_intent_exception() -> None:
    """Waydroid Intent発行でTimeoutや例外が発生する場合のテスト。"""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5.0)):
        res = map_control.run_waydroid_intent("geo:0,0")
        assert res is False


def test_set_gps_service_start() -> None:
    """GPSサービス開始処理のテスト。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = map_control.set_gps_service(True)
        assert res is True
        assert mock_run.call_args[0][0] == [
            "systemctl",
            "--user",
            "start",
            "waydroid-gps-bridge.service",
        ]


def test_set_gps_service_stop() -> None:
    """GPSサービス停止処理のテスト。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        res = map_control.set_gps_service(False)
        assert res is True
        assert mock_run.call_args[0][0] == [
            "systemctl",
            "--user",
            "stop",
            "waydroid-gps-bridge.service",
        ]


def test_set_gps_service_exception() -> None:
    """GPSサービス制御時の例外発生テスト。"""
    with patch("subprocess.run", side_effect=OSError("Command not found")):
        res = map_control.set_gps_service(True)
        assert res is False


def test_is_gps_service_active() -> None:
    """GPSサービス稼働状態確認のテスト。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="active\n")
        assert map_control.is_gps_service_active() is True

        mock_run.return_value = MagicMock(stdout="inactive\n")
        assert map_control.is_gps_service_active() is False

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 3.0)):
        assert map_control.is_gps_service_active() is False


def test_handle_navigate_success() -> None:
    """ナビ開始処理が成功する場合のテスト。"""
    with (
        patch.object(map_control, "set_gps_service") as mock_gps,
        patch.object(map_control, "run_waydroid_intent") as mock_intent,
    ):
        mock_intent.return_value = True
        res = map_control.handle_navigate("都城駅")
        assert res["success"] is True
        assert "ナビゲーションを開始するね" in res["speech"]
        mock_gps.assert_called_once_with(True)
        mock_intent.assert_called_once_with("google.navigation:q=都城駅")


def test_handle_navigate_failure() -> None:
    """ナビ開始処理が失敗する場合のテスト。"""
    with (
        patch.object(map_control, "set_gps_service"),
        patch.object(map_control, "run_waydroid_intent", return_value=False),
    ):
        res = map_control.handle_navigate("都城駅")
        assert res["success"] is False
        assert "失敗したみたい" in res["speech"]


def test_handle_overview_with_destination() -> None:
    """目的地指定での全体表示テスト。"""
    with (
        patch.object(map_control, "set_gps_service") as mock_gps,
        patch.object(map_control, "run_waydroid_intent", return_value=True),
    ):
        res = map_control.handle_overview("都城駅")
        assert res["success"] is True
        assert res["gps_tracking"] is False
        assert "ルート全体を表示したよ" in res["speech"]
        mock_gps.assert_called_once_with(False)


def test_handle_overview_without_destination() -> None:
    """目的地なしでの広域表示テスト。"""
    with (
        patch.object(map_control, "set_gps_service") as mock_gps,
        patch.object(map_control, "run_waydroid_intent", return_value=True),
    ):
        res = map_control.handle_overview(None)
        assert res["success"] is True
        assert "広域地図を表示したよ" in res["speech"]
        mock_gps.assert_called_once_with(False)


def test_handle_overview_failure() -> None:
    """全体表示失敗テスト。"""
    with (
        patch.object(map_control, "set_gps_service"),
        patch.object(map_control, "run_waydroid_intent", return_value=False),
    ):
        res = map_control.handle_overview()
        assert res["success"] is False
        assert "失敗したみたい" in res["speech"]


def test_handle_zoom() -> None:
    """ズームレベル変更テスト。"""
    with patch.object(map_control, "run_waydroid_intent", return_value=True):
        res = map_control.handle_zoom(15)
        assert res["success"] is True
        assert res["level"] == 15
        assert "レベル15" in res["speech"]

    with patch.object(map_control, "run_waydroid_intent", return_value=False):
        res = map_control.handle_zoom(15)
        assert res["success"] is False


def test_handle_zoom_in_out() -> None:
    """ズームイン・ズームアウトのテスト。"""
    with patch.object(map_control, "handle_zoom") as mock_zoom:
        mock_zoom.return_value = {"success": True}
        map_control.handle_zoom_in()
        mock_zoom.assert_called_with(16)

        map_control.handle_zoom_out()
        mock_zoom.assert_called_with(11)


def test_handle_pause_and_resume_tracking() -> None:
    """GPS追従手動一時停止および再開テスト。"""
    with patch.object(map_control, "set_gps_service") as mock_gps:
        mock_gps.return_value = True
        res_pause = map_control.handle_pause_tracking()
        assert res_pause["success"] is True
        assert "一時停止したよ" in res_pause["speech"]
        mock_gps.assert_called_with(False)

        res_resume = map_control.handle_resume_tracking()
        assert res_resume["success"] is True
        assert "再開したよ" in res_resume["speech"]
        mock_gps.assert_called_with(True)

    with patch.object(map_control, "set_gps_service", return_value=False):
        assert map_control.handle_pause_tracking()["success"] is False
        assert map_control.handle_resume_tracking()["success"] is False


def test_handle_status() -> None:
    """ステータス取得テスト。"""
    with patch.object(map_control, "is_gps_service_active", return_value=True):
        res = map_control.handle_status()
        assert res["gps_tracking"] is True
        assert "稼働中だよ" in res["speech"]

    with patch.object(map_control, "is_gps_service_active", return_value=False):
        res = map_control.handle_status()
        assert res["gps_tracking"] is False
        assert "停止中だよ" in res["speech"]


def test_build_parser() -> None:
    """コマンドラインパーサーの検証テスト。"""
    parser = map_control.build_parser()
    args = parser.parse_args(["navigate", "宮崎駅"])
    assert args.command == "navigate"
    assert args.destination == "宮崎駅"

    args = parser.parse_args(["overview"])
    assert args.command == "overview"
    assert args.destination is None

    args = parser.parse_args(["zoom", "12"])
    assert args.command == "zoom"
    assert args.level == 12


def test_main(capsys: pytest.CaptureFixture[str]) -> None:
    """mainエントリーポイントのテスト。"""
    with (
        patch("sys.argv", ["map_control.py", "status"]),
        patch.object(map_control, "handle_status") as mock_status,
    ):
        mock_status.return_value = {"success": True, "gps_tracking": True}
        map_control.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["success"] is True

    # 失敗時にsys.exit(1)されることの確認
    with (
        patch("sys.argv", ["map_control.py", "navigate", "dummy"]),
        patch.object(map_control, "handle_navigate") as mock_nav,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_nav.return_value = {"success": False}
        map_control.main()
    assert exc_info.value.code == 1


STATUS_RUNNING = "Session:\tRUNNING\nContainer:\tRUNNING\n"
STATUS_FROZEN = "Session:\tRUNNING\nContainer:\tFROZEN\n"


def test_is_container_frozen() -> None:
    """凍結状態をwaydroid statusの出力から判定する。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=STATUS_FROZEN)
        assert map_control.is_container_frozen() is True
        mock_run.return_value = MagicMock(stdout=STATUS_RUNNING)
        assert map_control.is_container_frozen() is False


def test_is_container_frozen_unknown_status() -> None:
    """状態を取得できない場合は凍結とみなさない。"""
    with patch("subprocess.run", side_effect=OSError("waydroid not found")):
        assert map_control.is_container_frozen() is False


def test_ensure_unfrozen_noop_when_running() -> None:
    """凍結していなければ何も起動しない。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=STATUS_RUNNING)
        assert map_control.ensure_unfrozen() is True
        mock_run.assert_called_once()


def test_ensure_unfrozen_launches_to_unfreeze() -> None:
    """凍結中はwaydroid app launchで解除し、解除後の状態を返す。"""
    outputs = [STATUS_FROZEN, "", "", STATUS_RUNNING]  # 状態・active_apps・起動・状態
    with patch("subprocess.run", side_effect=lambda cmd, **kw: MagicMock(stdout=outputs.pop(0))) as mock_run:
        assert map_control.ensure_unfrozen() is True
        assert mock_run.call_args_list[2][0][0] == ["waydroid", "app", "launch", "com.google.android.apps.maps"]


def test_ensure_unfrozen_reports_failure() -> None:
    """解除後も凍結のままならFalseを返す。"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=STATUS_FROZEN)
        assert map_control.ensure_unfrozen() is False


def test_ensure_unfrozen_launch_timeout() -> None:
    """解除コマンドがタイムアウトしたらFalseを返す。"""
    calls = []

    def fake_run(cmd, **kwargs):
        """状態確認は凍結中、起動はタイムアウトさせる。"""
        calls.append(cmd)
        if cmd[:2] == ["waydroid", "status"]:
            return MagicMock(stdout=STATUS_FROZEN)
        raise subprocess.TimeoutExpired(cmd, 30.0)

    with patch("subprocess.run", side_effect=fake_run):
        assert map_control.ensure_unfrozen() is False


def test_run_waydroid_intent_unfreezes_first_frozen() -> None:
    """Intent発行の前に必ず凍結解除を試みる。"""
    order = []
    with (
        patch.object(map_control, "ensure_unfrozen", side_effect=lambda: order.append("unfreeze") or True),
        patch("subprocess.run", side_effect=lambda cmd, **kw: order.append("intent") or MagicMock(returncode=0)),
    ):
        assert map_control.run_waydroid_intent("geo:0,0?z=14") is True
    assert order == ["unfreeze", "intent"]


@pytest.mark.parametrize(
    "active, expected",
    [
        ("com.google.android.apps.maps\n", ["waydroid", "app", "launch", "com.google.android.apps.maps"]),
        ("Waydroid\n", ["waydroid", "show-full-ui"]),
        ("", ["waydroid", "app", "launch", "com.google.android.apps.maps"]),
        ("[2026-09-27] log line\nWaydroid\n", ["waydroid", "show-full-ui"]),
    ],
)
def test_unfreeze_command_keeps_full_ui_mode(active, expected) -> None:
    """Android全体を1つのウィンドウで見せているときは、アプリごとの表示へ切り替わらないよう、show-full-uiで解凍する。"""
    with patch("subprocess.run", return_value=MagicMock(stdout=active)):
        assert map_control.unfreeze_command() == expected


def test_unfreeze_command_falls_back_when_prop_unavailable() -> None:
    """active_appsを取得できなくても、従来どおり、アプリ起動で解凍する。"""
    with patch("subprocess.run", side_effect=OSError("waydroidがありません")):
        assert map_control.unfreeze_command()[:3] == ["waydroid", "app", "launch"]
