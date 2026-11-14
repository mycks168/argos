"""tmux方式の設定が従来スロットへ波及しないことを検証する。"""

import json
from dataclasses import replace

import pytest

from argos.config import load_settings


@pytest.mark.parametrize("mode", ["cli", "tmux"])
def test_slot_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """明示した方式と応答待ち時間だけを対象スロットに適用する。"""
    monkeypatch.setenv("ARGOS_AGENT_PROVIDER", "claude")
    monkeypatch.setenv("ARGOS_AGENT_SLOTS_JSON", json.dumps([
        {"name": "試験", "execution_mode": mode, "tmux_timeout_seconds": 0},
        {"name": "従来", "provider": "codex"},
    ]))
    slots = load_settings().agent_slots
    assert slots[0].execution_mode == mode
    assert slots[0].tmux_timeout_seconds == 0
    assert slots[1].execution_mode == "cli"
    assert replace(slots[0], execution_mode="cli").execution_mode == "cli"


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "1"])
def test_invalid_timeout(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    """不正な待ち時間で無限ループや即終了を起こさない。"""
    monkeypatch.setenv("ARGOS_AGENT_SLOTS_JSON", json.dumps([
        {"name": "試験", "provider": "claude", "tmux_timeout_seconds": value},
    ]))
    with pytest.raises(ValueError, match="tmux_timeout_seconds"):
        load_settings()


@pytest.mark.parametrize("provider,mode", [("hermes", "tmux"), ("claude", "unknown")])
def test_invalid_mode(monkeypatch: pytest.MonkeyPatch, provider: str, mode: str) -> None:
    """未実装providerを動作可能として受け付けない。"""
    monkeypatch.setenv("ARGOS_AGENT_SLOTS_JSON", json.dumps([
        {"name": "試験", "provider": provider, "execution_mode": mode},
    ]))
    with pytest.raises(ValueError):
        load_settings()
