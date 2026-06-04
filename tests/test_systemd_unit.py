import json
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


def test_dashboard_kiosk_disables_translation_ui():
    """キオスク画面ではChromiumの翻訳UIを表示しない。"""
    script = (Path(__file__).parents[1] / "scripts" / "open-dashboard-kiosk.sh").read_text()

    assert "--lang=ja" in script
    assert "--disable-extensions" in script
    assert "--disable-features=Translate,TranslateUI" in script
    assert "--disable-translate" in script


def test_dashboard_chromium_policy_disables_translation():
    """Chromium管理ポリシーで翻訳バーを無効化する。"""
    policy_path = Path(__file__).parents[1] / "chromium" / "argos-dashboard.json"

    assert json.loads(policy_path.read_text())["TranslateEnabled"] is False


def test_hash_auth_keyword_script_exists():
    """音声キーワードをハッシュ化する補助スクリプトがある。"""
    script = Path(__file__).parents[1] / "scripts" / "hash-auth-keyword.py"

    assert script.exists()
    assert "hash_keyword" in script.read_text()


def test_enroll_face_auth_script_exists():
    """顔認証の登録スクリプトがある。"""
    script = Path(__file__).parents[1] / "scripts" / "enroll-face-auth.py"

    assert script.exists()
    assert "FaceAuthVerifier" in script.read_text()
    assert "verifier.detect" in script.read_text()


def test_check_face_detection_script_exists():
    """顔検出確認スクリプトがある。"""
    script = Path(__file__).parents[1] / "scripts" / "check-face-detection.py"

    assert script.exists()
    assert "DEFAULT_CAMERA_SNAPSHOT_PATH" in script.read_text()


def test_download_face_models_script_exists():
    """顔認証モデル取得スクリプトがある。"""
    script = Path(__file__).parents[1] / "scripts" / "download-face-models.py"

    assert script.exists()
    assert "face_detection_yunet_2023mar.onnx" in script.read_text()
