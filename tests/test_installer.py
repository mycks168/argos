import json

import pytest

from argos.installer import (
    DEFAULT_OS_PACKAGES,
    DEFAULT_MANIFEST,
    apply_plan,
    build_install_plan,
    configure_env,
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
    _ensure_core_env_defaults,
    _ensure_reminder_dashboard_token,
    _ensure_tts_filter_shared_token,
    _install_chromium_policy,
    _merge_yaml_into_compat_env,
    _prepare_unified_slots_for_configure,
    _reload_systemd,
    _restore_unified_slots_after_configure,
    _sync_config_yaml,
)


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


def test_sync_config_yaml_migrates_all_env_values(tmp_path):
    """インストーラーは既存.envを階層YAMLへ欠落なく同期する。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ARGOS_DASHBOARD_PORT=8765\n"
        "ARGOS_AGENT_SLOT_1=作業,codex,/opt/argos,2,gpt-test\n"
        "CUSTOM_SECRET=secret\n",
        encoding="utf-8",
    )

    _sync_config_yaml(tmp_path)

    from argos.yaml_config import load_yaml_environment

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


def test_sync_config_yaml_preserves_existing_file_without_overwrite(tmp_path):
    """通常更新では利用者が編集したconfig.yamlを上書きしない。"""
    (tmp_path / ".env").write_text("ARGOS_DASHBOARD_PORT=8765\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  port: 9999\n", encoding="utf-8")

    _sync_config_yaml(tmp_path)

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


def test_merge_yaml_into_compat_env_uses_yaml_as_configure_base(tmp_path):
    """対話設定時は古い.envより既存YAMLを優先する。"""
    env_path = tmp_path / ".env"
    env_path.write_text("ARGOS_DASHBOARD_PORT=8765\nLEGACY_VALUE=keep\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("dashboard:\n  port: 9999\n", encoding="utf-8")

    _merge_yaml_into_compat_env(tmp_path)

    values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines())
    assert values["ARGOS_DASHBOARD_PORT"] == "9999"
    assert values["LEGACY_VALUE"] == "keep"


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
        service_user="yuki",
        service_group="staff",
    )
    payload = plan_to_dict(plan)

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "argos-reminder" in encoded
    assert payload["service_user"] == "yuki"
    assert payload["service_group"] == "staff"
    assert payload["service_home"] == "/home/yuki"


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
            "yuki",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "ARGOSインストール計画" in output
    assert "tts-filter" in output
    assert "service_user: yuki" in output


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
    (project / ".env.example").write_text("DRY_RUN=true\n", encoding="utf-8")
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

    env_text = (project / ".env").read_text(encoding="utf-8")
    assert "DRY_RUN=true" in env_text
    assert "ARGOS_DASHBOARD_TOKEN=" in env_text
    assert "ARGOS_DASHBOARD_TOKEN=\n" not in env_text
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


def test_apply_plan_syncs_subprojects_as_service_user(tmp_path):
    """サブプロジェクトのvenvもARGOS実行ユーザーのuvで作成する。"""
    project = tmp_path / "argos"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='argos'\nversion='0.1.0'\n", encoding="utf-8")
    (project / ".env.example").write_text("DRY_RUN=true\n", encoding="utf-8")
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
    (project / ".env.example").write_text("DRY_RUN=true\n", encoding="utf-8")
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
    (project / ".env.example").write_text("DRY_RUN=true\n", encoding="utf-8")
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
    (project / ".env.example").write_text("DRY_RUN=true\n", encoding="utf-8")
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


def test_configure_env_updates_urls_and_audio_devices(tmp_path):
    """対話式設定でURLと音声デバイスを.envへ反映できる。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "STT_GATEWAY_URL=",
                "VOICEVOX_URL=http://localhost:50021",
                "VOICEVOX_BEARER_TOKEN=",
                "OSRM_URL=",
                "ARGOS_REMOTE_LOCATION_URL=",
                "ARGOS_WAKEWORD_ENABLED=false",
                "ARGOS_AGENT_RUNNER_URL=",
                "ARGOS_PTT_GPIO=17",
                "AUDIO_INPUT_DEVICES=default",
                "AUDIO_OUTPUT_DEVICE=default",
                "ARGOS_DASHBOARD_TOKEN=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    answers = iter(
        [
            "http://stt.local:23000",
            "",
            "voice-token",
            "http://router.local:5000",
            "http://gps.local:8080/gps",
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

    configure_env(
        env_path,
        runner=fake_runner,
        input_func=lambda _prompt: next(answers),
        output_func=lambda _message: None,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "STT_GATEWAY_URL=http://stt.local:23000" in text
    assert "VOICEVOX_URL=http://localhost:50021" in text
    assert "VOICEVOX_BEARER_TOKEN=voice-token" in text
    assert "OSRM_URL=http://router.local:5000" in text
    assert "ARGOS_REMOTE_LOCATION_URL=http://gps.local:8080/gps" in text
    assert "ARGOS_WAKEWORD_ENABLED=true" in text
    assert "ARGOS_AGENT_RUNNER_URL=http://127.0.0.1:28765" in text
    assert "ARGOS_PTT_GPIO=" in text
    assert "AUDIO_INPUT_DEVICES=plughw:CARD=Mic,DEV=0" in text
    assert "AUDIO_OUTPUT_DEVICE=hw:CARD=Speaker,DEV=0" in text
    assert "ARGOS_DASHBOARD_TOKEN=" in text
    assert "ARGOS_DASHBOARD_TOKEN=\n" not in text


def test_configure_env_sets_agent_slots_from_selected_providers(tmp_path):
    """対話式設定で利用providerからスロットを生成できる。"""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
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
        )
        + "\n",
        encoding="utf-8",
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

    configure_env(
        env_path,
        runner=lambda _command, **_kwargs: Result(),
        input_func=lambda _prompt: next(answers),
        output_func=lambda _message: None,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "ARGOS_AGENT_PROVIDER=codex" in text
    assert "ARGOS_AGENT_SLOT_1=作業,codex,/opt/argos,2,gpt-test" in text
    assert "ARGOS_AGENT_SLOT_2=Claude,claude,/home/argos,,sonnet" in text
    assert "ARGOS_AGENT_SLOT_3=\n" in text
    assert "ARGOS_AGENT_SLOT_4=\n" in text


def test_ensure_core_env_defaults_generates_dashboard_token(tmp_path):
    """既存.envのダッシュボードトークンが空なら自動生成する。"""
    env_path = tmp_path / ".env"
    env_path.write_text("ARGOS_DASHBOARD_TOKEN=\nARGOS_DASHBOARD_ENABLED=true\n", encoding="utf-8")

    _ensure_core_env_defaults(env_path)

    values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert values["ARGOS_DASHBOARD_TOKEN"]


def test_ensure_core_env_defaults_generates_agent_runner_token(tmp_path):
    """既存.envのAgent Runnerトークンが空なら自動生成する。"""
    env_path = tmp_path / ".env"
    env_path.write_text("ARGOS_AGENT_RUNNER_TOKEN=\nARGOS_DASHBOARD_TOKEN=token\n", encoding="utf-8")

    _ensure_core_env_defaults(env_path)

    values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert values["ARGOS_AGENT_RUNNER_TOKEN"]


def test_ensure_core_env_defaults_restricts_env_permissions(tmp_path):
    """トークンを含む.envは所有者のみ読める権限へ変更する。"""
    env_path = tmp_path / ".env"
    env_path.write_text("ARGOS_DASHBOARD_TOKEN=token\n", encoding="utf-8")
    env_path.chmod(0o644)

    _ensure_core_env_defaults(env_path)

    assert env_path.stat().st_mode & 0o777 == 0o600


def test_ensure_tts_filter_shared_token_generates_and_syncs(tmp_path):
    """本体とtts-filterのBearerトークンを同じ値に揃える。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "tts-filter"
    service_dir.mkdir(parents=True)
    app_env = project / ".env"
    filter_env = service_dir / ".env"
    app_env.write_text("TTS_FILTER_URL=http://127.0.0.1:9191\nTTS_FILTER_BEARER_TOKEN=\n", encoding="utf-8")
    filter_env.write_text("TTS_FILTER_BEARER_TOKEN=change-me\nTTS_FILTER_CONFIG=src/tts_filter/dictionary.yml\n", encoding="utf-8")

    assert _ensure_tts_filter_shared_token(project) is True

    app_values = dict(line.split("=", 1) for line in app_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    filter_values = dict(line.split("=", 1) for line in filter_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert app_values["TTS_FILTER_BEARER_TOKEN"]
    assert app_values["TTS_FILTER_BEARER_TOKEN"] != "change-me"
    assert app_values["TTS_FILTER_BEARER_TOKEN"] == filter_values["TTS_FILTER_BEARER_TOKEN"]


def test_ensure_tts_filter_shared_token_prefers_app_token(tmp_path):
    """本体側に実トークンがある場合はtts-filter側へ反映する。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "tts-filter"
    service_dir.mkdir(parents=True)
    app_env = project / ".env"
    filter_env = service_dir / ".env"
    app_env.write_text("TTS_FILTER_BEARER_TOKEN=app-token\n", encoding="utf-8")
    filter_env.write_text("TTS_FILTER_BEARER_TOKEN=service-token\n", encoding="utf-8")

    assert _ensure_tts_filter_shared_token(project) is True

    filter_values = dict(line.split("=", 1) for line in filter_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert filter_values["TTS_FILTER_BEARER_TOKEN"] == "app-token"


def test_ensure_reminder_dashboard_token_syncs_from_app_env(tmp_path):
    """本体のダッシュボードトークンをargos-reminder側へ反映する。"""
    project = tmp_path / "argos"
    service_dir = project / "services" / "argos-reminder"
    service_dir.mkdir(parents=True)
    app_env = project / ".env"
    reminder_env = service_dir / ".env"
    app_env.write_text("ARGOS_DASHBOARD_TOKEN=dashboard-token\n", encoding="utf-8")
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
    app_env = project / ".env"
    reminder_env = service_dir / ".env"
    app_env.write_text("ARGOS_DASHBOARD_TOKEN=\n", encoding="utf-8")
    reminder_env.write_text("ARGOS_DASHBOARD_TOKEN=\n", encoding="utf-8")

    assert _ensure_reminder_dashboard_token(project) is True

    app_values = dict(line.split("=", 1) for line in app_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    reminder_values = dict(line.split("=", 1) for line in reminder_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert app_values["ARGOS_DASHBOARD_TOKEN"]
    assert app_values["ARGOS_DASHBOARD_TOKEN"] == reminder_values["ARGOS_DASHBOARD_TOKEN"]
