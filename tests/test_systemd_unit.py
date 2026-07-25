import json
from configparser import ConfigParser
from pathlib import Path


def _render_unit(name: str, project_dir: Path | str = "/opt/argos") -> str:
    """systemd ユニットテンプレートを検証用の値で置換する。"""
    text = (Path(__file__).parents[1] / "systemd" / name).read_text()
    return (
        text.replace("@PROJECT_DIR@", str(project_dir))
        .replace("@ARGOS_USER@", "argos")
        .replace("@ARGOS_GROUP@", "argos")
        .replace("@USER_HOME@", "/home/argos")
        .replace("@ARGOS_UID@", "1001")
    )


def _load_unit(project_dir: Path | str = "/opt/argos") -> ConfigParser:
    """ARGOS本体のsystemdユニットを検証用に読み込む。"""
    parser = ConfigParser(strict=False)
    parser.optionxform = str
    parser.read_string(_render_unit("argos.service", project_dir))
    return parser


def _load_runner_unit(project_dir: Path | str = "/opt/argos") -> ConfigParser:
    """Agent Runnerのsystemdユニットファイルを読み込む。"""
    parser = ConfigParser(strict=False)
    parser.optionxform = str
    parser.read_string(_render_unit("argos-agent-runner.service", project_dir))
    return parser


def test_argos_service_uses_project_runtime():
    """ARGOS の仮想環境と設定ファイルを使って起動することを確認する。"""
    unit = _load_unit()
    project_dir = Path("/opt/argos")

    assert unit["Service"]["User"] == "argos"
    assert unit["Service"]["Group"] == "argos"

    wd = Path(unit["Service"]["WorkingDirectory"]).resolve()
    assert wd == project_dir

    env_file = Path(unit["Service"]["EnvironmentFile"]).resolve()
    assert env_file == project_dir / ".env"

    exec_start = unit["Service"]["ExecStart"]
    expected_exec = str(wd / ".venv" / "bin" / "argos")
    assert exec_start == expected_exec
    assert unit["Service"]["Environment"] == (
        "PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
    )


def test_argos_service_is_enabled_for_system_boot():
    """システム起動時に有効化できるユニットであることを確認する。"""
    unit = _load_unit()

    assert unit["Unit"]["After"] == "network-online.target tailscale-online.target sound.target"
    assert unit["Unit"]["Wants"] == "network-online.target tailscale-online.target"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_agent_runner_service_uses_project_runtime():
    """Agent Runnerを別サービスとして起動できることを確認する。"""
    unit = _load_runner_unit()
    project_dir = Path("/opt/argos")

    assert unit["Service"]["User"] == "argos"
    assert unit["Service"]["Group"] == "argos"

    wd = Path(unit["Service"]["WorkingDirectory"]).resolve()
    assert wd == project_dir

    env_file = Path(unit["Service"]["EnvironmentFile"]).resolve()
    assert env_file == project_dir / ".env"

    exec_start = unit["Service"]["ExecStart"]
    expected_exec = str(wd / ".venv" / "bin" / "argos-agent-runner")
    assert exec_start == expected_exec

    assert unit["Service"]["Restart"] == "on-failure"


def test_systemd_templates_support_development_project_dir():
    """開発環境ではARGOS_PROJECT_DIR相当の値へ置換できる。"""
    project_dir = Path("/home/dev/argos")
    unit = _load_unit(project_dir)
    runner_unit = _load_runner_unit(project_dir)

    assert unit["Service"]["WorkingDirectory"] == str(project_dir)
    assert unit["Service"]["EnvironmentFile"] == str(project_dir / ".env")
    assert unit["Service"]["ExecStart"] == str(project_dir / ".venv" / "bin" / "argos")
    assert runner_unit["Service"]["ExecStart"] == str(project_dir / ".venv" / "bin" / "argos-agent-runner")


def test_install_systemd_services_script_renders_templates():
    """systemdインストーラーがテンプレート置換を持つ。"""
    script = Path(__file__).parents[1] / "scripts" / "install-systemd-services.sh"
    text = script.read_text()

    assert "ARGOS_PROJECT_DIR:-/opt/argos" in text
    assert "ARGOS_SERVICE_USER:-argos" in text
    assert "@PROJECT_DIR@" in text
    assert "@ARGOS_USER@" in text


def test_dashboard_kiosk_disables_translation_ui():
    """キオスク画面ではChromiumの翻訳UIを表示しない。"""
    unit = ConfigParser(strict=False)
    unit.optionxform = str
    unit.read_string(_render_unit("argos-dashboard-kiosk.service"))
    script = (Path(__file__).parents[1] / "scripts" / "open-dashboard-kiosk.sh").read_text()

    assert unit["Service"]["EnvironmentFile"] == "/opt/argos/.env"

    assert "--lang=ja" in script
    assert 'CHROMIUM_SNAP_FONT_DIR="${HOME}/snap/chromium/current/.local/share/fonts/argos"' in script
    assert "/usr/share/fonts/opentype/ipafont-gothic/*.ttf" in script
    assert "--no-first-run" in script
    assert "--no-default-browser-check" in script
    assert "--disable-extensions" in script
    assert "--disable-sync" in script
    assert "--disable-features=Translate,TranslateUI" in script
    assert "--disable-translate" in script
    assert "xset s off" in script
    assert "xset -dpms" in script
    assert "gsettings set org.gnome.desktop.screensaver lock-enabled false" in script


def test_dashboard_kiosk_uses_portable_splash_url():
    """接続待ち画面は配置先に追従し、転送先をファイル名と分離して渡す。"""
    project_dir = Path(__file__).parents[1]
    script = (project_dir / "scripts" / "open-dashboard-kiosk.sh").read_text()
    splash = (project_dir / "scripts" / "kiosk-splash.html").read_text()

    assert 'dirname -- "${BASH_SOURCE[0]}"' in script
    assert "pathlib.Path(sys.argv[1]).resolve().as_uri()" in script
    assert 'SPLASH_URL="${SPLASH_FILE_URL}#target=${ENCODED_TARGET}"' in script
    assert 'SPLASH_DIR="${HOME}/snap/chromium/common/argos-dashboard-kiosk"' in script
    assert 'install -m 600 "${SCRIPT_DIR}/kiosk-splash.html" "${SPLASH_FILE}"' in script
    assert "file:///opt/argos/scripts/kiosk-splash.html" not in script
    assert "location.hash.slice(1)" in splash
    assert 'queryParams.get("target")' in splash


def test_dashboard_chromium_policy_disables_translation():
    """Chromium管理ポリシーで翻訳バーとサインインUIを無効化する。"""
    policy_path = Path(__file__).parents[1] / "chromium" / "argos-dashboard.json"
    policy = json.loads(policy_path.read_text())

    assert policy["TranslateEnabled"] is False
    assert policy["BrowserSignin"] == 0
    assert policy["SyncDisabled"] is True
    assert policy["PasswordManagerEnabled"] is False


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
