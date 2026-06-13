import getpass
import json
from configparser import ConfigParser
from pathlib import Path


def _load_unit() -> ConfigParser:
    """systemd ユニットファイルを検証用に読み込む。"""
    parser = ConfigParser(strict=False)
    parser.optionxform = str
    parser.read(Path(__file__).parents[1] / "systemd" / "argos.service")
    return parser


def _load_runner_unit() -> ConfigParser:
    """Agent Runnerのsystemdユニットファイルを読み込む。"""
    parser = ConfigParser(strict=False)
    parser.optionxform = str
    parser.read(Path(__file__).parents[1] / "systemd" / "argos-agent-runner.service")
    return parser


def test_argos_service_uses_project_runtime():
    """ARGOS の仮想環境と設定ファイルを使って起動することを確認する。"""
    unit = _load_unit()
    project_dir = Path(__file__).parents[1].resolve()
    current_user = getpass.getuser()

    assert unit["Service"]["User"] in {"pi", current_user}
    assert unit["Service"]["Group"] in {"pi", current_user}

    wd = Path(unit["Service"]["WorkingDirectory"]).resolve()
    assert wd == project_dir or wd == Path("/home/pi/argos")

    env_file = Path(unit["Service"]["EnvironmentFile"]).resolve()
    assert env_file == project_dir / ".env" or env_file == Path("/home/pi/argos/.env")

    exec_start = unit["Service"]["ExecStart"]
    expected_exec = str(wd / ".venv" / "bin" / "argos")
    assert exec_start == expected_exec or exec_start == "/home/pi/argos/.venv/bin/argos"


def test_argos_service_is_enabled_for_system_boot():
    """システム起動時に有効化できるユニットであることを確認する。"""
    unit = _load_unit()

    assert unit["Unit"]["After"] == "network-online.target tailscale-online.target autossh-clove.service sound.target"
    assert unit["Unit"]["Wants"] == "network-online.target tailscale-online.target autossh-clove.service"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_agent_runner_service_uses_project_runtime():
    """Agent Runnerを別サービスとして起動できることを確認する。"""
    unit = _load_runner_unit()
    project_dir = Path(__file__).parents[1].resolve()
    current_user = getpass.getuser()

    user = unit["Service"].get("User")
    if user:
        assert user in {"pi", current_user}
    group = unit["Service"].get("Group")
    if group:
        assert group in {"pi", current_user}

    wd = Path(unit["Service"]["WorkingDirectory"]).resolve()
    assert wd == project_dir or wd == Path("/home/pi/argos")

    env_file = Path(unit["Service"]["EnvironmentFile"]).resolve()
    assert env_file == project_dir / ".env" or env_file == Path("/home/pi/argos/.env")

    exec_start = unit["Service"]["ExecStart"]
    expected_exec = str(wd / ".venv" / "bin" / "argos-agent-runner")
    assert exec_start == expected_exec or exec_start == "/home/pi/argos/.venv/bin/argos-agent-runner"

    assert unit["Service"]["Restart"] == "on-failure"


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
