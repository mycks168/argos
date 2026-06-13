"""ダッシュボード地図用のGPS現在地取得。"""

from __future__ import annotations

import os
import json
import select
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_GPS_DEVICE_PATH = Path("/dev/ttyACM0")
GPSD_HOST = "127.0.0.1"
GPSD_PORT = 2947


def read_location(
    provider: str,
    device_path: Path = DEFAULT_GPS_DEVICE_PATH,
    remote_url: str = "",
    timeout_seconds: float = 1.2,
) -> dict[str, Any]:
    """設定された取得元から地図表示用の現在地を返す。"""
    if provider == "remote":
        if not remote_url:
            return _unavailable("ARGOS_REMOTE_LOCATION_URL が未設定です")
        return read_remote_location(remote_url, timeout_seconds=timeout_seconds)
    return read_gps_location(device_path, timeout_seconds=timeout_seconds)


def read_gps_location(device_path: Path = DEFAULT_GPS_DEVICE_PATH, timeout_seconds: float = 1.2) -> dict[str, Any]:
    """gpsdを優先し、使えない場合だけGPSデバイスから現在地を返す。"""
    gpsd_location = read_gpsd_location(timeout_seconds=timeout_seconds)
    if gpsd_location["available"]:
        return gpsd_location
    device_location = read_nmea_device_location(device_path, timeout_seconds=timeout_seconds)
    if device_location["available"]:
        return device_location
    return _unavailable(f"{gpsd_location.get('error', '')}; {device_location.get('error', '')}".strip("; "))


def read_gpsd_location(timeout_seconds: float = 1.2) -> dict[str, Any]:
    """gpsdのJSONストリームから現在地を返す。"""
    deadline = time.monotonic() + timeout_seconds
    try:
        with socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=timeout_seconds) as gpsd_socket:
            gpsd_socket.settimeout(0.2)
            gpsd_socket.sendall(b'?WATCH={"enable":true,"json":true};\n')
            buffer = b""
            while time.monotonic() < deadline:
                try:
                    buffer += gpsd_socket.recv(4096)
                except TimeoutError:
                    continue
                except socket.timeout:
                    continue
                for line in buffer.decode("utf-8", errors="ignore").splitlines():
                    location = parse_gpsd_tpv(line)
                    if location:
                        return location
    except OSError as exc:
        return _unavailable(f"gpsdに接続できません: {exc}")
    return _unavailable("gpsdから有効な現在地を取得できません")


def read_nmea_device_location(device_path: Path = DEFAULT_GPS_DEVICE_PATH, timeout_seconds: float = 1.2) -> dict[str, Any]:
    """GPSデバイスから短時間だけNMEAを読み、現在地を返す。"""
    started_at = time.monotonic()
    buffer = b""
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return _unavailable(f"GPSデバイスを開けません: {exc}")
    try:
        while time.monotonic() - started_at < timeout_seconds:
            readable, _, _ = select.select([fd], [], [], 0.2)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            buffer += chunk
            lines = buffer.decode("ascii", errors="ignore").splitlines()
            for line in reversed(lines):
                location = parse_nmea_location(line)
                if location:
                    return location
    except OSError as exc:
        return _unavailable(f"GPS読み取りに失敗しました: {exc}")
    finally:
        os.close(fd)
    return _unavailable("GPSの有効な現在地を取得できません")


def read_remote_location(url: str, timeout_seconds: float = 2.0) -> dict[str, Any]:
    """リモートGPS APIから現在地を取得する。"""
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        return _unavailable(f"リモートGPSを取得できません: {exc}")
    if not isinstance(payload, dict):
        return _unavailable("リモートGPSのレスポンスがJSONオブジェクトではありません")
    return parse_remote_location(payload)


def parse_remote_location(payload: dict[str, Any]) -> dict[str, Any]:
    """カーロガーの /gps または /api/latest レスポンスを現在地形式へ変換する。"""
    point = payload.get("point")
    if isinstance(point, dict):
        lat = point.get("lat")
        lng = point.get("lng", point.get("lon"))
        updated_at = point.get("recorded_at") or payload.get("updated_at")
        speed_kmh = point.get("speed_kmh")
        course = point.get("course")
    else:
        lat = payload.get("lat")
        lng = payload.get("lng", payload.get("lon"))
        updated_at = payload.get("last_fix_at") or payload.get("updated_at")
        speed_kmh = payload.get("speed_kmh")
        course = payload.get("course")
    if not isinstance(lat, int | float) or not isinstance(lng, int | float):
        return _unavailable("リモートGPSの緯度経度が不正です")
    return {
        "available": True,
        "lat": float(lat),
        "lng": float(lng),
        "speed_kmh": float(speed_kmh) if isinstance(speed_kmh, int | float) else None,
        "course": float(course) if isinstance(course, int | float) else None,
        "updated_at": str(updated_at) if updated_at else datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "remote",
        "has_fix": payload.get("has_fix"),
    }


def parse_gpsd_tpv(line: str) -> dict[str, Any] | None:
    """gpsdのTPV JSONから地図表示用の現在地を取り出す。"""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if payload.get("class") != "TPV" or payload.get("mode", 0) < 2:
        return None
    lat = payload.get("lat")
    lng = payload.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lng, int | float):
        return None
    speed = payload.get("speed")
    return {
        "available": True,
        "lat": float(lat),
        "lng": float(lng),
        "speed_kmh": round(float(speed) * 3.6, 1) if isinstance(speed, int | float) else None,
        "course": float(payload["track"]) if isinstance(payload.get("track"), int | float) else None,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def parse_nmea_location(line: str) -> dict[str, Any] | None:
    """NMEAのRMC文から地図表示用の現在地を取り出す。"""
    parts = line.strip().split(",")
    if len(parts) < 10 or parts[0] not in {"$GPRMC", "$GNRMC"} or parts[2] != "A":
        return None
    lat = _parse_nmea_coordinate(parts[3], parts[4])
    lng = _parse_nmea_coordinate(parts[5], parts[6])
    if lat is None or lng is None:
        return None
    speed_knots = _optional_float(parts[7])
    course = _optional_float(parts[8])
    return {
        "available": True,
        "lat": lat,
        "lng": lng,
        "speed_kmh": round(speed_knots * 1.852, 1) if speed_knots is not None else None,
        "course": course,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _parse_nmea_coordinate(value: str, hemisphere: str) -> float | None:
    """NMEAのddmm.mmmm形式またはdddmm.mmmm形式を十進度へ変換する。"""
    if not value or hemisphere not in {"N", "S", "E", "W"}:
        return None
    degree_digits = 2 if hemisphere in {"N", "S"} else 3
    if len(value) <= degree_digits:
        return None
    try:
        degrees = int(value[:degree_digits])
        minutes = float(value[degree_digits:])
    except ValueError:
        return None
    coordinate = degrees + minutes / 60.0
    if hemisphere in {"S", "W"}:
        coordinate *= -1
    return coordinate


def _optional_float(value: str) -> float | None:
    """空文字をNoneとして扱い、数値ならfloatへ変換する。"""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _unavailable(error: str) -> dict[str, Any]:
    """現在地が取得できないレスポンスを返す。"""
    return {
        "available": False,
        "error": error,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
