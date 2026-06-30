#!/usr/bin/env python3
"""agyとcodexの利用制限状況を定期取得し、ARGOSが解釈可能なJSONとして書き出すスクリプト。"""

import os
import json
import subprocess
import sys
from pathlib import Path

LIMIT_DIR = Path(os.environ.get("ARGOS_AGENT_LIMIT_DIR", Path(__file__).resolve().parent)).expanduser()
UV_COMMAND = os.environ.get("UV", "uv")


def run_cmd(args):
    """外部スクリプトを実行して結果のJSONをロードする。"""
    res = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(
            f"コマンド実行失敗: {args}\nStdout: {res.stdout}\nStderr: {res.stderr}"
        )
    return json.loads(res.stdout)


def _limit_block(limit_data):
    """five_hour/weeklyの解析結果を共通フォーマットのブロックに変換する。"""
    usage_pct = limit_data["usage_pct"]
    return {
        "remain_percentage": round(100 - usage_pct, 2),
        "use_percentage": usage_pct,
        "reset_at": limit_data["reset"],
    }


def main():
    # 1. Codex status の取得と変換
    try:
        codex_data = run_cmd([UV_COMMAND, "run", str(LIMIT_DIR / "codex_status.py")])
        codex_json = {
            "5hour": _limit_block(codex_data["five_hour"]),
            "weekly": _limit_block(codex_data["weekly"]),
            "other": {"text": f"{codex_data['credits']} credits"},
        }
        with open(LIMIT_DIR / "codex.json", "w", encoding="utf-8") as f:
            json.dump(codex_json, f, ensure_ascii=False, indent=2)
        print("Codex status updated successfully.")

        # hermes.json (Codexの利用状況をそのまま割り当て)
        with open(LIMIT_DIR / "hermes.json", "w", encoding="utf-8") as f:
            json.dump(codex_json, f, ensure_ascii=False, indent=2)
        print("Hermes (Codex) status updated successfully.")
    except Exception as e:
        print(f"Failed to update codex status: {e}", file=sys.stderr)

    # 2. Agy usage の取得と変換
    try:
        agy_data = run_cmd([UV_COMMAND, "run", str(LIMIT_DIR / "agy_usage.py")])

        # gemini のデータを取得 (antigravityで使用)
        gem = agy_data.get("gemini", {})
        if gem:
            gem_json = {
                "5hour": _limit_block(gem["five_hour"]),
                "weekly": _limit_block(gem["weekly"]),
                "other": {},
            }
            # antigravity.json (Geminiの利用状況を割り当て)
            with open(LIMIT_DIR / "antigravity.json", "w", encoding="utf-8") as f:
                json.dump(gem_json, f, ensure_ascii=False, indent=2)
            print("Antigravity (Gemini) status updated successfully.")

    except Exception as e:
        print(f"Failed to update agy usage: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
