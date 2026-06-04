#!/usr/bin/env python3
"""YuNet/SFaceの顔認証モデルをローカルへ取得する。"""

from __future__ import annotations

import urllib.request
from pathlib import Path


MODEL_DIR = Path("~/.local/share/argos/face-models").expanduser()
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}


def main() -> None:
    """不足しているモデルファイルをダウンロードする。"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        path = MODEL_DIR / name
        if path.exists() and path.stat().st_size > 0:
            print(f"skip: {path}")
            continue
        print(f"download: {path}")
        urllib.request.urlretrieve(url, path)


if __name__ == "__main__":
    main()
