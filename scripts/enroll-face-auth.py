#!/usr/bin/env python3
"""顔認証用の登録画像を撮影する。"""

from __future__ import annotations

import argparse

from argos.config import load_settings
from argos.services.face_auth import FaceAuthVerifier


def main() -> None:
    """設定に従ってカメラ撮影し、顔認証サンプルを保存する。"""
    parser = argparse.ArgumentParser(description="ARGOS 顔認証サンプル登録")
    parser.add_argument("--name", default="owner", help="登録名")
    parser.add_argument("--count", type=int, default=5, help="撮影する枚数")
    args = parser.parse_args()

    settings = load_settings()
    verifier = FaceAuthVerifier(
        True,
        settings.auth_face_samples_dir,
        settings.auth_face_capture_command,
        settings.auth_face_capture_path,
        settings.auth_face_threshold,
        settings.auth_face_min_matches,
        settings.auth_face_detection_enabled,
        settings.auth_face_min_detected_faces,
        settings.auth_face_max_detected_faces,
        settings.auth_face_image_rotation,
    )
    for index in range(args.count):
        image_path = verifier.capture()
        detection = verifier.detect(image_path)
        if not detection.available or detection.face_count != 1:
            print(f"{index + 1}/{args.count}: 登録しませんでした。{detection.message}")
            continue
        sample_path = verifier.enroll(image_path, args.name)
        print(f"{index + 1}/{args.count}: {sample_path}")


if __name__ == "__main__":
    main()
