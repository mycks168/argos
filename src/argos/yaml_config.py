"""階層化したconfig.yamlを既存設定名へ変換する。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


class _IndentedSafeDumper(yaml.SafeDumper):
    """配列も親キーの下へインデントして読みやすく出力する。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> int:
        """ブロック形式の配列でインデント省略を無効にする。"""
        return super().increase_indent(flow, False)


SECTION_PREFIXES = {
    "agent": "ARGOS_AGENT_",
    "agents.codex": "ARGOS_CODEX_",
    "agents.antigravity": "ARGOS_ANTIGRAVITY_",
    "agents.claude": "ARGOS_CLAUDE_",
    "agents.hermes": "ARGOS_HERMES_",
    "dashboard": "ARGOS_DASHBOARD_",
    "location": "ARGOS_LOCATION_",
    "location.remote": "ARGOS_REMOTE_LOCATION_",
    "audio": "AUDIO_",
    "lcd": "ARGOS_LCD_",
    "wakeword": "ARGOS_WAKEWORD_",
    "auth": "ARGOS_AUTH_",
    "greeting": "ARGOS_GREETING_",
    "startup": "ARGOS_STARTUP_",
    "runner": "ARGOS_AGENT_RUNNER_",
    "remote_argos": "ARGOS_REMOTE_ARGOS_",
    "conversation_history": "ARGOS_CONVERSATION_HISTORY_",
    "conversation_memory": "ARGOS_CONVERSATION_MEMORY_",
    "stt": "STT_GATEWAY_",
    "tts": "TTS_FILTER_",
    "voicevox": "VOICEVOX_",
    "kokoro": "ARGOS_KOKORO_",
    "whisper": "ARGOS_WHISPER_",
    "acknowledgement": "ARGOS_ACKNOWLEDGEMENT_",
}

LIST_MAPPINGS = {
    ("audio", "input_devices"): ("AUDIO_INPUT_DEVICES", ";"),
    ("wakeword", "aliases"): ("ARGOS_WAKEWORD_ALIASES", ","),
    ("agent", "progress_start_phrases"): ("ARGOS_AGENT_PROGRESS_START_PHRASES", ";"),
    ("agent", "progress_wait_phrases"): ("ARGOS_AGENT_PROGRESS_WAIT_PHRASES", ";"),
    ("agents.codex", "extra_args"): ("ARGOS_CODEX_EXTRA_ARGS", " "),
    ("agents.antigravity", "extra_args"): ("ARGOS_ANTIGRAVITY_EXTRA_ARGS", " "),
    ("agents.hermes", "extra_args"): ("ARGOS_HERMES_EXTRA_ARGS", " "),
}


def default_config_path() -> Path:
    """環境変数またはカレントディレクトリから設定ファイルを返す。"""
    return Path(os.environ.get("ARGOS_CONFIG_FILE", "config.yaml")).expanduser()


def load_yaml_environment(path: Path | None = None) -> dict[str, str]:
    """YAML設定を既存環境変数名の辞書へ変換する。"""
    config_path = path or default_config_path()
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"config.yamlを読み込めません: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("config.yamlのルートはマッピングで指定してください")

    values: dict[str, str] = {}
    environment = data.get("environment", {})
    if isinstance(environment, dict):
        for name, value in environment.items():
            values[str(name)] = _stringify(value)

    for section, prefix in SECTION_PREFIXES.items():
        table = _nested_table(data, section)
        if not isinstance(table, dict):
            continue
        for key, value in table.items():
            if isinstance(value, dict) or isinstance(value, list):
                continue
            values[prefix + str(key).upper()] = _stringify(value)

    _load_agent_slots(data, values)
    _load_named_commands(data, values)
    _load_list_values(data, values)
    return values


def apply_yaml_environment(path: Path | None, process_environment: set[str]) -> dict[str, str]:
    """実プロセス環境を上書きせずYAML値をos.environへ反映する。"""
    values = load_yaml_environment(path)
    for name, value in values.items():
        if name not in process_environment:
            os.environ[name] = value
    return values


def write_yaml_from_environment(values: dict[str, str], path: Path) -> None:
    """旧env値を階層YAMLへ変換し、未知の値も失わず保存する。"""
    data: dict[str, Any] = {}
    remaining = dict(values)
    slots: list[dict[str, Any]] = []
    unified = remaining.pop("ARGOS_AGENT_SLOTS_JSON", "").strip()
    if unified:
        payload = json.loads(unified)
        if not isinstance(payload, list):
            raise ValueError("ARGOS_AGENT_SLOTS_JSONはJSON配列で指定してください")
        slots.extend(item for item in payload if isinstance(item, dict))
    else:
        index = 1
        while True:
            raw = remaining.pop(f"ARGOS_AGENT_SLOT_{index}", "")
            if not raw:
                break
            parts = [part.strip() for part in raw.split(",", 4)]
            slot: dict[str, Any] = {"type": "local", "name": parts[0]}
            for key, position in (("provider", 1), ("cwd", 2), ("voicevox_speaker", 3), ("model", 4)):
                if len(parts) > position and parts[position]:
                    slot[key] = _typed_value(parts[position])
            slots.append(slot)
            index += 1
    legacy_remote = remaining.pop("ARGOS_REMOTE_ARGOS_SLOTS", "").strip()
    if legacy_remote:
        payload = json.loads(legacy_remote)
        if not isinstance(payload, list):
            raise ValueError("ARGOS_REMOTE_ARGOS_SLOTSはJSON配列で指定してください")
        for item in payload:
            if isinstance(item, dict):
                slots.append({"type": "remote", **item})
    if slots:
        _ensure_table(data, "agent")["slots"] = slots

    for (section, key), (name, separator) in LIST_MAPPINGS.items():
        if name not in remaining:
            continue
        raw = remaining.pop(name)
        _ensure_table(data, section)[key] = [part for part in raw.split(separator) if part]

    for section, prefix in sorted(SECTION_PREFIXES.items(), key=lambda item: len(item[1]), reverse=True):
        table = _ensure_table(data, section)
        for name in list(remaining):
            if not name.startswith(prefix):
                continue
            key = name[len(prefix) :].lower()
            if not key or key.startswith("slot_") or key.startswith("usage_command_"):
                continue
            table[key] = _typed_value(remaining.pop(name))
        if not table:
            _remove_empty_table(data, section)

    usage_commands = {
        name.removeprefix("ARGOS_AGENT_USAGE_COMMAND_").lower(): _typed_value(value)
        for name, value in list(remaining.items())
        if name.startswith("ARGOS_AGENT_USAGE_COMMAND_")
    }
    for provider in usage_commands:
        remaining.pop(f"ARGOS_AGENT_USAGE_COMMAND_{provider.upper()}", None)
    if usage_commands:
        _ensure_table(data, "agent")["usage_commands"] = usage_commands
    if remaining:
        data["environment"] = dict(sorted(remaining.items()))
    path.write_text(
        yaml.dump(
            data,
            Dumper=_IndentedSafeDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _load_agent_slots(data: dict[str, Any], values: dict[str, str]) -> None:
    """agent.slotsを順序を保った共通スロット設定へ変換する。"""
    agent = data.get("agent", {})
    slots = agent.get("slots", []) if isinstance(agent, dict) else []
    if not isinstance(slots, list):
        raise ValueError("agent.slotsは配列で指定してください")
    normalized: list[dict[str, Any]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError("agent.slotsの各要素はテーブルで指定してください")
        name = str(slot.get("name", "")).strip()
        if not name:
            raise ValueError("agent.slotsにはnameが必要です")
        item = {str(key): value for key, value in slot.items()}
        item["name"] = name
        normalized.append(item)
    if normalized:
        values["ARGOS_AGENT_SLOTS_JSON"] = json.dumps(normalized, ensure_ascii=False)


def _load_named_commands(data: dict[str, Any], values: dict[str, str]) -> None:
    """provider別利用枠コマンドを環境変数へ変換する。"""
    agent = data.get("agent", {})
    commands = agent.get("usage_commands", {}) if isinstance(agent, dict) else {}
    if isinstance(commands, dict):
        for provider, command in commands.items():
            values[f"ARGOS_AGENT_USAGE_COMMAND_{str(provider).upper()}"] = _stringify(command)


def _load_list_values(data: dict[str, Any], values: dict[str, str]) -> None:
    """YAML配列で表現する主要な複数値設定を変換する。"""
    for (section, key), (name, separator) in LIST_MAPPINGS.items():
        table = _nested_table(data, section)
        value = table.get(key) if isinstance(table, dict) else None
        if isinstance(value, list):
            values[name] = separator.join(_stringify(item) for item in value)


def _nested_table(data: dict[str, Any], dotted: str) -> Any:
    """ドット区切りのテーブルを辿る。"""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _stringify(value: Any) -> str:
    """YAML値を既存設定ローダーが扱える文字列へ変換する。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _typed_value(value: str) -> str | int | float | bool:
    """env文字列を安全な範囲でYAMLの型へ変換する。"""
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if value.strip() and value.strip() == value:
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                pass
    return value


def _ensure_table(data: dict[str, Any], dotted: str) -> dict[str, Any]:
    """書込用のネストしたテーブルを返す。"""
    current = data
    for part in dotted.split("."):
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"{dotted}をテーブルとして作成できません")
        current = child
    return current


def _remove_empty_table(data: dict[str, Any], dotted: str) -> None:
    """空の末端テーブルを削除する。"""
    parts = dotted.split(".")
    current: dict[str, Any] = data
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts:
        child = current.get(part)
        if not isinstance(child, dict):
            return
        parents.append((current, part))
        current = child
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break
