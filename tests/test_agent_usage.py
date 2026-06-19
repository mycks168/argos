from argos.config import AgentUsageCommand
from argos.services.agent_usage import AgentUsageProvider, parse_agent_usage_json


def test_parse_agent_usage_json_supports_new_format():
    """新フォーマットの利用枠JSONを読み取れる。"""
    snapshot = parse_agent_usage_json(
        "codex",
        '{"label":"ChatGPT","5hour":{"remain_percentage":95.18,"use_percentage":4.82,"reset_at":"06/16 10:01"},"weekly":{"remain_percentage":34.57,"use_percentage":65.43,"reset_at":"06/19 06:59"},"other":{"text":"878 credits"}}',
    )

    assert snapshot.available is True
    assert snapshot.label == "ChatGPT"
    assert snapshot.five_hour is not None
    assert snapshot.five_hour.remain_percentage == 95.18
    assert snapshot.five_hour.use_percentage == 4.82
    assert snapshot.five_hour.reset_at == "06/16 10:01"
    assert snapshot.weekly is not None
    assert snapshot.weekly.remain_percentage == 34.57
    assert snapshot.weekly.use_percentage == 65.43
    assert snapshot.weekly.reset_at == "06/19 06:59"
    assert snapshot.other_text == "878 credits"


def test_parse_agent_usage_json_handles_missing_or_empty_fields():
    """欠落している、あるいは空のフィールドを含む利用枠JSONを読み取れる。"""
    snapshot = parse_agent_usage_json(
        "antigravity",
        '{"5hour":{"remain_percentage":99.0,"use_percentage":1.0},"weekly":null,"other":{}}',
    )

    assert snapshot.available is True
    assert snapshot.five_hour is not None
    assert snapshot.five_hour.remain_percentage == 99.0
    assert snapshot.five_hour.use_percentage == 1.0
    assert snapshot.five_hour.reset_at == ""
    assert snapshot.weekly is None
    assert snapshot.other_text == ""


def test_agent_usage_provider_runs_configured_command(tmp_path):
    """設定された外部コマンドを実行して利用枠を取得する。"""
    script = tmp_path / "usage.sh"
    script.write_text(
        '#!/bin/sh\nprintf \'{"5hour":{"remain_percentage":90.0,"use_percentage":10.0}}\'\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    provider = AgentUsageProvider((AgentUsageCommand("codex", str(script)),), timeout_seconds=2)

    snapshot = provider.fetch("codex")

    assert snapshot.available is True
    assert snapshot.five_hour is not None
    assert snapshot.five_hour.remain_percentage == 90.0
    assert snapshot.five_hour.use_percentage == 10.0


def test_agent_usage_provider_reports_command_failure():
    """利用枠取得コマンドの失敗を表示用スナップショットにする。"""
    provider = AgentUsageProvider((AgentUsageCommand("codex", "sh -c 'echo failed >&2; exit 2'"),), timeout_seconds=2)

    snapshot = provider.fetch("codex")

    assert snapshot.available is False
    assert snapshot.label == "取得失敗"
    assert "failed" in snapshot.error
