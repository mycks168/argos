"""常駐実行を明示したスロットの追加設定を検証する。"""

import math
from collections.abc import Mapping


def is_tmux_provider(provider: str) -> bool:
    """設定と実行経路で対応providerの判定を共有する。"""
    return provider.strip().lower() in {"claude", "claudecode", "codex", "antigravity"}


def slot_execution_mode(item: Mapping[str, object], name: str, default_provider: str) -> str:
    """未対応providerを試験設定で有効にしない。"""
    mode = str(item.get("execution_mode", "cli")).strip().lower()
    if mode not in {"cli", "tmux"}:
        raise ValueError(f"agent.slots[{name}].execution_modeはcliまたはtmuxにしてください")
    provider = str(item.get("provider", default_provider)).strip().lower()
    if mode == "tmux" and not is_tmux_provider(provider):
        raise ValueError(f"agent.slots[{name}]のtmux試験はClaude・Codex・antigravityのみ対応しています")
    return mode


def slot_tmux_timeout(item: Mapping[str, object], name: str) -> float:
    """無期限待機をゼロで表し、負数や非有限値を拒否する。"""
    value = item.get("tmux_timeout_seconds", 1800)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"agent.slots[{name}].tmux_timeout_secondsは0以上の数値にしてください")
    timeout_seconds = float(value)
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError(f"agent.slots[{name}].tmux_timeout_secondsは0以上の有限値にしてください")
    return timeout_seconds
