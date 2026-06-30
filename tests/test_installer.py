import json

from argos.installer import apply_plan, build_install_plan, load_manifest, main, plan_to_dict, render_unit_template


def test_load_manifest_lists_core_and_planned_services():
    """サービスマニフェストから主要サービスを読み込める。"""
    services = load_manifest()
    names = {service.name for service in services}

    assert "argos" in names
    assert "argos-agent-runner" in names
    assert "tts-filter" in names
    assert "argos-acknowledgement-api" in names
    assert "stt-gateway" in names


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
    assert ("stt-gateway", "configure") in actions
    assert plan.service_user == "argos"


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
