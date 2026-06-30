import os
import json
import urllib.request
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import pytest
from scripts import update_typhoon_image


def test_load_env_vars():
    mock_env = "ARGOS_DASHBOARD_HOST=192.168.1.100\nARGOS_DASHBOARD_PORT=9999\n# comment\nOTHER_VAR=test"
    with patch("builtins.open", mock_open(read_data=mock_env)), \
         patch("pathlib.Path.exists", return_value=True):
        vars_dict = update_typhoon_image.load_env_vars()
        assert vars_dict["ARGOS_DASHBOARD_HOST"] == "192.168.1.100"
        assert vars_dict["ARGOS_DASHBOARD_PORT"] == "9999"


def test_get_image_url_wide():
    url = update_typhoon_image.get_image_url("wide")
    assert "japan_wide-large.jpg" in url


def test_get_image_url_near():
    url = update_typhoon_image.get_image_url("near")
    assert "japan_near-large.jpg" in url


def test_get_image_url_detail_with_id():
    url1 = update_typhoon_image.get_image_url("detail", "2608")
    assert "typhoon_2608-large.jpg" in url1

    url2 = update_typhoon_image.get_image_url("detail", "typhoon_2607")
    assert "typhoon_2607-large.jpg" in url2


def test_get_image_url_detail_auto_success():
    dummy_html = '<html><body><a href="/typhoon_2607/">台風7号</a><a href="/typhoon_2608/">台風8号</a></body></html>'
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.read.return_value = dummy_html.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_res
        
        url = update_typhoon_image.get_image_url("detail")
        assert "typhoon_2608-large.jpg" in url


def test_get_image_url_detail_auto_fail_or_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection error")):
        url = update_typhoon_image.get_image_url("detail")
        assert url == update_typhoon_image.DEFAULT_IMAGE_URL


def test_download_image():
    with patch("urllib.request.urlopen") as mock_urlopen, \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes") as mock_write:
        
        mock_res = MagicMock()
        mock_res.read.return_value = b"fake_image_data"
        mock_urlopen.return_value.__enter__.return_value = mock_res
        
        update_typhoon_image.download_image("http://example.com/img.jpg", Path("/tmp/dest.jpg"))
        mock_write.assert_called_once_with(b"fake_image_data")


def test_send_overlay_event():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.read.return_value = b'{"status": "success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_res
        
        res = update_typhoon_image.send_overlay_event("http://localhost:8765/api/events", "token", "center")
        assert res == '{"status": "success"}'

        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_method() == "POST"
        assert req.headers["Content-type"] == "application/json"
        assert req.headers["Authorization"] == "Bearer token"

        body = json.loads(req.data.decode("utf-8"))
        assert body["type"] == "overlay"
        assert body["overlay_type"] == "image"
        assert "typhoon.jpg" in body["url"]


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/typhoon_2608-large.jpg")
@patch("scripts.update_typhoon_image.download_image")
@patch("scripts.update_typhoon_image.send_overlay_event", return_value='{"status": "ok"}')
def test_main_success_detail(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main(["--target-slot", "center", "--mode", "detail", "--typhoon-id", "2608"])
    assert ret == 0
    mock_download.assert_called_once_with("http://example.com/typhoon_2608-large.jpg", Path("/home/yuki/argos/src/argos/services/dashboard/static/typhoon.jpg"))
    
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[2] == "center"
    assert kwargs["title"] == "台風第8号情報"


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/typhoon_99-large.jpg")
@patch("scripts.update_typhoon_image.download_image")
@patch("scripts.update_typhoon_image.send_overlay_event", return_value='{"status": "ok"}')
def test_main_success_detail_short_id(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main(["--mode", "detail", "--typhoon-id", "99"])
    assert ret == 0
    
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[2] == "center"
    assert kwargs["title"] == "台風99情報"


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/img.jpg")
@patch("scripts.update_typhoon_image.download_image")
@patch("scripts.update_typhoon_image.send_overlay_event", return_value='{"status": "ok"}')
def test_main_success_near(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main(["--mode", "near"])
    assert ret == 0
    
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[2] == "center"
    assert kwargs["title"] == "台風情報（日本近海）"


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/img.jpg")
@patch("scripts.update_typhoon_image.download_image")
@patch("scripts.update_typhoon_image.send_overlay_event", return_value='{"status": "ok"}')
def test_main_success_wide(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main(["--mode", "wide"])
    assert ret == 0
    
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    assert args[2] == "center"
    assert kwargs["title"] == "台風情報（広域）"


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/img_failed.jpg")
@patch("scripts.update_typhoon_image.download_image", side_effect=[Exception("failed first"), b"success retry"])
@patch("scripts.update_typhoon_image.send_overlay_event", return_value='{"status": "ok"}')
def test_main_download_retry_success(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main([])
    assert ret == 0
    assert mock_download.call_count == 2


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/img_failed.jpg")
@patch("scripts.update_typhoon_image.download_image", side_effect=Exception("always fails"))
def test_main_download_fail(mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main([])
    assert ret == 1


@patch("scripts.update_typhoon_image.load_env_vars", return_value={})
@patch("scripts.update_typhoon_image.get_image_url", return_value="http://example.com/img.jpg")
@patch("scripts.update_typhoon_image.download_image")
@patch("scripts.update_typhoon_image.send_overlay_event", side_effect=Exception("API error"))
def test_main_send_fail(mock_send, mock_download, mock_get_url, mock_load_env):
    ret = update_typhoon_image.main([])
    assert ret == 1
