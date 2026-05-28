from configparser import ConfigParser
from pathlib import Path


def _load_unit() -> ConfigParser:
    """systemd ユニットファイルを検証用に読み込む。"""
    parser = ConfigParser(strict=False)
    parser.optionxform = str
    parser.read(Path(__file__).parents[1] / "systemd" / "argos.service")
    return parser


def test_argos_service_uses_project_runtime():
    """ARGOS の仮想環境と設定ファイルを使って起動することを確認する。"""
    unit = _load_unit()

    assert unit["Service"]["User"] == "pi"
    assert unit["Service"]["Group"] == "pi"
    assert unit["Service"]["WorkingDirectory"] == "/home/pi/argos"
    assert unit["Service"]["EnvironmentFile"] == "/home/pi/argos/.env"
    assert unit["Service"]["ExecStart"] == "/home/pi/argos/.venv/bin/argos"


def test_argos_service_is_enabled_for_system_boot():
    """システム起動時に有効化できるユニットであることを確認する。"""
    unit = _load_unit()

    assert unit["Unit"]["After"] == "network-online.target sound.target"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Install"]["WantedBy"] == "multi-user.target"
