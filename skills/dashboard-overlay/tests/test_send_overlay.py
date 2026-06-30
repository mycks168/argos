"""ARGOSダッシュボード表示スクリプトのテスト。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "send_overlay.py"
spec = importlib.util.spec_from_file_location("send_overlay", SCRIPT_PATH)
assert spec is not None
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class DashboardOverlayTest(unittest.TestCase):
    """ダッシュボード表示payloadとCLI動作を検証する。"""

    def _args(self, **overrides):
        """build_payloadへ渡す最小引数を作る。"""
        values = {
            "type": "markdown",
            "target_slot": "right",
            "title": "表示",
            "url": None,
            "content": "",
            "file": None,
            "lat": None,
            "lng": None,
            "color": None,
            "zoom": 13,
            "zoom_offset": 0,
            "orientation": "north",
            "interval_ms": 2000,
            "label_mode": "permanent",
            "cur_lat": None,
            "cur_lng": None,
            "follow_current": True,
            "point": None,
            "preset": None,
            "current_location": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_build_markdown_payload_for_center_slot(self) -> None:
        """Markdownを中央スロットへ表示するpayloadを作れる。"""
        payload = module.build_payload(
            self._args(type="markdown", target_slot="center", title="設計", content="# 見出し")
        )

        self.assertEqual(payload["type"], "overlay")
        self.assertEqual(payload["overlay_type"], "markdown")
        self.assertEqual(payload["target_slot"], "center")
        self.assertEqual(payload["content"], "# 見出し")
        self.assertEqual(payload["url"], "/static/reader.html")

    def test_build_map_payload_with_points_and_follow(self) -> None:
        """複数地点と現在地追従を含む地図payloadを作れる。"""
        payload = module.build_payload(
            self._args(type="map", point=["35.0,139.0,A", "36.0,140.0,B"], title="候補地", label_mode="popup")
        )

        self.assertIn("/static/map.html?", payload["url"])
        self.assertIn("follow=1", payload["url"])
        self.assertIn("label_mode=popup", payload["url"])
        self.assertEqual(payload["options"]["points"], ["35.0,139.0,A", "36.0,140.0,B"])
        self.assertEqual(payload["options"]["label_mode"], "popup")
        self.assertTrue(payload["options"]["follow_current"])

    def test_build_nav_payload_for_center_slot(self) -> None:
        """現在地追従ナビを中央スロットへ表示するpayloadを作れる。"""
        payload = module.build_payload(
            self._args(type="nav", target_slot="center", title="ナビ", zoom=14, orientation="heading", interval_ms=1500)
        )

        self.assertEqual(payload["type"], "overlay")
        self.assertEqual(payload["overlay_type"], "nav")
        self.assertEqual(payload["target_slot"], "center")
        self.assertTrue(payload["replace_top"])
        self.assertIn("/static/nav.html?", payload["url"])
        self.assertIn("orientation=heading", payload["url"])
        self.assertIn("interval=1500", payload["url"])
        self.assertEqual(payload["options"]["zoom"], 14)
        self.assertEqual(payload["options"]["orientation"], "heading")

    def test_build_clear_payload_for_target_slot(self) -> None:
        """指定スロットだけ閉じるpayloadを作れる。"""
        payload = module.build_payload(self._args(type="clear", target_slot="center"))

        self.assertEqual(payload, {"type": "clear_overlay", "target_slot": "center"})

    def test_build_swap_payload(self) -> None:
        """中央と右を入れ替えるpayloadを作れる。"""
        payload = module.build_payload(self._args(type="swap"))

        self.assertEqual(payload, {"type": "swap_slots"})

    def test_apply_tsuruoka_roadstations_preset(self) -> None:
        """鶴岡方面の道の駅プリセットを地図引数へ反映できる。"""
        args = self._args(type=None, preset="tsuruoka-roadstations")

        module.apply_preset(args)
        payload = module.build_payload(args)

        self.assertEqual(payload["overlay_type"], "map")
        self.assertEqual(payload["target_slot"], "right")
        self.assertIn("label_mode=popup", payload["url"])
        self.assertGreaterEqual(len(payload["options"]["points"]), 8)

    def test_dry_run_prints_payload(self) -> None:
        """dry-runではHTTP送信せずpayloadを表示する。"""
        with patch("builtins.print") as print_mock:
            result = module.main(["--type", "markdown", "--target-slot", "center", "--content", "本文", "--dry-run"])

        self.assertEqual(result, 0)
        printed = print_mock.call_args.args[0]
        payload = json.loads(printed)
        self.assertEqual(payload["target_slot"], "center")
        self.assertEqual(payload["content"], "本文")

    def test_build_image_payload(self) -> None:
        """Imageを表示するpayloadを作れる。"""
        payload = module.build_payload(self._args(type="image", url="/static/img.jpg"))
        self.assertEqual(payload["type"], "overlay")
        self.assertEqual(payload["overlay_type"], "image")
        self.assertIn("/static/viewer.html?url=%2Fstatic%2Fimg.jpg", payload["url"])

    def test_build_image_payload_missing_url(self) -> None:
        """ImageでURLがない場合にValueErrorを投げる。"""
        with self.assertRaises(ValueError):
            module.build_payload(self._args(type="image", url=None))

    def test_build_html_payload(self) -> None:
        """HTMLを表示するpayloadを作れる。"""
        payload = module.build_payload(self._args(type="html", url="http://example.com", content="テスト"))
        self.assertEqual(payload["type"], "overlay")
        self.assertEqual(payload["overlay_type"], "html")
        self.assertEqual(payload["url"], "http://example.com")
        self.assertEqual(payload["content"], "テスト")

    def test_build_html_payload_missing_url(self) -> None:
        """HTMLでURLがない場合にValueErrorを投げる。"""
        with self.assertRaises(ValueError):
            module.build_payload(self._args(type="html", url=None))

    def test_build_markdown_payload_missing_file(self) -> None:
        """Markdownで指定したファイルが存在しない場合にFileNotFoundErrorを投げる。"""
        with self.assertRaises(FileNotFoundError):
            module.build_payload(self._args(type="markdown", file="/non_existent_file.md"))

    def test_load_env_vars(self) -> None:
        """環境変数の読み込み処理が正常に終了する。"""
        env_vars = module.load_env_vars()
        self.assertIsInstance(env_vars, dict)


if __name__ == "__main__":
    unittest.main()
