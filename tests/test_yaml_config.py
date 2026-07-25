"""config.yaml読込と旧.env移行のテスト。"""

import pytest
import yaml

from argos.yaml_config import apply_yaml_environment, load_yaml_environment, write_yaml_from_environment


def test_load_hierarchical_yaml(tmp_path):
    """階層設定とスロット配列を既存設定名へ変換する。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  host: 0.0.0.0
  port: 8765
  enabled: true
  camera_snapshot_path: /tmp/camera.jpg
audio:
  state_path: ~/.local/state/argos/audio.json
  listen_mode: vad
  silence_rms_threshold: 180
tts:
  delimiters: 。！？
  cache:
    enabled: false
    max_chars: 40
ptt:
  gpio: 17
network:
  wifi_status_refresh_seconds: 15
runtime:
  dry_run: false
location:
  osrm_url: https://route.example
agents:
  codex:
    model: gpt-test
    extra_args:
      - --one
      - --two
agent:
  usage_commands:
    codex: python usage.py
  slots:
    - name: 車載Codex
      provider: codex
      cwd: /opt/argos
      voicevox_speaker: 8
      model: gpt-test
""",
        encoding="utf-8",
    )

    values = load_yaml_environment(path)

    assert values["ARGOS_DASHBOARD_HOST"] == "0.0.0.0"
    assert values["ARGOS_DASHBOARD_PORT"] == "8765"
    assert values["ARGOS_DASHBOARD_ENABLED"] == "true"
    assert values["ARGOS_CAMERA_SNAPSHOT_PATH"] == "/tmp/camera.jpg"
    assert values["ARGOS_AUDIO_STATE_PATH"] == "~/.local/state/argos/audio.json"
    assert values["ARGOS_LISTEN_MODE"] == "vad"
    assert values["SILENCE_RMS_THRESHOLD"] == "180"
    assert values["ARGOS_TTS_DELIMITERS"] == "。！？"
    assert values["ARGOS_TTS_CACHE_ENABLED"] == "false"
    assert values["ARGOS_TTS_CACHE_MAX_CHARS"] == "40"
    assert values["ARGOS_PTT_GPIO"] == "17"
    assert values["ARGOS_WIFI_STATUS_REFRESH_SECONDS"] == "15"
    assert values["DRY_RUN"] == "false"
    assert values["OSRM_URL"] == "https://route.example"
    assert "TTS_FILTER_DELIMITERS" not in values
    assert values["ARGOS_CODEX_MODEL"] == "gpt-test"
    assert values["ARGOS_CODEX_EXTRA_ARGS"] == "--one --two"
    assert values["ARGOS_AGENT_USAGE_COMMAND_CODEX"] == "python usage.py"
    slots = __import__("json").loads(values["ARGOS_AGENT_SLOTS_JSON"])
    assert slots == [
        {
            "name": "車載Codex",
            "provider": "codex",
            "cwd": "/opt/argos",
            "voicevox_speaker": 8,
            "model": "gpt-test",
        }
    ]


def test_process_environment_overrides_yaml(monkeypatch, tmp_path):
    """実プロセス環境はYAMLより優先する。"""
    path = tmp_path / "config.yaml"
    path.write_text("dashboard:\n  port: 9999\n  host: 0.0.0.0\n", encoding="utf-8")
    monkeypatch.setenv("ARGOS_DASHBOARD_PORT", "8765")
    monkeypatch.delenv("ARGOS_DASHBOARD_HOST", raising=False)

    apply_yaml_environment(path, {"ARGOS_DASHBOARD_PORT"})

    assert __import__("os").environ["ARGOS_DASHBOARD_PORT"] == "8765"
    assert __import__("os").environ["ARGOS_DASHBOARD_HOST"] == "0.0.0.0"


def test_env_to_yaml_round_trip(tmp_path):
    """旧envの既知・未知設定とスロットをYAML経由で維持する。"""
    source = {
        "ARGOS_DASHBOARD_ENABLED": "true",
        "ARGOS_DASHBOARD_PORT": "8765",
        "ARGOS_CODEX_MODEL": "gpt-test",
        "ARGOS_AUDIO_STATE_PATH": "~/.local/state/argos/audio.json",
        "ARGOS_TTS_CACHE_DIR": "cache/custom",
        "ARGOS_PTT_GPIO": "17",
        "OSRM_URL": "https://route.example",
        "ARGOS_AGENT_SLOT_1": "作業,codex,/opt/argos,2,gpt-test",
        "CUSTOM_SECRET": "abc==",
    }
    path = tmp_path / "config.yaml"

    write_yaml_from_environment(source, path)
    loaded = load_yaml_environment(path)

    assert loaded["ARGOS_DASHBOARD_ENABLED"] == source["ARGOS_DASHBOARD_ENABLED"]
    assert loaded["ARGOS_DASHBOARD_PORT"] == source["ARGOS_DASHBOARD_PORT"]
    assert loaded["ARGOS_CODEX_MODEL"] == source["ARGOS_CODEX_MODEL"]
    assert loaded["ARGOS_AUDIO_STATE_PATH"] == source["ARGOS_AUDIO_STATE_PATH"]
    assert loaded["ARGOS_TTS_CACHE_DIR"] == source["ARGOS_TTS_CACHE_DIR"]
    assert loaded["ARGOS_PTT_GPIO"] == source["ARGOS_PTT_GPIO"]
    assert loaded["OSRM_URL"] == source["OSRM_URL"]
    assert loaded["CUSTOM_SECRET"] == source["CUSTOM_SECRET"]
    slots = __import__("json").loads(loaded["ARGOS_AGENT_SLOTS_JSON"])
    assert slots[0]["name"] == "作業"
    assert slots[0]["type"] == "local"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["dashboard"]["port"] == 8765
    assert data["audio"]["state_path"] == "~/.local/state/argos/audio.json"
    assert data["tts"]["cache"]["dir"] == "cache/custom"
    assert data["ptt"]["gpio"] == 17
    assert data["location"]["osrm_url"] == "https://route.example"
    assert data["agent"]["slots"][0]["name"] == "作業"
    assert "  slots:\n    - type: local" in path.read_text(encoding="utf-8")
    assert data["environment"]["CUSTOM_SECRET"] == "abc=="
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_remote_slot_keeps_local_and_remote_order(tmp_path):
    """ローカルとリモートのスロット順序をYAMLどおり維持する。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        """
agent:
  slots:
    - type: remote
      name: 自宅
      url: https://home.example
      token: secret
      remote_name: 作業
      remote_provider: codex
    - type: local
      name: 車載
      provider: claude
      cwd: /opt/argos
""",
        encoding="utf-8",
    )

    slots = __import__("json").loads(load_yaml_environment(path)["ARGOS_AGENT_SLOTS_JSON"])

    assert [slot["name"] for slot in slots] == ["自宅", "車載"]
    assert slots[0]["type"] == "remote"


def test_invalid_yaml_reports_configuration_error(tmp_path):
    """壊れたYAMLは無視せず起動前に検出する。"""
    path = tmp_path / "config.yaml"
    path.write_text("dashboard:\n  port: [broken\n", encoding="utf-8")

    with pytest.raises(ValueError, match="config.yaml"):
        load_yaml_environment(path)
