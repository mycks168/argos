"""ARGOSのエントリーポイントのテスト。"""

import pytest

from argos import main as main_module


class FakeApp:
    """runだけを持つARGOSの代わり。"""

    restart = False

    def __init__(self, settings):
        """設定を受け取る。"""
        self.settings = settings

    @property
    def restart_requested(self):
        """再起動を求められたかを返す。"""
        return FakeApp.restart

    def run(self):
        """何もしない。"""


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    """設定の読み込みと実行時ディレクトリの作成を差し替える。"""
    monkeypatch.setattr(main_module, "load_settings", lambda: "settings")
    monkeypatch.setattr(main_module, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(main_module, "ArgosApp", FakeApp)


def test_main_exits_normally_without_restart():
    """再起動を求められていなければ、普通に終わる。"""
    FakeApp.restart = False
    assert main_module.main() is None


def test_main_exits_with_restart_code():
    """再起動を求められて止まったら、systemdが起動し直すよう、0以外の終了コードで終わる。"""
    FakeApp.restart = True
    with pytest.raises(SystemExit) as exc:
        main_module.main()
    assert exc.value.code == main_module.RESTART_EXIT_CODE != 0
