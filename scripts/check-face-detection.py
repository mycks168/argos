#!/usr/bin/env python3
"""カメラで1枚撮影して顔検出結果を確認する。"""

from __future__ import annotations

import shutil
from pathlib import Path

from argos.config import load_settings
from argos.services.dashboard.server import DEFAULT_CAMERA_SNAPSHOT_PATH
from argos.services.face_auth import FaceAuthVerifier


def main() -> None:
    """設定に従って撮影し、顔検出結果を標準出力へ出す。"""
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
        settings.auth_face_detector_model_path,
        settings.auth_face_recognizer_model_path,
        settings.auth_face_sface_threshold,
    )
    image_path = verifier.capture()
    detection = verifier.detect(image_path)
    DEFAULT_CAMERA_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image_path, DEFAULT_CAMERA_SNAPSHOT_PATH)
    print(f"画像: {Path(image_path)}")
    print(f"顔検出: {detection.face_count}件")
    print(detection.message)


if __name__ == "__main__":
    main()
