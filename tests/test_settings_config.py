from pathlib import Path

import pytest
import yaml

from argos.services.dashboard.settings_config import (
    load_settings_form,
    save_settings_form,
)


def _write_config(path: Path) -> None:
    """設定画面テスト用のYAMLを書く。"""
    path.write_text(
        """
audio:
  output_volume: 70
  listen_mode: wakeword
agents:
  antigravity:
    skip_permissions: false
location:
  provider: local
wakeword:
  enabled: false
remote_argos:
  timeout_seconds: 1800
custom:
  keep_me: value
""".lstrip(),
        encoding="utf-8",
    )


def test_load_settings_form_returns_descriptions_and_current_values(tmp_path):
    """現在値と初心者向け説明をまとめて取得できる。"""
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    result = load_settings_form(config_path)
    fields = {field["key"]: field for field in result["fields"]}

    assert fields["audio.output_volume"]["value"] == 70
    assert fields["audio.output_volume"]["description"]
    assert fields["location.provider"]["value"] == "local"
    assert fields["custom.keep_me"]["value"] == "value"
    assert fields["custom.keep_me"]["description"]
    assert fields["remote_argos.timeout_seconds"]["value"] == 1800
    assert fields["remote_argos.timeout_seconds"]["section_label"] == "リモートARGOS"
    assert "0なら" in fields["remote_argos.timeout_seconds"]["description"]
    assert result["restart_required"] is True


def test_save_settings_form_updates_only_allowed_values_and_creates_backup(tmp_path):
    """許可項目だけを更新し、元設定をバックアップする。"""
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    backup_path = save_settings_form(
        config_path,
        {
            "audio.output_volume": 55,
            "agents.antigravity.skip_permissions": True,
            "location.provider": "local",
        },
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    backup = yaml.safe_load(backup_path.read_text(encoding="utf-8"))
    assert saved["audio"]["output_volume"] == 55
    assert saved["agents"]["antigravity"]["skip_permissions"] is True
    assert saved["location"]["provider"] == "local"
    assert saved["custom"]["keep_me"] == "value"
    assert backup["audio"]["output_volume"] == 70


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"audio.output_volume": 101}, "0から100"),
        ({"location.provider": "unknown"}, "選択肢"),
        ({"unknown.value": True}, "未対応"),
        ({"wakeword.enabled": "true"}, "オンまたはオフ"),
    ],
)
def test_save_settings_form_rejects_invalid_values(tmp_path, values, message):
    """範囲外や未対応の入力を保存しない。"""
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    with pytest.raises(ValueError, match=message):
        save_settings_form(config_path, values)

    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["audio"]["output_volume"] == 70


def test_secret_values_are_masked_and_unchanged_marker_keeps_value(tmp_path):
    """トークンは画面へ返さず、変更なし指定では既存値を維持する。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  token: secret-value\n", encoding="utf-8")

    field = load_settings_form(config_path)["fields"][0]
    assert field["secret"] is True
    assert field["configured"] is True
    assert field["value"] == ""

    save_settings_form(config_path, {"dashboard.token": "__ARGOS_SECRET_UNCHANGED__"})
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["dashboard"]["token"] == "secret-value"
