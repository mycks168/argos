"""カメラ画像を使った簡易本人照合。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image


FINGERPRINT_VERSION = 1
FINGERPRINT_SIZE = 16


@dataclass(frozen=True)
class FaceAuthResult:
    """顔照合の結果。"""

    authenticated: bool
    message: str
    score: int | None = None
    image_path: str = ""


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
    ) -> None:
        """顔照合の設定を保持する。"""
        self._enabled = enabled
        self._samples_dir = Path(samples_dir).expanduser()
        self._capture_command = capture_command
        self._capture_path = Path(capture_path).expanduser()
        self._threshold = threshold
        self._min_matches = max(1, min_matches)

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
            fingerprint = image_fingerprint(captured_path)
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
        return path

    def enroll(self, image_path: Path, name: str = "owner") -> Path:
        """撮影済み画像を登録サンプルとして保存する。"""
        self._samples_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = image_fingerprint(image_path)
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

    def _load_sample_hashes(self) -> list[str]:
        """登録済みサンプルのハッシュを読み込む。"""
        hashes: list[str] = []
        for sample_path in sorted(self._samples_dir.glob("*.json")):
            try:
                payload = json.loads(sample_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            fingerprint = payload.get("fingerprint", "")
            if isinstance(fingerprint, str) and fingerprint:
                hashes.append(fingerprint)
        return hashes


def image_fingerprint(image_path: Path) -> str:
    """画像を明暗の指紋へ変換する。"""
    with Image.open(image_path) as image:
        gray = image.convert("L").resize((FINGERPRINT_SIZE, FINGERPRINT_SIZE))
    pixels = list(gray.tobytes())
    average = sum(pixels) / len(pixels)
    return "".join("1" if pixel >= average else "0" for pixel in pixels)


def hamming_distance(left: str, right: str) -> int:
    """2つの指紋の差分ビット数を返す。"""
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(1 for a, b in zip(left, right) if a != b)
