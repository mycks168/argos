"""雨雲レーダー表示（rain_radar.html / show_rain_radar.py）のテスト。"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from argos.services.dashboard.server import DashboardServer
from argos.services.dashboard.state import DashboardState


def _load_rain_radar_module():
    """スキル配下の show_rain_radar.py をテスト用に読み込む。"""
    script_path = Path(__file__).resolve().parents[1] / "skills/dashboard-overlay/scripts/show_rain_radar.py"
    spec = importlib.util.spec_from_file_location("show_rain_radar", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["show_rain_radar"] = module
    spec.loader.exec_module(module)
    return module


def test_rain_radar_html_is_served():
    """ダッシュボードサーバーが /static/rain_radar.html を配信できることを確認する。"""
    state = DashboardState()
    server = DashboardServer(state, "127.0.0.1", 0, "secret")
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        import urllib.request

        with urllib.request.urlopen(f"{base_url}/static/rain_radar.html", timeout=2) as res:
            assert res.status == 200
            html = res.read().decode("utf-8")
        assert "targetTimes_N2.json" in html
        assert "leaflet-base-map-pane" in html
        assert "名古屋" not in html  # 利用者の地域はソースに書かない
        assert "139.6917" in html
    finally:
        server.stop()


def test_resolve_radar_target_priority():
    """座標解決の優先順位（明示座標 → 地名検索 → 現在地 → 既定の地点）を確認する。"""
    mod = _load_rain_radar_module()

    # 1. 明示座標が最優先
    assert mod.resolve_radar_target(
        lat=35.0, lng=137.0, query="東京駅", label="テスト", dashboard_host="127.0.0.1", dashboard_port=8765
    ) == (35.0, 137.0, "テスト")

    # 2. 地名検索
    with patch.object(mod, "geocode_place", return_value=(34.5, 135.5, "テスト駅")):
        assert mod.resolve_radar_target(
            lat=None, lng=None, query="テスト駅", label="", dashboard_host="127.0.0.1", dashboard_port=8765
        ) == (34.5, 135.5, "テスト駅")

    # 3. 現在地API成功時
    with patch.object(mod.send_overlay, "fetch_current_location", return_value=(35.5, 139.5)):
        assert mod.resolve_radar_target(
            lat=None, lng=None, query="", label="", dashboard_host="127.0.0.1", dashboard_port=8765
        ) == (35.5, 139.5, "現在地")

    # 4. 現在地API失敗時は既定の地点（設定がなければ東京）
    with (
        patch.object(mod.send_overlay, "fetch_current_location", side_effect=RuntimeError("no gps")),
        patch.object(mod, "default_location", return_value=mod.FALLBACK_LOCATION),
    ):
        assert mod.resolve_radar_target(
            lat=None, lng=None, query="", label="", dashboard_host="127.0.0.1", dashboard_port=8765
        ) == (35.6895, 139.6917, "東京")


def test_geocode_place_parses_gsi_response():
    """国土地理院APIのレスポンスから座標とタイトルを抽出できることを確認する。"""
    mod = _load_rain_radar_module()
    fake_payload = json.dumps(
        [{"geometry": {"coordinates": [135.501234, 34.512345]}, "properties": {"title": "テスト県テスト市テスト駅"}}]
    ).encode("utf-8")

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResponse(fake_payload)):
        lat, lng, title = mod.geocode_place("テスト駅")
    assert round(lat, 4) == 34.5123
    assert round(lng, 4) == 135.5012
    assert "テスト市" in title


def test_main_dry_run_and_post(capsys):
    """dry-run時のJSON出力と通常実行時のpost_event呼び出しを確認する。"""
    mod = _load_rain_radar_module()

    with (
        patch.object(mod.send_overlay, "fetch_current_location", side_effect=RuntimeError("no gps")),
        patch.dict("os.environ", {mod.DEFAULT_ENV: "34.5,135.5,テスト地点"}),
    ):
        rc = mod.main(["--dry-run", "--target-slot", "right", "--no-play"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overlay_type"] == "html"
    assert out["target_slot"] == "right"
    assert "play=0" in out["url"]
    assert "34.500000" in out["url"]

    posted = []
    with (
        patch.dict("os.environ", {"ARGOS_DASHBOARD_TOKEN": "test-token"}),
        patch.object(mod.send_overlay, "fetch_current_location", side_effect=RuntimeError("no gps")),
        patch.object(
            mod.send_overlay,
            "send_payload",
            side_effect=lambda payload, url, token: posted.append(payload) or "ok",
        ),
    ):
        rc2 = mod.main(["--lat", "34.5", "--lng", "135.5", "--label", "自宅"])
    assert rc2 == 0
    assert len(posted) == 1
    assert posted[0]["title"] == "雨雲レーダー (自宅)"
    assert "play=1" in posted[0]["url"]


def test_default_location_from_setting(monkeypatch):
    """既定の地点は環境変数・config.yaml の設定から読み、読めなければ東京にする。"""
    mod = _load_rain_radar_module()
    monkeypatch.setenv(mod.DEFAULT_ENV, " 34.5 , 135.5 , テスト地点 ")
    assert mod.default_location() == (34.5, 135.5, "テスト地点")
    monkeypatch.setenv(mod.DEFAULT_ENV, "34.5,135.5")
    assert mod.default_location() == (34.5, 135.5, "既定の地点")
    for bad in ("x,y,z", "34.5", "95,135,範囲外", "34,190,範囲外"):
        monkeypatch.setenv(mod.DEFAULT_ENV, bad)
        assert mod.default_location() == mod.FALLBACK_LOCATION

    monkeypatch.delenv(mod.DEFAULT_ENV)
    import argos.yaml_config as yaml_config

    read = []

    def fake_load(path):
        """読んだ設定ファイルを記録して、設定を返す。"""
        read.append(path)
        return {mod.DEFAULT_ENV: "33.5,130.4,設定の地点"}

    monkeypatch.setattr(yaml_config, "load_yaml_environment", fake_load)
    monkeypatch.delenv("ARGOS_CONFIG_FILE", raising=False)
    assert mod.default_location() == (33.5, 130.4, "設定の地点")
    # 作業フォルダではなく、ARGOS のフォルダの config.yaml を読む。
    assert read[-1] == mod.ARGOS_ROOT / "config.yaml"
    monkeypatch.setenv("ARGOS_CONFIG_FILE", "/tmp/other.yaml")
    mod.default_location()
    assert str(read[-1]) == "/tmp/other.yaml"

    def broken(path):
        """設定を読めない場合。"""
        raise OSError("no config")

    monkeypatch.setattr(yaml_config, "load_yaml_environment", broken)
    assert mod.default_location() == mod.FALLBACK_LOCATION
