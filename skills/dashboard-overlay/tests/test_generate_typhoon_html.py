import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from scripts import generate_typhoon_html


def test_load_env_vars(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ARGOS_DASHBOARD_HOST=192.168.1.100\nARGOS_DASHBOARD_PORT=9999\n# comment\nOTHER_VAR=test",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"ARGOS_ENV_FILE": str(env_file)}):
        vars_dict = generate_typhoon_html.load_env_vars()
    assert vars_dict["ARGOS_DASHBOARD_HOST"] == "192.168.1.100"
    assert vars_dict["ARGOS_DASHBOARD_PORT"] == "9999"


def test_get_save_path_uses_env():
    with patch.dict("os.environ", {"ARGOS_DASHBOARD_STATIC_DIR": "/tmp/argos-static"}):
        assert generate_typhoon_html.get_save_path({}) == Path("/tmp/argos-static/typhoon_map.html")


def test_fetch_latest_typhoon_id_success():
    dummy_html = '<html><body><a href="/bousai/typhoon/2607/">台風7号</a><a href="/bousai/typhoon/2608/">台風8号</a></body></html>'
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.read.return_value = dummy_html.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_res

        tid = generate_typhoon_html.fetch_latest_typhoon_id()
        assert tid == "2608"


def test_fetch_latest_typhoon_id_fail():
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        tid = generate_typhoon_html.fetch_latest_typhoon_id()
        assert tid == "2608"  # fallback


def test_parse_typhoon_points_success():
    dummy_html = """
    <table>
      <tr><th rowspan="2">中心位置</th><td rowspan="2">北緯 27度35分<br>東経 134度05分</td></tr>
      <tr><th rowspan="2">予報円の中心</th><td rowspan="2">北緯 33度55分<br>東経 137度40分</td></tr>
    </table>
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.read.return_value = dummy_html.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_res

        points = generate_typhoon_html.parse_typhoon_points("2608")
        assert len(points) == 2
        # 27 + 35/60 = 27.5833
        assert pytest.approx(points[0]["lat"], 0.001) == 27.583
        assert pytest.approx(points[0]["lon"], 0.001) == 134.083
        assert points[0]["is_current"] is True
        assert points[0]["label"] == "実況"

        assert pytest.approx(points[1]["lat"], 0.001) == 33.916
        assert pytest.approx(points[1]["lon"], 0.001) == 137.666
        assert points[1]["is_current"] is False
        assert points[1]["label"] == "予想(12時間後)"


def test_parse_typhoon_points_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        points = generate_typhoon_html.parse_typhoon_points("2608")
        assert points == []


def test_latlon_to_css():
    top, left = generate_typhoon_html.latlon_to_css(35.68, 139.69) # Tokyo
    assert 0.0 <= top <= 100.0
    assert 0.0 <= left <= 100.0


def test_generate_html():
    points = [
        {"label": "実況", "lat": 27.58, "lon": 134.08, "is_current": True},
        {"label": "予想(12時間後)", "lat": 33.92, "lon": 137.67, "is_current": False}
    ]
    html = generate_html = generate_typhoon_html.generate_html("2608", points)
    assert "台風第8号 進路予測" in html
    assert "marker-current" in html
    assert "marker-forecast" in html
    assert "<polyline" in html
    assert "<circle" in html


def test_send_overlay_event():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.read.return_value = b'{"status": "success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_res

        res = generate_typhoon_html.send_overlay_event("http://localhost:8765/api/events", "token", "center", "title")
        assert res == '{"status": "success"}'

        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_method() == "POST"
        assert req.headers["Content-type"] == "application/json"
        assert req.headers["Authorization"] == "Bearer token"

        body = json.loads(req.data.decode("utf-8"))
        assert body["type"] == "overlay"
        assert body["overlay_type"] == "html"
        assert body["url"] == "/static/typhoon_map.html"


@patch("scripts.generate_typhoon_html.load_env_vars", return_value={})
@patch("scripts.generate_typhoon_html.fetch_latest_typhoon_id", return_value="2608")
@patch("scripts.generate_typhoon_html.parse_typhoon_points", return_value=[
    {"label": "実況", "lat": 27.58, "lon": 134.08, "is_current": True}
])
@patch("pathlib.Path.mkdir")
@patch("pathlib.Path.write_text")
@patch("scripts.generate_typhoon_html.send_overlay_event", return_value='{"status": "ok"}')
def test_main_success(mock_send, mock_write, mock_mkdir, mock_parse, mock_fetch_id, mock_load_env):
    ret = generate_typhoon_html.main(["--target-slot", "center"])
    assert ret == 0
    mock_write.assert_called_once()
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[2] == "center"
    assert kwargs["title"] == "台風第8号予測マップ"


@patch("scripts.generate_typhoon_html.load_env_vars", return_value={})
@patch("scripts.generate_typhoon_html.fetch_latest_typhoon_id", return_value="2608")
@patch("scripts.generate_typhoon_html.parse_typhoon_points", return_value=[])
def test_main_parse_fail(mock_parse, mock_fetch_id, mock_load_env):
    ret = generate_typhoon_html.main([])
    assert ret == 1


@patch("scripts.generate_typhoon_html.load_env_vars", return_value={})
@patch("scripts.generate_typhoon_html.fetch_latest_typhoon_id", return_value="2608")
@patch("scripts.generate_typhoon_html.parse_typhoon_points", return_value=[
    {"label": "実況", "lat": 27.58, "lon": 134.08, "is_current": True}
])
@patch("pathlib.Path.mkdir")
@patch("pathlib.Path.write_text")
@patch("scripts.generate_typhoon_html.send_overlay_event", side_effect=Exception("API error"))
def test_main_send_fail(mock_send, mock_write, mock_mkdir, mock_parse, mock_fetch_id, mock_load_env):
    ret = generate_typhoon_html.main([])
    assert ret == 1
