#!/usr/bin/env python3
"""各エージェントの利用制限状況を定期取得し、ARGOSが解釈可能なJSONとして書き出すスクリプト。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

LIMIT_DIR = Path(os.environ.get("ARGOS_AGENT_LIMIT_DIR", Path(__file__).resolve().parent)).expanduser()
UV_COMMAND = os.environ.get("UV", "uv")


class LimitData(TypedDict):
    """エージェントから取得した単一期間の利用枠。"""

    usage_pct: float
    reset: str | None


class UsageData(TypedDict):
    """エージェントから取得した短期・週次利用枠。"""

    five_hour: LimitData
    weekly: LimitData


class CodexData(UsageData, total=False):
    """任意のクレジット残高を含むCodex利用枠。"""

    credits: int


def run_cmd(args: list[str]) -> object:
    """外部スクリプトを実行して結果のJSONをロードする。"""
    res = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(
            f"コマンド実行失敗: {args}\nStdout: {res.stdout}\nStderr: {res.stderr}"
        )
    return json.loads(res.stdout)


def _limit_block(limit_data: LimitData) -> dict[str, object]:
    """five_hour/weeklyの解析結果を共通フォーマットのブロックに変換する。"""
    usage_pct = limit_data["usage_pct"]
    return {
        "remain_percentage": round(100 - usage_pct, 2),
        "use_percentage": usage_pct,
        "reset_at": limit_data["reset"],
    }


def _usage_json(usage_data: UsageData) -> dict[str, object]:
    """five_hour/weeklyの取得結果をARGOS用JSONに変換する。"""
    return {
        "5hour": _limit_block(usage_data["five_hour"]),
        "weekly": _limit_block(usage_data["weekly"]),
        "other": {},
    }


def _codex_usage_json(usage_data: CodexData) -> dict[str, object]:
    """Codexの取得結果を、任意のクレジット表示を含むARGOS用JSONへ変換する。"""
    result = _usage_json(usage_data)
    credits = usage_data.get("credits")
    if credits is not None:
        result["other"] = {"text": f"{credits} credits"}
    return result


def main() -> None:
    """各エージェントの利用枠を取得し、ダッシュボード用JSONを更新する。"""
    # Codex status の取得と変換
    try:
        codex_data = cast(
            CodexData,
            run_cmd([UV_COMMAND, "run", str(LIMIT_DIR / "codex_status.py")]),
        )
        codex_json = _codex_usage_json(codex_data)
        with open(LIMIT_DIR / "codex.json", "w", encoding="utf-8") as output_file:
            json.dump(codex_json, output_file, ensure_ascii=False, indent=2)
        print("Codex status updated successfully.")

        with open(LIMIT_DIR / "hermes.json", "w", encoding="utf-8") as output_file:
            json.dump(codex_json, output_file, ensure_ascii=False, indent=2)
        print("Hermes (Codex) status updated successfully.")
    except Exception as error:
        print(f"Failed to update codex status: {error}", file=sys.stderr)

    # Agy usage の取得と変換
    try:
        agy_data = cast(
            dict[str, UsageData],
            run_cmd([UV_COMMAND, "run", str(LIMIT_DIR / "agy_usage.py")]),
        )

        gemini_usage = agy_data.get("gemini")
        if gemini_usage:
            gemini_json = _usage_json(gemini_usage)
            with open(
                LIMIT_DIR / "antigravity.json", "w", encoding="utf-8"
            ) as output_file:
                json.dump(gemini_json, output_file, ensure_ascii=False, indent=2)
            print("Antigravity (Gemini) status updated successfully.")

    except Exception as error:
        print(f"Failed to update agy usage: {error}", file=sys.stderr)

    # Claude usage の取得と変換
    try:
        claude_data = cast(
            UsageData,
            run_cmd([UV_COMMAND, "run", str(LIMIT_DIR / "claude_usage.py")]),
        )
        claude_json = _usage_json(claude_data)
        with open(LIMIT_DIR / "claude.json", "w", encoding="utf-8") as output_file:
            json.dump(claude_json, output_file, ensure_ascii=False, indent=2)
        print("Claude status updated successfully.")
    except Exception as error:
        print(f"Failed to update claude usage: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
