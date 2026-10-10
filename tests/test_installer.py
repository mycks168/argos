import json
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from argos import installer
from argos.installer import (
    DEFAULT_OS_PACKAGES,
    KIOSK_OS_PACKAGES,
    DEFAULT_MANIFEST,
    apply_plan,
    build_install_plan,
    configure_config,
    load_manifest,
    main,
    migrate_config,
    plan_to_dict,
    render_unit_template,
    update_project,
    AGENT_LIMIT_CRON_MARKER,
    CHROMIUM_POLICY_TARGETS,
    _ensure_uv_for_user,
    _resolve_os_packages,
    _ensure_agent_limit_cron,
    _ensure_config_file,
    _ensure_core_config_defaults,
    _ensure_reminder_dashboard_token,
    _ensure_tts_filter_shared_token,
    _install_chromium_policy,
    _prepare_unified_slots_for_configure,
    _reload_systemd,
    _remove_inaccessible_venv,
    _configure_kiosk_display,
    _restore_unified_slots_after_configure,
)
from argos.yaml_config import load_yaml_environment, write_yaml_from_environment


def test_project_python_range_stays_compatible_with_lgpio_wheels():
    """インストーラ起動前にlgpioのソースビルドが必要にならないPython範囲を使う。"""
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["requires-python"] == ">=3.11,<3.13"


def test_load_manifest_lists_core_and_planned_services():
    """サービスマニフェストから主要サービスを読み込める。"""
    services = load_manifest()
    names = {service.name for service in services}

    assert "argos" in names
    assert "argos-agent-runner" in names
    assert "tts-filter" in names
    assert "argos-acknowledgement-api" in names
    assert "stt-gateway" in names
    assert "wakeword-models" in names


def test_build_install_plan_includes_external_and_planned_steps(tmp_path):
    """外部依存と取り込み予定サービスをインストール計画へ含める。"""
    services = load_manifest()
    plan = build_install_plan(
        services,
        project_dir=tmp_path / "argos",
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
    )
    actions = {(step.service, step.action) for step in plan.steps}

    assert ("argos", "render-unit") in actions
    assert ("tts-filter", "sync") in actions
    assert ("tts-filter", "render-unit") in actions
    assert ("agent-limit", "cron") in actions
    assert ("", "policy") in actions
    assert ("", "home") in actions
    assert ("wakeword-models", "check") in actions
    assert ("stt-gateway", "configure") in actions
    assert plan.service_user == "argos"


def test_ensure_config_file_migrates_all_env_values(tmp_path):
    """インストーラーは既存.envを階層YAMLへ欠落なく同期する。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ARGOS_DASHBOARD_PORT=8765\n"
        "ARGOS_AGENT_SLOT_1=作業,codex,/opt/argos,2,gpt-test\n"
        "CUSTOM_SECRET=secret\n",
        encoding="utf-8",
    )

    _ensure_config_file(tmp_path)

    values = load_yaml_environment(tmp_path / "config.yaml")
    assert values["ARGOS_DASHBOARD_PORT"] == "8765"
    slots = json.loads(values["ARGOS_AGENT_SLOTS_JSON"])
    assert slots[0] == {
        "type": "local",
        "name": "作業",
        "provider": "codex",
        "cwd": "/opt/argos",
        "voicevox_speaker": 2,
        "model": "gpt-test",
    }
    assert values["CUSTOM_SECRET"] == "secret"


def test_ensure_config_file_preserves_existing_file(tmp_path):
    """通常更新では利用者が編集したconfig.yamlを上書きしない。"""
    (tmp_path / ".env").write_text("ARGOS_DASHBOARD_PORT=8765\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  port: 9999\n", encoding="utf-8")

    _ensure_config_file(tmp_path)

    assert config_path.read_text(encoding="utf-8") == "dashboard:\n  port: 9999\n"


def test_migrate_config_creates_yaml_without_install(tmp_path):
    """設定移行コマンドは.envだけをYAMLへ変換する。"""
    (tmp_path / ".env").write_text(
        "ARGOS_DASHBOARD_PORT=8765\nARGOS_AGENT_SLOT_1=作業,codex,/opt/argos\n",
        encoding="utf-8",
    )

    config_path = migrate_config(tmp_path)

    assert config_path == tmp_path / "config.yaml"
    assert config_path.exists()
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_migrate_config_refuses_existing_yaml(tmp_path):
    """既存config.yamlは移行コマンドで上書きしない。"""
    (tmp_path / ".env").write_text("ARGOS_DASHBOARD_PORT=8765\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  port: 9999\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="上書きしません"):
        migrate_config(tmp_path)

    assert config_path.read_text(encoding="utf-8") == "dashboard:\n  port: 9999\n"


def test_main_migrate_config_skips_manifest_and_plan(capsys, tmp_path):
    """CLIの設定移行はマニフェスト読込や計画表示を行わない。"""
    (tmp_path / ".env").write_text("ARGOS_DASHBOARD_PORT=8765\n", encoding="utf-8")

    result = main(["--project-dir", str(tmp_path), "--migrate-config"])

    assert result == 0
    assert (tmp_path / "config.yaml").exists()
    assert "設定を移行しました" in capsys.readouterr().out


def test_ensure_config_file_copies_yaml_example(tmp_path):
    """設定がない新規環境ではYAMLサンプルを共通設定として使う。"""
    example = tmp_path / "config.yaml.example"
    example.write_text("dashboard:\n  port: 9999\n", encoding="utf-8")

    config_path = _ensure_config_file(tmp_path)

    assert config_path.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_configure_helpers_preserve_remote_slot_position():
    """対話設定でローカルを更新してもリモートの配置を維持する。"""
    values = {
        "ARGOS_AGENT_PROVIDER": "codex",
        "ARGOS_AGENT_SLOTS_JSON": json.dumps(
            [
                {"type": "local", "name": "旧", "provider": "codex", "cwd": "/old"},
                {
                    "type": "remote",
                    "name": "自宅",
                    "url": "https://home.example",
                    "remote_name": "作業",
                    "remote_provider": "codex",
                },
            ]
        ),
    }

    template = _prepare_unified_slots_for_configure(values)
    values["ARGOS_AGENT_SLOT_1"] = "新,claude,/opt/argos,,sonnet"
    _restore_unified_slots_after_configure(values, template)

    slots = json.loads(values["ARGOS_AGENT_SLOTS_JSON"])
    assert [slot["name"] for slot in slots] == ["新", "自宅"]
    assert slots[0]["model"] == "sonnet"
    assert slots[1]["type"] == "remote"
    assert "ARGOS_AGENT_SLOT_1" not in values


def test_bundled_wakeword_models_exist():
    """同梱ウェイクワードモデルが既定パスに揃っている。"""
    model_dir = DEFAULT_MANIFEST.parents[1] / "models" / "wakeword"

    assert (model_dir / "argos.onnx").exists()
    assert (model_dir / "melspectrogram.onnx").exists()
    assert (model_dir / "embedding_model.onnx").exists()
    assert (model_dir / "silero_vad_v6.onnx").exists()


def test_plan_to_dict_is_json_serializable(tmp_path):
    """計画をJSONとして出力できる。"""
    plan = build_install_plan(
        load_manifest(),
        project_dir=tmp_path / "argos",
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="deploy",
        service_group="staff",
    )
    payload = plan_to_dict(plan)

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "argos-reminder" in encoded
    assert payload["service_user"] == "deploy"
    assert payload["service_group"] == "staff"
    assert payload["service_home"] == "/home/deploy"


def test_main_prints_human_readable_plan(capsys, tmp_path):
    """CLIは既定で人間向けのdry-run計画を表示する。"""
    result = main(
        [
            "--project-dir",
            str(tmp_path),
            "--system-unit-dir",
            str(tmp_path / "system"),
            "--user-unit-dir",
            str(tmp_path / "user"),
            "--user",
            "deploy",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "ARGOSインストール計画" in output
    assert "tts-filter" in output
    assert "service_user: deploy" in output


def test_build_install_plan_bootstrap_includes_dedicated_user_steps(tmp_path):
    """bootstrap有効時は専用ユーザーとOS初期設定の手順を含める。"""
    plan = build_install_plan(
        load_manifest(),
        project_dir=tmp_path / "argos",
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
        bootstrap=True,
    )
    actions = [step.action for step in plan.steps]

    assert plan.bootstrap is True
    assert plan.service_home == "/home/argos"
    assert "user" in actions
    assert "apt" in actions
    assert "uv" in actions
    assert "linger" in actions
    assert "chown" in actions
    assert "swig" in plan.os_packages
    assert "python3-dev" in plan.os_packages
    assert "build-essential" in plan.os_packages
    assert "liblgpio-dev" in plan.os_packages
    assert "chromium-browser|chromium" in plan.os_packages
    assert set(KIOSK_OS_PACKAGES).issubset(plan.os_packages)



def test_configure_kiosk_display_sets_lightdm_autologin(tmp_path, monkeypatch):
    """kiosk端末はARGOSユーザーの軽量Xセッションへ自動ログインする。"""
    captured = {}
    monkeypatch.setattr(
        "argos.installer._write_unit",
        lambda path, content, runner: captured.update(path=path, content=content),
    )
    kiosk = next(service for service in load_manifest() if service.name == "argos-dashboard-kiosk")
    plan = build_install_plan(
        [kiosk],
        project_dir=tmp_path,
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
        bootstrap=True,
    )

    _configure_kiosk_display(plan)

    assert captured["path"] == Path("/etc/lightdm/lightdm.conf.d/50-argos-kiosk.conf")
    assert "autologin-user=argos" in captured["content"]
    assert "autologin-session=openbox" in captured["content"]

def test_default_os_packages_include_runtime_and_build_dependencies():
    """標準OSパッケージに実機で必要な依存を含める。"""
    packages = set(DEFAULT_OS_PACKAGES)

    assert {
        "swig",
        "python3-dev",
        "build-essential",
        "liblgpio-dev",
        "cron",
        "curl",
        "ffmpeg",
        "fonts-ipafont-gothic",
        "fonts-ipafont-mincho",
    }.issubset(packages)
    assert "chromium-browser|chromium" in packages


def test_main_prints_json_plan(capsys, tmp_path):
    """CLIはJSON形式のdry-run計画も表示できる。"""
    result = main(
        [
            "--json",
            "--project-dir",
            str(tmp_path),
            "--system-unit-dir",
            str(tmp_path / "system"),
            "--user-unit-dir",
            str(tmp_path / "user"),
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_dir"] == str(tmp_path.resolve())
    assert any(service["name"] == "voicevox" for service in payload["services"])


def test_render_unit_template_replaces_project_and_user(tmp_path):
    """systemd unitテンプレートのプレースホルダを置換できる。"""
    template = tmp_path / "sample.service"
    template.write_text("User=@ARGOS_USER@\nGroup=@ARGOS_GROUP@\nExecStart=@PROJECT_DIR@/bin/app\n", encoding="utf-8")
    plan = build_install_plan(
        [],
        project_dir=tmp_path / "argos",
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="staff",
    )

    rendered = render_unit_template(template, plan)

    assert "User=argos" in rendered
    assert "Group=staff" in rendered
    assert f"ExecStart={tmp_path}/argos/bin/app" in rendered


def test_apply_plan_syncs_and_writes_units_without_enabling(tmp_path):
    """apply_planはuv syncとunit生成を行い、--no-enable相当ならenableしない。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    systemd_dir = project / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "argos.service").write_text("User=@ARGOS_USER@\nExecStart=@PROJECT_DIR@/.venv/bin/argos\n", encoding="utf-8")
    services = [service for service in load_manifest() if service.name == "argos"]
    plan = build_install_plan(
        services,
        project_dir=project,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append((command, kwargs.get("cwd")))

    apply_plan(plan, enable=False, runner=fake_runner)

    config_values = load_yaml_environment(project / "config.yaml")
    assert config_values["DRY_RUN"] == "true"
    assert config_values["ARGOS_DASHBOARD_TOKEN"]
    assert config_values["ARGOS_AGENT_RUNNER_TOKEN"]
    assert not (project / ".env").exists()
    assert (tmp_path / "system-units" / "argos.service").exists()
    assert any(
        command[0]
        == [
            "sudo",
            "-u",
            "argos",
            "env",
            "HOME=/home/argos",
            "PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin",
            "uv",
            "sync",
            "--extra",
            "face",
        ]
        for command in commands
    )
    assert not any("enable" in command[0] for command in commands)



def test_remove_inaccessible_venv_recreates_root_managed_environment(tmp_path):
    """root配下のPythonを指すvenvはサービスユーザーの同期前に削除する。"""
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    commands = []

    class Result:
        returncode = 1

    def fake_runner(command, **kwargs):
        commands.append(command)
        return Result()

    _remove_inaccessible_venv(tmp_path, "argos", runner=fake_runner)

    assert commands == [
        ["sudo", "-u", "argos", "test", "-x", str(venv / "bin" / "python")],
        ["sudo", "rm", "-rf", str(venv)],
    ]

def test_apply_plan_syncs_subprojects_as_service_user(tmp_path):
    """サブプロジェクトのvenvもARGOS実行ユーザーのuvで作成する。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    service_dir = project / "services" / "agent-limit"
    service_dir.mkdir(parents=True)
    (service_dir / "pyproject.toml").write_text("[project]\nname='agent-limit'\nversion='0.1.0'\n", encoding="utf-8")
    services = [service for service in load_manifest() if service.name == "agent-limit"]
    plan = build_install_plan(
        services,
        project_dir=project,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append((command, kwargs.get("cwd")))

    apply_plan(plan, enable=False, runner=fake_runner)

    assert (
        [
            "sudo",
            "-u",
            "argos",
            "env",
            "HOME=/home/argos",
            "PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin",
            "uv",
            "sync",
        ],
        service_dir,
    ) in commands


def test_update_project_pulls_as_service_user(tmp_path, monkeypatch):
    """updateはARGOS専用ユーザーでgit pullする。"""
    project = tmp_path / "argos"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setattr("argos.installer._lookup_uid", lambda user: "1234")
    plan = build_install_plan(
        [],
        project_dir=project,
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append(command)

    update_project(plan, runner=fake_runner)

    assert commands == [
        [
            "sudo",
            "-u",
            "argos",
            "env",
            "HOME=/home/argos",
            "XDG_RUNTIME_DIR=/run/user/1234",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus",
            "git",
            "-C",
            str(project),
            "pull",
            "--ff-only",
        ]
    ]


def test_apply_plan_bootstrap_runs_host_setup(tmp_path, monkeypatch):
    """bootstrap有効時はユーザー作成、apt、linger、所有者設定を実行する。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    monkeypatch.setattr("argos.installer._group_exists", lambda group: group in {"audio", "video"})
    services = []
    plan = build_install_plan(
        services,
        project_dir=project,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos-test",
        service_group="argos-test",
        bootstrap=True,
        os_packages=["alsa-utils"],
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append(command)

    apply_plan(plan, enable=False, runner=fake_runner)

    assert ["sudo", "useradd", "--create-home", "--home-dir", "/home/argos-test", "--shell", "/bin/bash", "--user-group", "argos-test"] in commands
    assert ["sudo", "apt-get", "install", "-y", "alsa-utils"] in commands
    assert ["sudo", "-u", "argos-test", "env", "HOME=/home/argos-test", "PATH=/home/argos-test/.local/bin:/home/argos-test/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin", "sh", "-lc", "command -v uv"] in commands
    assert [
        "sudo",
        "-u",
        "argos-test",
        "env",
        "HOME=/home/argos-test",
        "PATH=/home/argos-test/.local/bin:/home/argos-test/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin",
        "sh",
        "-lc",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
    ] in commands
    assert ["sudo", "loginctl", "enable-linger", "argos-test"] in commands
    assert ["sudo", "chown", "-R", "argos-test:argos-test", str(project)] in commands
    chown_index = commands.index(["sudo", "chown", "-R", "argos-test:argos-test", str(project)])
    sync_index = next(i for i, command in enumerate(commands) if "uv" in command and "sync" in command)
    assert chown_index < sync_index


def test_ensure_uv_for_user_skips_when_available():
    """uvが既に見つかる場合はインストールを省略する。"""
    commands = []

    class Result:
        """command -v uvの結果を表す。"""

        returncode = 0

    def fake_runner(command, **kwargs):
        """uv確認コマンドを記録する。"""
        commands.append(command)
        return Result()

    _ensure_uv_for_user("argos", "/home/argos", runner=fake_runner)

    assert commands == [
        [
            "sudo",
            "-u",
            "argos",
            "env",
            "HOME=/home/argos",
            "PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin",
            "sh",
            "-lc",
            "command -v uv",
        ]
    ]


def test_apply_plan_update_restarts_enabled_services(tmp_path, monkeypatch):
    """update時はunit更新後に既定有効サービスを再起動する。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    systemd_dir = project / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "argos.service").write_text("User=@ARGOS_USER@\n", encoding="utf-8")
    (systemd_dir / "argos-dashboard-kiosk.service").write_text("ExecStart=@PROJECT_DIR@/run\n", encoding="utf-8")
    monkeypatch.setattr("argos.installer._lookup_uid", lambda user: "1234")
    services = [service for service in load_manifest() if service.name in {"argos", "argos-dashboard-kiosk"}]
    plan = build_install_plan(
        services,
        project_dir=project,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append(command)

    apply_plan(plan, enable=True, restart_services=True, runner=fake_runner)

    assert ["systemctl", "restart", "argos.service"] in commands
    assert [
        "sudo",
        "-u",
        "argos",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1234",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus",
        "systemctl",
        "--user",
        "restart",
        "argos-dashboard-kiosk.service",
    ] in commands


def test_apply_plan_repairs_home_dirs_for_user_services(tmp_path, monkeypatch):
    """user serviceがある場合はChromiumなどが使うホーム内ディレクトリ所有者を補正する。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    systemd_dir = project / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "argos-dashboard-kiosk.service").write_text("ExecStart=@PROJECT_DIR@/run\n", encoding="utf-8")
    services = [service for service in load_manifest() if service.name == "argos-dashboard-kiosk"]
    plan = build_install_plan(
        services,
        project_dir=project,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
        service_home=tmp_path / "home" / "argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append(command)

    apply_plan(plan, enable=False, runner=fake_runner)

    for dirname in (".config", ".local", ".cache"):
        path = str(tmp_path / "home" / "argos" / dirname)
        assert ["sudo", "install", "-d", "-o", "argos", "-g", "argos", "-m", "700", path] in commands
        assert ["sudo", "chown", "-R", "argos:argos", path] in commands


def test_reload_systemd_uses_service_user_bus(tmp_path, monkeypatch):
    """user daemon-reloadは実行ユーザーではなくARGOS専用ユーザーのbusへ向ける。"""
    monkeypatch.setattr("argos.installer._lookup_uid", lambda user: "1234")
    plan = build_install_plan(
        [],
        project_dir=tmp_path / "argos",
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    def fake_runner(command, **kwargs):
        """外部コマンドを記録する。"""
        commands.append(command)

    _reload_systemd(plan, runner=fake_runner)

    assert ["systemctl", "daemon-reload"] in commands
    assert [
        "sudo",
        "-u",
        "argos",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1234",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus",
        "systemctl",
        "--user",
        "daemon-reload",
    ] in commands


def test_ensure_agent_limit_cron_adds_missing_entry(tmp_path):
    """agent-limitの更新cronを未登録時だけ追加する。"""
    project = tmp_path / "argos"
    updater = project / "services" / "agent-limit" / "update_limits.py"
    updater.parent.mkdir(parents=True)
    updater.write_text("print('ok')\n", encoding="utf-8")
    plan = build_install_plan(
        [],
        project_dir=project,
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
    )
    commands = []
    installed_cron = {}

    class Result:
        """crontab -lの結果を表す。"""

        stdout = "SHELL=/bin/bash\n"

    def fake_runner(command, **kwargs):
        """crontab操作を記録する。"""
        commands.append((command, kwargs))
        if command == ["sudo", "-u", "argos", "crontab", "-l"]:
            return Result()
        if command == ["sudo", "-u", "argos", "crontab", "-"]:
            installed_cron["content"] = kwargs["input"]
        return Result()

    _ensure_agent_limit_cron(plan, runner=fake_runner)

    assert AGENT_LIMIT_CRON_MARKER in installed_cron["content"]
    assert "HOME=/home/argos" in installed_cron["content"]
    assert "PATH=/home/argos/.local/bin:/home/argos/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin" in installed_cron["content"]
    assert "uv run python update_limits.py" in installed_cron["content"]
    assert "*/5 * * * *" in installed_cron["content"]
    assert commands[0][1]["capture_output"] is True
    assert commands[1][0] == ["sudo", "-u", "argos", "crontab", "-"]
    assert commands[1][1]["text"] is True


def test_ensure_agent_limit_cron_skips_existing_entry(tmp_path):
    """cron登録済みなら二重登録しない。"""
    project = tmp_path / "argos"
    updater = project / "services" / "agent-limit" / "update_limits.py"
    updater.parent.mkdir(parents=True)
    updater.write_text("print('ok')\n", encoding="utf-8")
    plan = build_install_plan(
        [],
        project_dir=project,
        system_unit_dir=tmp_path / "system",
        user_unit_dir=tmp_path / "user",
        service_user="argos",
        service_group="argos",
    )
    commands = []

    class Result:
        """crontab -lの結果を表す。"""

        stdout = f"{AGENT_LIMIT_CRON_MARKER}\n"

    def fake_runner(command, **kwargs):
        """crontab操作を記録する。"""
        commands.append(command)
        return Result()

    _ensure_agent_limit_cron(plan, runner=fake_runner)

    assert commands == [["sudo", "-u", "argos", "crontab", "-l"]]


def test_install_chromium_policy_installs_to_common_paths(tmp_path):
    """Chromium管理ポリシーをUbuntuとRaspberry Pi OS向けの両方へ配置する。"""
    project = tmp_path / "argos"
    policy = project / "chromium" / "argos-dashboard.json"
    policy.parent.mkdir(parents=True)
    policy.write_text('{"TranslateEnabled": false}\n', encoding="utf-8")
    commands = []

    def fake_runner(command, **_kwargs):
        """installコマンドを記録する。"""
        commands.append(command)

    _install_chromium_policy(project, runner=fake_runner)

    for target in CHROMIUM_POLICY_TARGETS:
        assert ["sudo", "install", "-D", "-m", "644", str(policy), str(target)] in commands


def test_resolve_os_packages_selects_available_chromium_package():
    """Ubuntu/Raspberry Pi OSで異なるChromiumパッケージ名を吸収する。"""

    class Result:
        """apt-cacheの結果を表す簡易オブジェクト。"""

        def __init__(self, returncode):
            """戻り値コードを保持する。"""
            self.returncode = returncode

    def fake_runner(command, **kwargs):
        """chromium-browserは無く、chromiumだけある環境を再現する。"""
        package = command[-1]
        return Result(0 if package == "chromium" else 100)

    assert _resolve_os_packages(["alsa-utils", "chromium-browser|chromium"], runner=fake_runner) == [
        "alsa-utils",
        "chromium",
    ]


def test_configure_config_updates_urls_tokens_ssl_and_audio_devices(tmp_path, monkeypatch):
    """対話式設定で接続先、認証、HTTPS、音声デバイスをYAMLへ反映できる。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment(
        {
            "STT_GATEWAY_URL": "",
            "VOICEVOX_URL": "http://localhost:50021",
            "VOICEVOX_BEARER_TOKEN": "",
            "OSRM_URL": "",
            "ARGOS_REMOTE_LOCATION_URL": "",
            "ARGOS_WAKEWORD_ENABLED": "false",
            "ARGOS_AGENT_RUNNER_URL": "",
            "ARGOS_PTT_GPIO": "17",
            "AUDIO_INPUT_DEVICES": "default",
            "AUDIO_OUTPUT_DEVICE": "default",
            "ARGOS_DASHBOARD_TOKEN": "",
        },
        config_path,
    )
    answers = iter(
        [
            "http://stt.local:23000",
            "stt-token",
            "",
            "voice-token",
            "http://router.local:5000",
            "http://gps.local:8080/gps",
            "y",
            "y",
            "1",
            "y",
            "y",
            "",
            "-",
            "2",
            "hw:CARD=Speaker,DEV=0",
        ]
    )

    class Result:
        """外部コマンドの結果を表す簡易オブジェクト。"""

        returncode = 0

        def __init__(self, stdout):
            """標準出力を保持する。"""
            self.stdout = stdout

    def fake_runner(command, **kwargs):
        """arecord/aplayの候補を返す。"""
        if command[0] == "arecord":
            return Result("default\nplughw:CARD=Mic,DEV=0\n  説明行\n")
        return Result("default\nplughw:CARD=Speaker,DEV=0\n")

    monkeypatch.setattr("argos.installer.getpass.getpass", lambda _prompt: next(answers))

    configure_config(
        config_path,
        runner=fake_runner,
        input_func=lambda _prompt: next(answers),
        output_func=lambda _message: None,
    )

    values = load_yaml_environment(config_path)
    assert values["STT_GATEWAY_URL"] == "http://stt.local:23000"
    assert values["STT_GATEWAY_BEARER_TOKEN"] == "stt-token"
    assert values["VOICEVOX_URL"] == "http://localhost:50021"
    assert values["VOICEVOX_BEARER_TOKEN"] == "voice-token"
    assert values["OSRM_URL"] == "http://router.local:5000"
    assert values["ARGOS_REMOTE_LOCATION_URL"] == "http://gps.local:8080/gps"
    assert values["ARGOS_WAKEWORD_ENABLED"] == "true"
    assert values["ARGOS_WINDOW_LAYOUT_ANDROID_APP"] == "maps"
    assert values["ARGOS_WINDOW_LAYOUT_STYLE"] == "overlay"
    assert values["ARGOS_AGENT_RUNNER_URL"] == "http://127.0.0.1:28765"
    assert values["ARGOS_DASHBOARD_SSL"] == "true"
    assert values["ARGOS_PTT_GPIO"] == ""
    assert values["AUDIO_INPUT_DEVICES"] == "plughw:CARD=Mic,DEV=0"
    assert values["AUDIO_OUTPUT_DEVICE"] == "hw:CARD=Speaker,DEV=0"
    assert values["ARGOS_DASHBOARD_TOKEN"]


def test_configure_config_sets_agent_slots_from_selected_providers(tmp_path, monkeypatch):
    """対話式設定で利用providerからスロットを生成できる。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment(
        dict(
            line.split("=", 1)
            for line in [
                "ARGOS_AGENT_PROVIDER=codex",
                "ARGOS_AGENT_CWD=/home/argos",
                "ARGOS_AGENT_SLOT_1=デフォルト,codex,/home/argos,2",
                "ARGOS_AGENT_SLOT_2=アンチグラビティ,antigravity,/home/argos,51",
                "ARGOS_AGENT_SLOT_3=調査,codex,/home/argos,21",
                "ARGOS_AGENT_SLOT_4=クロード,claude,/home/argos,8",
                "ARGOS_DASHBOARD_TOKEN=token",
                "STT_GATEWAY_URL=",
                "VOICEVOX_URL=",
                "VOICEVOX_BEARER_TOKEN=",
                "OSRM_URL=",
                "ARGOS_REMOTE_LOCATION_URL=",
                "ARGOS_WAKEWORD_ENABLED=false",
                "ARGOS_AGENT_RUNNER_URL=",
                "ARGOS_PTT_GPIO=",
                "AUDIO_INPUT_DEVICES=default",
                "AUDIO_OUTPUT_DEVICE=default",
            ]
        ),
        config_path,
    )
    answers = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "codex,claude",
            "作業",
            "/opt/argos",
            "2",
            "gpt-test",
            "Claude",
            "/home/argos",
            "-",
            "sonnet",
            "",
            "",
            "",
        ]
    )

    class Result:
        """ALSA候補なしの結果を表す。"""

        returncode = 1
        stdout = ""

    monkeypatch.setattr("argos.installer.getpass.getpass", lambda _prompt: next(answers))

    configure_config(
        config_path,
        runner=lambda _command, **_kwargs: Result(),
        input_func=lambda _prompt: next(answers),
        output_func=lambda _message: None,
    )

    values = load_yaml_environment(config_path)
    slots = json.loads(values["ARGOS_AGENT_SLOTS_JSON"])
    assert values["ARGOS_AGENT_PROVIDER"] == "codex"
    assert slots[0] == {
        "type": "local",
        "name": "作業",
        "provider": "codex",
        "cwd": "/opt/argos",
        "voicevox_speaker": 2,
        "model": "gpt-test",
    }
    assert slots[1] == {
        "type": "local",
        "name": "Claude",
        "provider": "claude",
        "cwd": "/home/argos",
        "model": "sonnet",
    }


def test_ensure_core_config_defaults_generates_dashboard_token(tmp_path):
    """共通設定のダッシュボードトークンが空なら自動生成する。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_DASHBOARD_TOKEN": "", "ARGOS_DASHBOARD_ENABLED": "true"}, config_path)

    _ensure_core_config_defaults(config_path)

    values = load_yaml_environment(config_path)
    assert values["ARGOS_DASHBOARD_TOKEN"]


def test_ensure_core_config_defaults_generates_agent_runner_token(tmp_path):
    """共通設定のAgent Runnerトークンが空なら自動生成する。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_AGENT_RUNNER_TOKEN": "", "ARGOS_DASHBOARD_TOKEN": "token"}, config_path)

    _ensure_core_config_defaults(config_path)

    values = load_yaml_environment(config_path)
    assert values["ARGOS_AGENT_RUNNER_TOKEN"]


def test_ensure_core_config_defaults_restricts_permissions(tmp_path):
    """トークンを含む共通設定は所有者だけが読める権限へ変更する。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_DASHBOARD_TOKEN": "token"}, config_path)
    config_path.chmod(0o644)

    _ensure_core_config_defaults(config_path)

    assert config_path.stat().st_mode & 0o777 == 0o600


def test_ensure_tts_filter_shared_token_generates_and_syncs(tmp_path):
    """本体とtts-filterのBearerトークンを同じ値に揃える。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "tts-filter"
    service_dir.mkdir(parents=True)
    config_path = project / "config.yaml"
    filter_env = service_dir / ".env"
    write_yaml_from_environment(
        {"TTS_FILTER_URL": "http://127.0.0.1:9191", "TTS_FILTER_BEARER_TOKEN": ""},
        config_path,
    )
    filter_env.write_text("TTS_FILTER_BEARER_TOKEN=change-me\nTTS_FILTER_CONFIG=src/tts_filter/dictionary.yml\n", encoding="utf-8")

    assert _ensure_tts_filter_shared_token(project) is True

    app_values = load_yaml_environment(config_path)
    filter_values = dict(line.split("=", 1) for line in filter_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert app_values["TTS_FILTER_BEARER_TOKEN"]
    assert app_values["TTS_FILTER_BEARER_TOKEN"] != "change-me"
    assert app_values["TTS_FILTER_BEARER_TOKEN"] == filter_values["TTS_FILTER_BEARER_TOKEN"]


def test_ensure_tts_filter_shared_token_prefers_app_token(tmp_path):
    """本体側に実トークンがある場合はtts-filter側へ反映する。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "tts-filter"
    service_dir.mkdir(parents=True)
    config_path = project / "config.yaml"
    filter_env = service_dir / ".env"
    write_yaml_from_environment({"TTS_FILTER_BEARER_TOKEN": "app-token"}, config_path)
    filter_env.write_text("TTS_FILTER_BEARER_TOKEN=service-token\n", encoding="utf-8")

    assert _ensure_tts_filter_shared_token(project) is True

    filter_values = dict(line.split("=", 1) for line in filter_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert filter_values["TTS_FILTER_BEARER_TOKEN"] == "app-token"


def test_ensure_reminder_dashboard_token_syncs_from_app_env(tmp_path):
    """本体のダッシュボードトークンをargos-reminder側へ反映する。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "argos-reminder"
    service_dir.mkdir(parents=True)
    config_path = project / "config.yaml"
    reminder_env = service_dir / ".env"
    write_yaml_from_environment({"ARGOS_DASHBOARD_TOKEN": "dashboard-token"}, config_path)
    reminder_env.write_text(
        "ARGOS_DASHBOARD_URL=http://127.0.0.1:8765\nARGOS_DASHBOARD_TOKEN=\n",
        encoding="utf-8",
    )

    assert _ensure_reminder_dashboard_token(project) is True

    reminder_values = dict(line.split("=", 1) for line in reminder_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert reminder_values["ARGOS_DASHBOARD_TOKEN"] == "dashboard-token"


def test_ensure_reminder_dashboard_token_generates_shared_token(tmp_path):
    """本体とargos-reminderのトークンが空なら共有トークンを生成する。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "argos-reminder"
    service_dir.mkdir(parents=True)
    config_path = project / "config.yaml"
    reminder_env = service_dir / ".env"
    write_yaml_from_environment({"ARGOS_DASHBOARD_TOKEN": ""}, config_path)
    reminder_env.write_text("ARGOS_DASHBOARD_TOKEN=\n", encoding="utf-8")

    assert _ensure_reminder_dashboard_token(project) is True

    app_values = load_yaml_environment(config_path)
    reminder_values = dict(line.split("=", 1) for line in reminder_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert app_values["ARGOS_DASHBOARD_TOKEN"]
    assert app_values["ARGOS_DASHBOARD_TOKEN"] == reminder_values["ARGOS_DASHBOARD_TOKEN"]


def _window_layout_plan(tmp_path):
    """画面配置サービスを含む計画を作る。"""
    services = [service for service in load_manifest() if service.name in ("argos-dashboard-kiosk", "argos-window-layout")]
    return build_install_plan(
        services,
        project_dir=tmp_path,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
        service_home=tmp_path / "home",
    )


def test_window_layout_service_is_opt_in():
    """画面配置サービスは任意扱いで、既定では有効化されない。"""
    service = next(item for item in load_manifest() if item.name == "argos-window-layout")
    assert (service.kind, service.bundle, service.enabled_by_default) == ("user", "optional", False)


def test_window_layout_unit_is_generic_and_follows_kiosk():
    """unitに端末固有の名前を含めず、キオスクの再起動へ追従する。"""
    text = (Path(__file__).resolve().parents[1] / "systemd/argos-window-layout.service").read_text(encoding="utf-8")
    assert "PartOf=argos-dashboard-kiosk.service" in text
    assert "waydroid-" not in text
    assert "window_layout boot" in text


def test_apply_plan_installs_window_layout_unit_without_enabling(tmp_path):
    """質問なしのインストールでは、unitを置くだけで有効化も無効化もしない。"""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (tmp_path / "config.yaml.example").write_text("runtime:\n  dry_run: true\n", encoding="utf-8")
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd/argos-window-layout.service").write_text("ExecStart=@PROJECT_DIR@/.venv/bin/python\n", encoding="utf-8")
    (tmp_path / "systemd/argos-dashboard-kiosk.service").write_text("Description=kiosk\n", encoding="utf-8")
    commands = []
    apply_plan(_window_layout_plan(tmp_path), runner=lambda command, **kwargs: commands.append(command))
    assert (tmp_path / "user-units/argos-window-layout.service").exists()
    assert not any("argos-window-layout.service" in command for command in commands)


@pytest.mark.parametrize("answer, action", [("maps", "enable"), ("", "disable")])
def test_window_layout_choice_switches_service(tmp_path, monkeypatch, answer, action):
    """回答に応じて配置復元サービスを有効化または無効化する。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": answer}, config_path)
    monkeypatch.setattr("argos.installer.shutil.which", lambda tool: None)
    commands, messages = [], []
    installer._apply_window_layout_choice(
        _window_layout_plan(tmp_path),
        config_path,
        runner=lambda command, **kwargs: commands.append(command),
        output_func=messages.append,
    )
    assert commands[-1][-3:] == [action, "--now", "argos-window-layout.service"]
    assert bool(messages) == bool(answer)


def test_window_layout_choice_ignored_without_service(tmp_path):
    """マニフェストに含まれない構成では何もしない。"""
    plan = build_install_plan(
        [service for service in load_manifest() if service.name == "argos"],
        project_dir=tmp_path,
        system_unit_dir=tmp_path / "s",
        user_unit_dir=tmp_path / "u",
        service_user="argos",
        service_group="argos",
    )
    commands = []
    installer._apply_window_layout_choice(plan, tmp_path / "config.yaml", runner=lambda command, **kwargs: commands.append(command))
    assert commands == []


TOUCH_CONFIG = """<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
	<!-- 保持するコメント -->
	<touch deviceName="ILITEK" mapToOutput="HDMI-A-1" mouseEmulation="yes"/>
</openbox_config>
"""


def _labwc_config(tmp_path, text):
    """サービスユーザーのrc.xmlを作って返す。"""
    config = tmp_path / "home/.config/labwc/rc.xml"
    config.parent.mkdir(parents=True)
    config.write_text(text, encoding="utf-8")
    return config


def test_enable_multitouch_flips_emulation_with_backup(tmp_path):
    """mouseEmulationがyesなら、バックアップを残してnoへ変え、labwcへ再読み込みを通知する。"""
    config = _labwc_config(tmp_path, TOUCH_CONFIG)
    commands, messages = [], []
    installer._enable_multitouch(_window_layout_plan(tmp_path), runner=lambda command, **kwargs: commands.append(command), output_func=messages.append)
    text = config.read_text(encoding="utf-8")
    assert 'mouseEmulation="no"' in text and 'mouseEmulation="yes"' not in text
    assert "<!-- 保持するコメント -->" in text and 'deviceName="ILITEK"' in text
    assert config.with_name("rc.xml.before-argos-touch").read_text(encoding="utf-8") == TOUCH_CONFIG
    assert commands == [["pkill", "-HUP", "-u", "argos", "-x", "labwc"]]
    assert messages and "before-argos-touch" in messages[0]


def test_enable_multitouch_keeps_first_backup(tmp_path):
    """既存のバックアップは上書きしない。"""
    config = _labwc_config(tmp_path, TOUCH_CONFIG)
    backup = config.with_name("rc.xml.before-argos-touch")
    backup.write_text("最初の設定", encoding="utf-8")
    installer._enable_multitouch(_window_layout_plan(tmp_path), runner=lambda command, **kwargs: None, output_func=lambda message: None)
    assert backup.read_text(encoding="utf-8") == "最初の設定"


@pytest.mark.parametrize(
    "text",
    [
        TOUCH_CONFIG.replace('"yes"', '"no"'),
        '<openbox_config><touch deviceName="x"/></openbox_config>',
        "<openbox_config><keyboard/></openbox_config>",
    ],
)
def test_enable_multitouch_leaves_other_configs(tmp_path, text):
    """yesが明示されていない設定は変更せず、バックアップも作らない。"""
    config = _labwc_config(tmp_path, text)
    commands = []
    installer._enable_multitouch(_window_layout_plan(tmp_path), runner=lambda command, **kwargs: commands.append(command), output_func=lambda message: None)
    assert config.read_text(encoding="utf-8") == text
    assert not config.with_name("rc.xml.before-argos-touch").exists()
    assert commands == []


def test_enable_multitouch_without_config_or_broken_xml(tmp_path):
    """rc.xmlがない端末は何もせず、壊れたXMLは変更せず警告する。"""
    plan = _window_layout_plan(tmp_path)
    installer._enable_multitouch(plan, runner=lambda command, **kwargs: None, output_func=lambda message: None)
    config = _labwc_config(tmp_path, "<openbox_config><touch")
    messages = []
    installer._enable_multitouch(plan, runner=lambda command, **kwargs: None, output_func=messages.append)
    assert config.read_text(encoding="utf-8") == "<openbox_config><touch"
    assert messages and "解析できない" in messages[0]


def test_window_layout_choice_yes_enables_multitouch_only(tmp_path, monkeypatch):
    """画面分割にyと答えたときだけタッチ設定を変え、nでは変えない。"""
    config = _labwc_config(tmp_path, TOUCH_CONFIG)
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr("argos.installer.shutil.which", lambda tool: f"/usr/bin/{tool}")
    plan = _window_layout_plan(tmp_path)
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": ""}, config_path)
    installer._apply_window_layout_choice(plan, config_path, runner=lambda command, **kwargs: None, output_func=lambda message: None)
    assert config.read_text(encoding="utf-8") == TOUCH_CONFIG
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": "maps"}, config_path)
    installer._apply_window_layout_choice(plan, config_path, runner=lambda command, **kwargs: None, output_func=lambda message: None)
    assert 'mouseEmulation="no"' in config.read_text(encoding="utf-8")


def test_window_layout_choice_disable_failure_does_not_stop_install(tmp_path):
    """画面分割を使わない端末での無効化に失敗しても、インストールは続ける。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": ""}, config_path)
    messages = []

    def fail(command, **kwargs):
        """ユーザーのsystemdに接続できない状態を模擬する。"""
        raise subprocess.CalledProcessError(1, command)

    installer._apply_window_layout_choice(_window_layout_plan(tmp_path), config_path, runner=fail, output_func=messages.append)
    assert messages and "無効化できませんでした" in messages[0]


@pytest.mark.parametrize(
    "answer, current, expected",
    [("1", "", "overlay"), ("overlay", "split", "overlay"), ("2", "overlay", "split"), ("SPLIT", "", "split"), ("", "overlay", "overlay"), ("x", "overlay", "overlay"), ("", "", "")],
)
def test_ask_layout_style(answer, current, expected):
    """表示方式は番号か名前で選べ、空入力や不明な入力なら現在値を保つ。"""
    values = {installer.WINDOW_LAYOUT_STYLE_KEY: current} if current else {}
    installer._ask_layout_style(values, input_func=lambda prompt: answer)
    assert values.get(installer.WINDOW_LAYOUT_STYLE_KEY, "") == expected


@pytest.mark.parametrize("layout_answer, asked", [("y", 1), ("n", 0), ("", 0)])
def test_configure_asks_style_only_when_layout_is_used(tmp_path, monkeypatch, layout_answer, asked):
    """表示方式の質問は、Waydroidと並べて使うと答えたときだけ出す。"""
    monkeypatch.setattr("argos.installer.getpass.getpass", lambda _prompt: "")
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_DASHBOARD_TOKEN": "token", "AUDIO_INPUT_DEVICES": "default", "AUDIO_OUTPUT_DEVICE": "default"}, config_path)
    prompts = []

    def answer(prompt):
        """質問文を記録し、画面配置の質問だけに答える。"""
        prompts.append(prompt)
        return layout_answer if "並べて使う" in prompt else ""

    installer.configure_config(config_path, runner=lambda command, **kwargs: SimpleNamespace(returncode=1, stdout=""), input_func=answer, output_func=lambda message: None)
    assert sum("表示方式" in prompt for prompt in prompts) == asked


@pytest.mark.parametrize(
    "layout, before, after",
    [
        ("maps", "", "Waydroid"),
        ("maps", "Waydroid", "Waydroid"),
        ("maps", "waydroid", "waydroid"),
        ("maps", "Other", "Other,Waydroid"),
        ("", "Waydroid", ""),
        ("", "Other,Waydroid", "Other"),
        ("", "Other", "Other"),
        ("", "", ""),
    ],
)
def test_sync_waydroid_audio_priority(layout, before, after):
    """Waydroidと並べて使うときだけ、ARGOSの発話をWaydroidの音声に譲る設定にする。他のアプリ名は保つ。"""
    values = {installer.WINDOW_LAYOUT_KEY: layout, installer.YIELD_TO_APPS_KEY: before}
    installer._sync_waydroid_audio_priority(values)
    assert values[installer.YIELD_TO_APPS_KEY] == after


def _watchdog_plan(tmp_path):
    """画面配置と見張りの両方を含む計画を作る。"""
    names = ("argos-dashboard-kiosk", "argos-window-layout", "argos-waydroid-watchdog")
    return build_install_plan(
        [service for service in load_manifest() if service.name in names],
        project_dir=tmp_path,
        system_unit_dir=tmp_path / "system-units",
        user_unit_dir=tmp_path / "user-units",
        service_user="argos",
        service_group="argos",
        service_home=tmp_path / "home",
    )


def test_watchdog_service_is_opt_in_and_generic():
    """見張りは任意サービスで既定では有効化されず、unitに端末固有の名前を含めない。"""
    service = next(item for item in load_manifest() if item.name == "argos-waydroid-watchdog")
    assert (service.kind, service.bundle, service.enabled_by_default) == ("user", "optional", False)
    text = (Path(__file__).resolve().parents[1] / "systemd/argos-waydroid-watchdog.service").read_text(encoding="utf-8")
    assert "argos.tools.waydroid_watchdog" in text and "Restart=always" in text and "waydroid-gps" not in text


@pytest.mark.parametrize("answer, action", [("maps", "enable"), ("", "disable")])
def test_watchdog_follows_window_layout_choice(tmp_path, monkeypatch, answer, action):
    """Waydroidと並べて使うなら見張りも有効化し、使わないなら無効化する。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": answer}, config_path)
    monkeypatch.setattr("argos.installer.shutil.which", lambda tool: f"/usr/bin/{tool}")
    commands = []
    installer._apply_window_layout_choice(_watchdog_plan(tmp_path), config_path, runner=lambda command, **kwargs: commands.append(command), output_func=lambda message: None)
    units = [command[-1] for command in commands if action in command]
    assert units == ["argos-waydroid-watchdog.service", "argos-window-layout.service"]


def test_watchdog_disable_failure_does_not_skip_the_rest(tmp_path):
    """見張りの無効化に失敗しても、警告だけ出して、画面配置の無効化へ進む。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": ""}, config_path)
    seen, messages = [], []

    def runner(command, **kwargs):
        """見張りの無効化だけ失敗させる。"""
        seen.append(command[-1])
        if command[-1] == "argos-waydroid-watchdog.service":
            raise subprocess.CalledProcessError(1, command)

    installer._apply_window_layout_choice(_watchdog_plan(tmp_path), config_path, runner=runner, output_func=messages.append)
    assert seen == ["argos-waydroid-watchdog.service", "argos-window-layout.service"]
    assert len(messages) == 1 and "argos-waydroid-watchdog.service" in messages[0]


def test_quiet_lxc_sudoers_is_checked_then_installed():
    """lxc-attachの記録を残さない設定は、visudoで確認してから、root所有の0440で置く。"""
    commands, contents = [], []

    def runner(command, **kwargs):
        """実行したコマンドと、確認した一時ファイルの中身を記録する。"""
        commands.append(command)
        if command[:3] == ["sudo", "visudo", "-cf"]:
            contents.append(Path(command[3]).read_text(encoding="utf-8"))

    installer._install_quiet_lxc_sudoers(runner=runner, output_func=lambda message: None)
    assert commands[0][:3] == ["sudo", "visudo", "-cf"]
    assert commands[1][:8] == ["sudo", "install", "-m", "0440", "-o", "root", "-g", "root"]
    assert commands[1][-1] == "/etc/sudoers.d/argos-lxc-attach"
    assert "Defaults!/usr/bin/lxc-attach !log_allowed, !pam_session" in contents[0]
    assert not Path(commands[0][3]).exists()


def test_quiet_lxc_sudoers_failure_only_warns():
    """visudoの確認に失敗したら、置かずに警告だけ出す。"""
    commands, messages = [], []

    def runner(command, **kwargs):
        """visudoの確認を失敗させる。"""
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    installer._install_quiet_lxc_sudoers(runner=runner, output_func=messages.append)
    assert len(commands) == 1
    assert messages and "argos-lxc-attach" in messages[0]


@pytest.mark.parametrize("answer, expected", [("maps", True), ("", False)])
def test_quiet_lxc_sudoers_follows_window_layout_choice(tmp_path, monkeypatch, answer, expected):
    """Waydroidと並べて使うと答えたときだけ、lxc-attachの記録を残さない設定を置く。"""
    config_path = tmp_path / "config.yaml"
    write_yaml_from_environment({"ARGOS_WINDOW_LAYOUT_ANDROID_APP": answer}, config_path)
    monkeypatch.setattr("argos.installer.shutil.which", lambda tool: f"/usr/bin/{tool}")
    commands = []
    installer._apply_window_layout_choice(
        _watchdog_plan(tmp_path), config_path, runner=lambda command, **kwargs: commands.append(command), output_func=lambda message: None
    )
    assert any(command[:2] == ["sudo", "visudo"] for command in commands) is expected
