import unittest
from unittest.mock import patch, MagicMock
import sys
import json
from pathlib import Path

# テスト対象をインポートできるように sys.path を調整
sys.path.append(str(Path(__file__).parent.parent / "scripts"))
import search_and_plot

class TestSearchAndPlot(unittest.TestCase):

    def setUp(self):
        # テスト用の一時データディレクトリを作成
        self.original_data_dir = search_and_plot.DATA_DIR
        self.test_dir = Path(__file__).parent / "test_data"
        self.test_dir.mkdir(exist_ok=True)
        search_and_plot.DATA_DIR = self.test_dir
        
        # テストデータの書き込み
        self.csv_path = self.test_dir / "test_tohoku6.csv"
        self.csv_data = (
            "name,prefecture,address,business_hours,closed_days,latitude,longitude,source_url\n"
            "にしね,岩手県,住所,時間,休み,39.88003,141.09925,http://example.com\n"
            "もりおか渋民,岩手県,住所,時間,休み,39.845022,141.173318,http://example.com\n"
        )
        self.csv_path.write_text(self.csv_data, encoding="utf-8")

    def tearDown(self):
        # 元に戻す
        search_and_plot.DATA_DIR = self.original_data_dir
        if hasattr(self, "test_dir") and self.test_dir.exists():
            for f in self.test_dir.glob("*"):
                f.unlink()
            self.test_dir.rmdir()

    def test_search_local_csv_success(self):
        result = search_and_plot.search_local_csv("にしね")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 39.88003)
        self.assertEqual(result[1], 141.09925)
        self.assertEqual(result[2], "道の駅 にしね")

    def test_search_local_csv_not_found(self):
        result = search_and_plot.search_local_csv("存在しない道の駅")
        self.assertIsNone(result)

    @patch("search_and_plot.urllib.request.urlopen")
    def test_search_location_success_overpass(self, mock_urlopen):
        # 模擬のOverpass APIレスポンス (CSV検索がNoneだった場合)
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "elements": [
                {
                    "type": "node",
                    "id": 12345,
                    "lat": 39.90169,
                    "lon": 140.45728,
                    "tags": {
                        "name": "道の駅 あに"
                    }
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # CSVに存在しない名前で検索してOverpassが呼ばれることを検証
        result = search_and_plot.search_location("道の駅 あに")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 39.90169)
        self.assertEqual(result[1], 140.45728)
        self.assertEqual(result[2], "道の駅 あに")

    @patch("search_and_plot.send_overlay.main")
    @patch("search_and_plot.search_location")
    def test_main_success_single(self, mock_search, mock_send_main):
        mock_search.return_value = (39.88003, 141.09925, "道の駅 にしね")
        mock_send_main.return_value = 0

        exit_code = search_and_plot.main(["--query", "にしね", "--dry-run"])
        self.assertEqual(exit_code, 0)
        mock_send_main.assert_called_once()

    @patch("search_and_plot.send_overlay.main")
    @patch("search_and_plot.search_location")
    def test_main_success_multiple(self, mock_search, mock_send_main):
        mock_search.side_effect = [
            (39.88003, 141.09925, "道の駅 にしね"),
            (39.845022, 141.173318, "道の駅 もりおか渋民")
        ]
        mock_send_main.return_value = 0

        exit_code = search_and_plot.main(["-q", "にしね", "-q", "もりおか渋民", "--dry-run"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_search.call_count, 2)
        mock_send_main.assert_called_once()
        args = mock_send_main.call_args[0][0]
        self.assertIn("道の駅 にしね ほか 2箇所", args)

    @patch("search_and_plot.search_location")
    def test_main_not_found(self, mock_search):
        mock_search.return_value = None

        exit_code = search_and_plot.main(["--query", "存在しない道の駅"])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
