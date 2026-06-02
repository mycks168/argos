from pathlib import Path

from PIL import Image

from argos.services.face_auth import FaceAuthVerifier, hamming_distance, image_fingerprint


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    """テスト用の単色画像を保存する。"""
    Image.new("RGB", (32, 32), color).save(path)


def test_image_fingerprint_is_stable(tmp_path):
    """同じ画像は同じ指紋になる。"""
    image_path = tmp_path / "owner.jpg"
    _write_image(image_path, (80, 120, 160))

    assert image_fingerprint(image_path) == image_fingerprint(image_path)


def test_hamming_distance_counts_different_bits():
    """ハミング距離が差分ビット数を返す。"""
    assert hamming_distance("1010", "1001") == 2


def test_enroll_and_verify_matching_image(tmp_path):
    """登録画像と一致する撮影画像なら認証成功にする。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    verifier = FaceAuthVerifier(True, str(tmp_path / "samples"), "true", str(image_path), 0, 1)
    verifier.enroll(image_path)

    result = verifier.verify()

    assert result.authenticated is True
    assert result.score == 0


def test_verify_without_samples_fails(tmp_path):
    """登録サンプルが無い場合は認証失敗にする。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    verifier = FaceAuthVerifier(True, str(tmp_path / "samples"), "true", str(image_path), 0, 1)

    result = verifier.verify()

    assert result.authenticated is False
    assert "登録画像" in result.message
