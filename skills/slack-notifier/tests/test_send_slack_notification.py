"""Slack通知スクリプトのテスト。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import importlib.util


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "send_slack_notification.py"
spec = importlib.util.spec_from_file_location("send_slack_notification", SCRIPT_PATH)
assert spec is not None
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class SlackNotificationTest(unittest.TestCase):
    """Slack通知payloadとCLI動作を検証する。"""

    def test_build_payload_with_title_text_and_url(self) -> None:
        """タイトル、本文、URLをSlack blocksへ変換できる。"""
        payload = module.build_payload("タイトル", "本文", "https://example.com", kind="link")

        self.assertEqual(payload["text"], "[ARGOS] タイトル 本文 https://example.com")
        self.assertEqual(payload["blocks"][0]["type"], "header")
        self.assertIn("<https://example.com|リンクを開く>", payload["blocks"][2]["text"]["text"])

    def test_build_payload_rejects_empty_text(self) -> None:
        """空通知は送らない。"""
        with self.assertRaises(ValueError):
            module.build_payload("", "", "")

    def test_dry_run_prints_payload(self) -> None:
        """dry-runではWebhookへ送らずpayloadを表示する。"""
        with patch("builtins.print") as print_mock:
            self.assertEqual(module.main(["--text", "本文", "--dry-run"]), 0)

        printed = print_mock.call_args.args[0]
        self.assertEqual(json.loads(printed), {"text": "[ARGOS] ARGOS通知 本文"})


if __name__ == "__main__":
    unittest.main()
