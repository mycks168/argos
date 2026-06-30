import json

from argos.installer import (
    DEFAULT_MANIFEST,
    apply_plan,
    build_install_plan,
    configure_env,
    load_manifest,
    main,
    plan_to_dict,
    render_unit_template,
    update_project,
    _resolve_os_packages,
    _reload_systemd,
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
    assert ("wakeword-models", "check") in actions
    assert ("stt-gateway", "configure") in actions
    assert plan.service_user == "argos"


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
    assert "linger" in actions
    assert "chown" in actions


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

    assert (project / ".env").read_text(encoding="utf-8") == "DRY_RUN=true\n"
    assert (tmp_path / "system-units" / "argos.service").exists()
    assert any(command[0] == ["uv", "sync"] for command in commands)
    assert not any("enable" in command[0] for command in commands)


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
    assert ["sudo", "loginctl", "enable-linger", "argos-test"] in commands
    assert ["sudo", "chown", "-R", "argos-test:argos-test", str(project)] in commands


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
