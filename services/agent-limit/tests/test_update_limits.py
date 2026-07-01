import json

import update_limits


def test_main_writes_claude_json_from_claude_usage(monkeypatch, tmp_path):
    """claude_usage.pyの取得結果をclaude.jsonへ書き出す。"""

    def fake_run_cmd(args):
        script = str(args[-1])
        if script.endswith("codex_status.py"):
            return {
                "five_hour": {"usage_pct": 10, "reset": "07/02 12:00"},
                "weekly": {"usage_pct": 20, "reset": "07/08 05:00"},
                "credits": 700,
            }
        if script.endswith("agy_usage.py"):
            return {
                "gemini": {
                    "five_hour": {"usage_pct": 2.5, "reset": "07/02 11:50"},
                    "weekly": {"usage_pct": 30, "reset": "07/04 04:23"},
                },
                "claude_gpt": {
                    "five_hour": {"usage_pct": 99, "reset": "wrong"},
                    "weekly": {"usage_pct": 99, "reset": "wrong"},
                },
            }
        if script.endswith("claude_usage.py"):
            return {
                "five_hour": {"usage_pct": 12.34, "reset": "07/02 13:00"},
                "weekly": {"usage_pct": 56.78, "reset": "07/05 09:30"},
            }
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(update_limits, "LIMIT_DIR", tmp_path)
    monkeypatch.setattr(update_limits, "run_cmd", fake_run_cmd)

    update_limits.main()

    claude_json = json.loads((tmp_path / "claude.json").read_text())
    assert claude_json == {
        "5hour": {
            "remain_percentage": 87.66,
            "use_percentage": 12.34,
            "reset_at": "07/02 13:00",
        },
        "weekly": {
            "remain_percentage": 43.22,
            "use_percentage": 56.78,
            "reset_at": "07/05 09:30",
        },
        "other": {},
    }

    antigravity_json = json.loads((tmp_path / "antigravity.json").read_text())
    assert antigravity_json["5hour"]["use_percentage"] == 2.5
