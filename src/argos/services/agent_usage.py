"""LLMエージェントの利用枠情報を取得する。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from argos.config import AgentUsageCommand


def _now_iso() -> str:
    """現在時刻をISO 8601形式で返す。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class UsageBucket:
    """5時間や週間など、1期間ぶんの利用枠を表す。"""

    label: str
    remain_percentage: float = 0.0
    use_percentage: float = 0.0
    reset_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """ダッシュボードへ渡す辞書へ変換する。"""
        return {
            "label": self.label,
            "remain_percentage": self.remain_percentage,
            "use_percentage": self.use_percentage,
            "reset_at": self.reset_at,
        }


@dataclass(frozen=True)
class AgentUsageSnapshot:
    """エージェント利用枠の取得結果。"""

    provider: str
    available: bool
    label: str
    five_hour: UsageBucket | None = None
    weekly: UsageBucket | None = None
    other_text: str = ""
    error: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """ダッシュボードへ渡す辞書へ変換する。"""
        return {
            "provider": self.provider,
            "available": self.available,
            "label": self.label,
            "five_hour": self.five_hour.to_dict() if self.five_hour else None,
            "weekly": self.weekly.to_dict() if self.weekly else None,
            "other_text": self.other_text,
            "error": self.error,
            "updated_at": self.updated_at or _now_iso(),
        }


class AgentUsageProvider:
    """プロバイダ名ごとの外部コマンドを実行して利用枠を取得する。"""

    def __init__(self, commands: tuple[AgentUsageCommand, ...], timeout_seconds: float = 5.0) -> None:
        """利用枠取得コマンドとタイムアウトを設定する。"""
        self._commands = {command.provider.lower(): command.command for command in commands}
        self._timeout_seconds = timeout_seconds

    @property
    def providers(self) -> set[str]:
        """取得コマンドが設定されたプロバイダ名を返す。"""
        return set(self._commands)

    def has_provider(self, provider: str) -> bool:
        """指定プロバイダの取得コマンドがあるか返す。"""
        return provider.strip().lower() in self._commands

    def fetch(self, provider: str) -> AgentUsageSnapshot:
        """指定プロバイダの利用枠を取得する。"""
        normalized = provider.strip().lower()
        command = self._commands.get(normalized, "")
        if not command:
            return AgentUsageSnapshot(normalized, False, "取得コマンド未設定", updated_at=_now_iso())
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return AgentUsageSnapshot(normalized, False, "取得タイムアウト", error="timeout", updated_at=_now_iso())
        except OSError as exc:
            return AgentUsageSnapshot(normalized, False, "取得失敗", error=str(exc), updated_at=_now_iso())
        if result.returncode != 0:
            error = (result.stderr or result.stdout).strip()
            return AgentUsageSnapshot(normalized, False, "取得失敗", error=error[:300], updated_at=_now_iso())
        return parse_agent_usage_json(normalized, result.stdout)


def parse_agent_usage_json(provider: str, output: str) -> AgentUsageSnapshot:
    """外部コマンドのJSON出力を利用枠スナップショットへ変換する。"""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return AgentUsageSnapshot(provider, False, "JSON解析失敗", error=str(exc), updated_at=_now_iso())
    if not isinstance(payload, dict):
        return AgentUsageSnapshot(provider, False, "JSON形式不正", error="root must be object", updated_at=_now_iso())
    
    five_hour_data = payload.get("5hour")
    five_hour = _parse_bucket("5時間", five_hour_data)
    
    weekly_data = payload.get("weekly")
    weekly = _parse_bucket("週間", weekly_data)
    
    other_data = payload.get("other") or {}
    other_text = ""
    if isinstance(other_data, dict):
        other_text = str(other_data.get("text") or "")
        
    label = str(payload.get("label") or payload.get("status") or "利用枠")
    available = bool(five_hour or weekly or payload.get("available", True))
    error = str(payload.get("error") or "")
    updated_at = str(payload.get("updated_at") or _now_iso())
    return AgentUsageSnapshot(
        provider,
        available,
        label,
        five_hour=five_hour,
        weekly=weekly,
        other_text=other_text,
        error=error,
        updated_at=updated_at,
    )


def _parse_bucket(
    label: str,
    value: object,
) -> UsageBucket | None:
    """入れ子形式の期間情報を読み取る。"""
    if not isinstance(value, dict):
        return None
    
    remain_percentage = _to_float(value.get("remain_percentage"))
    use_percentage = _to_float(value.get("use_percentage"))
    reset_at = str(value.get("reset_at") or "")
    
    return UsageBucket(
        label=label,
        remain_percentage=remain_percentage,
        use_percentage=use_percentage,
        reset_at=reset_at,
    )


def _to_float(value: object) -> float:
    """値をfloatに変換する。"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

