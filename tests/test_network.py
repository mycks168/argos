from pathlib import Path

from argos.services import network
from argos.services.network import read_wifi_status


def test_read_wifi_status_parses_proc_wireless_and_ssid(monkeypatch, tmp_path):
    """Wi-Fi品質とSSIDを読み取れる。"""
    wireless = tmp_path / "wireless"
    wireless.write_text(
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlan0: 0000   49.  -55.  -256        0      0      0      0      0        0\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(network, "_read_ssid", lambda interface: "CarWiFi" if interface == "wlan0" else "")

    status = read_wifi_status(wireless)

    assert status.connected is True
    assert status.interface == "wlan0"
    assert status.ssid == "CarWiFi"
    assert status.quality == 70
    assert status.level_dbm == -55
    assert status.to_dict()["connected"] is True


def test_read_wifi_status_handles_disconnected_and_missing_file(monkeypatch, tmp_path):
    """SSIDなしやファイルなしは未接続として扱う。"""
    wireless = tmp_path / "wireless"
    wireless.write_text(
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlan0: 0000    0.  -256. -256        0      0      0      0      0        0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(network, "_read_ssid", lambda _interface: "")

    status = read_wifi_status(wireless)
    missing = read_wifi_status(Path(tmp_path / "missing"))

    assert status.connected is False
    assert status.quality == 0
    assert missing.connected is False
    assert missing.interface == ""


def test_read_wifi_status_ignores_malformed_wireless_lines(monkeypatch, tmp_path):
    """壊れたwireless情報は取得不能として扱う。"""
    wireless = tmp_path / "wireless"
    wireless.write_text(
        "header\n"
        "header\n"
        "bad line without separator\n"
        "wlan0: 0000 only-one-field\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(network, "_read_ssid", lambda _interface: "ignored")

    status = read_wifi_status(wireless)

    assert status.connected is False
    assert status.quality is None


def test_read_wifi_status_handles_invalid_quality_values(monkeypatch, tmp_path):
    """数値化できない品質値はNoneとして扱う。"""
    wireless = tmp_path / "wireless"
    wireless.write_text(
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlan0: 0000   bad  also-bad  -256        0      0      0      0      0        0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(network, "_read_ssid", lambda _interface: "CarWiFi")

    status = read_wifi_status(wireless)

    assert status.connected is True
    assert status.quality is None
    assert status.level_dbm is None


def test_read_ssid_handles_iwgetid_results(monkeypatch):
    """iwgetidの成功、失敗、起動不能を扱える。"""

    class Result:
        def __init__(self, returncode, stdout):
            """偽のsubprocess結果を作る。"""
            self.returncode = returncode
            self.stdout = stdout

    monkeypatch.setattr(network, "_iwgetid_command", lambda: "iwgetid")
    monkeypatch.setattr(network.subprocess, "run", lambda *_args, **_kwargs: Result(0, "CarWiFi\n"))
    assert network._read_ssid("wlan0") == "CarWiFi"

    monkeypatch.setattr(network.subprocess, "run", lambda *_args, **_kwargs: Result(1, ""))
    assert network._read_ssid("wlan0") == ""

    def raise_os_error(*_args, **_kwargs):
        """iwgetidが実行できない状態を作る。"""
        raise OSError("missing")

    monkeypatch.setattr(network.subprocess, "run", raise_os_error)
    assert network._read_ssid("wlan0") == ""
