"""カメラ画像を使った簡易本人照合。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


FINGERPRINT_VERSION = 2
FINGERPRINT_SIZE = 16
_CV2_AUTO = object()


@dataclass(frozen=True)
class FaceAuthResult:
    """顔照合の結果。"""

    authenticated: bool
    message: str
    score: int | None = None
    image_path: str = ""


@dataclass(frozen=True)
class FaceDetectionResult:
    """顔検出の結果。"""

    available: bool
    face_count: int
    message: str
    boxes: tuple[tuple[int, int, int, int], ...] = ()
    rotation: int = 0


class FaceAuthVerifier:
    """登録済み画像と現在のカメラ画像を照合する。"""

    def __init__(
        self,
        enabled: bool,
        samples_dir: str,
        capture_command: str,
        capture_path: str,
        threshold: int,
        min_matches: int,
        detection_enabled: bool = True,
        min_detected_faces: int = 1,
        max_detected_faces: int = 1,
        image_rotation: int = 0,
    ) -> None:
        """顔照合の設定を保持する。"""
        self._enabled = enabled
        self._samples_dir = Path(samples_dir).expanduser()
        self._capture_command = capture_command
        self._capture_path = Path(capture_path).expanduser()
        self._threshold = threshold
        self._min_matches = max(1, min_matches)
        self._detection_enabled = detection_enabled
        self._min_detected_faces = min_detected_faces
        self._max_detected_faces = max_detected_faces
        self._image_rotation = image_rotation

    @property
    def enabled(self) -> bool:
        """顔照合が有効か返す。"""
        return self._enabled

    def verify(self) -> FaceAuthResult:
        """カメラで撮影し、登録済みサンプルと照合する。"""
        if not self._enabled:
            return FaceAuthResult(True, "顔認証は無効です。")
        samples = self._load_sample_hashes()
        if not samples:
            return FaceAuthResult(False, "顔認証の登録画像がありません。")
        try:
            captured_path = self.capture()
            detection = self.detect(captured_path)
            if not detection.available or detection.face_count < self._min_detected_faces:
                return FaceAuthResult(False, detection.message, image_path=str(captured_path))
            if detection.face_count > self._max_detected_faces:
                return FaceAuthResult(False, "複数の顔が検出されました。", image_path=str(captured_path))
            fingerprint = self._fingerprint(captured_path, detection)
        except Exception as exc:
            return FaceAuthResult(False, f"顔認証の撮影に失敗しました。{exc}")
        distances = [hamming_distance(fingerprint, sample) for sample in samples]
        best_score = min(distances)
        matches = sum(1 for distance in distances if distance <= self._threshold)
        if matches >= self._min_matches:
            return FaceAuthResult(True, "顔認証しました。", best_score, str(captured_path))
        return FaceAuthResult(False, "顔認証に失敗しました。", best_score, str(captured_path))

    def capture(self, output_path: Path | None = None) -> Path:
        """カメラ画像を撮影して保存する。"""
        path = output_path or self._capture_path
        path.parent.mkdir(parents=True, exist_ok=True)
        command = self._capture_command.format(path=str(path))
        subprocess.run(command, shell=True, check=True, timeout=8)
        rotate_saved_image(path, self._image_rotation)
        return path

    def detect(self, image_path: Path) -> FaceDetectionResult:
        """設定に応じて顔検出を実行する。"""
        if not self._detection_enabled:
            return FaceDetectionResult(True, 1, "顔検出は無効です。")
        return detect_faces(image_path)

    def enroll(self, image_path: Path, name: str = "owner") -> Path:
        """撮影済み画像を登録サンプルとして保存する。"""
        self._samples_dir.mkdir(parents=True, exist_ok=True)
        detection = self.detect(image_path)
        if not detection.available or detection.face_count < self._min_detected_faces:
            raise ValueError(detection.message)
        if detection.face_count > self._max_detected_faces:
            raise ValueError("複数の顔が検出されました。")
        fingerprint = self._fingerprint(image_path, detection)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        sample_path = self._samples_dir / f"{name}-{timestamp}.json"
        sample_path.write_text(
            json.dumps(
                {
                    "version": FINGERPRINT_VERSION,
                    "name": name,
                    "fingerprint": fingerprint,
                    "created_at": datetime.now().astimezone().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return sample_path

    def _fingerprint(self, image_path: Path, detection: FaceDetectionResult) -> str:
        """顔検出が有効なら顔領域だけの指紋を返す。"""
        if not self._detection_enabled or not detection.boxes:
            return image_fingerprint(image_path)
        box = max(detection.boxes, key=lambda item: item[2] * item[3])
        return image_fingerprint(image_path, box, detection.rotation)

    def _load_sample_hashes(self) -> list[str]:
        """登録済みサンプルのハッシュを読み込む。"""
        hashes: list[str] = []
        for sample_path in sorted(self._samples_dir.glob("*.json")):
            try:
                payload = json.loads(sample_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("version") != FINGERPRINT_VERSION:
                continue
            fingerprint = payload.get("fingerprint", "")
            if isinstance(fingerprint, str) and fingerprint:
                hashes.append(fingerprint)
        return hashes


def image_fingerprint(
    image_path: Path,
    box: tuple[int, int, int, int] | None = None,
    rotation: int = 0,
) -> str:
    """画像を明暗の指紋へ変換する。"""
    with Image.open(image_path) as image:
        image = _rotate_image_for_detection(image.convert("RGB"), rotation)
        if box is not None:
            image = _crop_box_with_margin(image, box)
        gray = image.convert("L").resize((FINGERPRINT_SIZE, FINGERPRINT_SIZE))
    pixels = list(gray.tobytes())
    average = sum(pixels) / len(pixels)
    return "".join("1" if pixel >= average else "0" for pixel in pixels)


def _rotate_image_for_detection(image: Image.Image, rotation: int) -> Image.Image:
    """OpenCV検出時と同じ向きにPillow画像を回転する。"""
    if rotation == 90:
        return image.rotate(-90, expand=True)
    if rotation == 180:
        return image.rotate(180, expand=True)
    if rotation == 270:
        return image.rotate(90, expand=True)
    return image


def rotate_saved_image(image_path: Path, rotation: int) -> None:
    """撮影画像を指定角度で回転して上書き保存する。"""
    normalized = rotation % 360
    if normalized == 0:
        return
    if normalized not in (90, 180, 270):
        raise ValueError("画像回転は 0, 90, 180, 270 のいずれかで指定してください。")
    with Image.open(image_path) as image:
        rotated = _rotate_image_for_detection(image.convert("RGB"), normalized)
        rotated.save(image_path)


def _crop_box_with_margin(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """顔の矩形に少し余白を付けて切り出す。"""
    x, y, width, height = box
    margin = int(max(width, height) * 0.18)
    left = max(0, x - margin)
    top = max(0, y - margin)
    right = min(image.width, x + width + margin)
    bottom = min(image.height, y + height + margin)
    return image.crop((left, top, right, bottom))


def detect_faces(image_path: Path, cv2_module: Any = _CV2_AUTO) -> FaceDetectionResult:
    """OpenCVで画像内の顔数を検出する。"""
    cv2 = _load_cv2() if cv2_module is _CV2_AUTO else cv2_module
    if cv2 is None:
        return FaceDetectionResult(False, 0, "OpenCVが未導入のため顔検出できません。")
    image = cv2.imread(str(image_path))
    if image is None:
        return FaceDetectionResult(False, 0, "顔検出用の画像を読み込めません。")
    cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml")
    classifier = cv2.CascadeClassifier(cascade_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if hasattr(cv2, "equalizeHist"):
        gray = cv2.equalizeHist(gray)
    best_rotation = 0
    best_faces: Any = ()
    for rotation, candidate in _face_detection_candidates(gray, cv2):
        faces = classifier.detectMultiScale(candidate, scaleFactor=1.03, minNeighbors=2, minSize=(50, 50))
        if len(faces) > len(best_faces):
            best_rotation = rotation
            best_faces = faces
    best_faces = _significant_faces(best_faces)
    count = len(best_faces)
    if count == 0:
        return FaceDetectionResult(True, 0, "顔が検出できません。")
    boxes = tuple(tuple(int(value) for value in face) for face in best_faces)
    return FaceDetectionResult(True, count, f"顔を{count}件検出しました。", boxes, best_rotation)


def _significant_faces(faces: Any) -> list[tuple[int, int, int, int]]:
    """最大顔に近い大きさの検出だけを残す。"""
    boxes = [tuple(int(value) for value in face) for face in faces]
    if not boxes:
        return []
    largest_area = max(width * height for _x, _y, width, height in boxes)
    return [box for box in boxes if box[2] * box[3] >= largest_area * 0.8]


def _face_detection_candidates(gray_image: Any, cv2: Any) -> list[tuple[int, Any]]:
    """回転した画像候補を返す。"""
    if not hasattr(cv2, "rotate"):
        return [(0, gray_image)]
    return [
        (0, gray_image),
        (90, cv2.rotate(gray_image, cv2.ROTATE_90_CLOCKWISE)),
        (180, cv2.rotate(gray_image, cv2.ROTATE_180)),
        (270, cv2.rotate(gray_image, cv2.ROTATE_90_COUNTERCLOCKWISE)),
    ]


def _load_cv2() -> Any:
    """OpenCVを遅延インポートする。"""
    try:
        import cv2
    except ImportError:
        return None
    return cv2


def hamming_distance(left: str, right: str) -> int:
    """2つの指紋の差分ビット数を返す。"""
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(1 for a, b in zip(left, right) if a != b)
