from pathlib import Path

from PIL import Image

from argos.services.face_auth import (
    FaceAuthVerifier,
    cosine_similarity,
    detect_faces,
    hamming_distance,
    image_fingerprint,
    rotate_saved_image,
)


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


def test_cosine_similarity_returns_face_score():
    """SFace特徴量のコサイン類似度を返す。"""
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_rotate_saved_image_rotates_file(tmp_path):
    """撮影画像の回転補正を保存する。"""
    image_path = tmp_path / "capture.jpg"
    Image.new("RGB", (20, 10), (80, 120, 160)).save(image_path)

    rotate_saved_image(image_path, 90)

    with Image.open(image_path) as image:
        assert image.size == (10, 20)


def test_enroll_and_verify_matching_image(tmp_path):
    """登録画像と一致する撮影画像なら認証成功にする。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    verifier = FaceAuthVerifier(True, str(tmp_path / "samples"), "true", str(image_path), 0, 1, False)
    verifier.enroll(image_path)

    result = verifier.verify()

    assert result.authenticated is True
    assert result.score == 0


def test_old_fingerprint_version_is_ignored(tmp_path):
    """古い形式の登録サンプルは照合に使わない。"""
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "old.json").write_text('{"version": 1, "fingerprint": "1111"}', encoding="utf-8")
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    verifier = FaceAuthVerifier(True, str(samples_dir), "true", str(image_path), 0, 1, False)

    result = verifier.verify()

    assert result.authenticated is False
    assert "登録画像" in result.message


def test_verify_without_samples_fails(tmp_path):
    """登録サンプルが無い場合は認証失敗にする。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    verifier = FaceAuthVerifier(True, str(tmp_path / "samples"), "true", str(image_path), 0, 1, False)

    result = verifier.verify()

    assert result.authenticated is False
    assert "登録画像" in result.message


def test_detect_faces_reports_missing_opencv(tmp_path):
    """OpenCV未導入なら顔検出不可として返す。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))

    result = detect_faces(image_path, cv2_module=None)

    assert result.available is False
    assert "OpenCV" in result.message


def test_detect_faces_counts_faces(tmp_path):
    """OpenCVの検出結果から顔数を返す。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))

    class FakeCascade:
        """顔検出器のテストダブル。"""

        def __init__(self, _path):
            """パスを受け取る。"""

        def detectMultiScale(self, *_args, **_kwargs):
            """顔検出結果を返す。"""
            return [(0, 0, 100, 100)]

    class FakeCv2:
        """OpenCVモジュールのテストダブル。"""

        COLOR_BGR2GRAY = 0
        CascadeClassifier = FakeCascade
        data = type("Data", (), {"haarcascades": str(tmp_path)})()

        @staticmethod
        def imread(_path):
            """画像読み込み結果を返す。"""
            return object()

        @staticmethod
        def cvtColor(image, _mode):
            """グレースケール画像を返す。"""
            return image

    result = detect_faces(image_path, cv2_module=FakeCv2)

    assert result.available is True
    assert result.face_count == 1


def test_detect_faces_uses_yunet_model(tmp_path):
    """YuNetモデルがある場合はDNN顔検出を使う。"""
    image_path = tmp_path / "capture.jpg"
    model_path = tmp_path / "yunet.onnx"
    _write_image(image_path, (20, 20, 20))
    model_path.write_bytes(b"model")

    class FakeImage:
        """画像shapeを持つテストダブル。"""

        shape = (480, 640, 3)

    class FakeDetector:
        """YuNet検出器のテストダブル。"""

        def detect(self, _image):
            """顔候補を返す。"""
            return 1, [[10.2, 20.8, 100.1, 120.9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.95]]

    class FakeCv2:
        """YuNet対応OpenCVモジュールのテストダブル。"""

        @staticmethod
        def imread(_path):
            """画像読み込み結果を返す。"""
            return FakeImage()

        @staticmethod
        def FaceDetectorYN_create(*_args):
            """YuNet検出器を返す。"""
            return FakeDetector()

    result = detect_faces(image_path, cv2_module=FakeCv2, yunet_model_path=str(model_path))

    assert result.face_count == 1
    assert result.boxes == ((10, 20, 100, 120),)


def test_detect_faces_prefers_original_orientation(tmp_path):
    """元画像で顔が見つかった場合は回転候補の誤検出を採用しない。"""
    image_path = tmp_path / "capture.jpg"
    _write_image(image_path, (20, 20, 20))
    original = object()
    rotated = object()

    class FakeCascade:
        """顔検出器のテストダブル。"""

        def __init__(self, _path):
            """パスを受け取る。"""

        def detectMultiScale(self, candidate, **_kwargs):
            """元画像と回転画像で異なる顔候補を返す。"""
            if candidate is original:
                return [(344, 203, 129, 129)]
            return [(329, 91, 54, 54), (372, 258, 97, 97)]

    class FakeCv2:
        """回転候補を持つOpenCVモジュールのテストダブル。"""

        COLOR_BGR2GRAY = 0
        ROTATE_90_CLOCKWISE = 90
        ROTATE_180 = 180
        ROTATE_90_COUNTERCLOCKWISE = 270
        CascadeClassifier = FakeCascade
        data = type("Data", (), {"haarcascades": str(tmp_path)})()

        @staticmethod
        def imread(_path):
            """画像読み込み結果を返す。"""
            return original

        @staticmethod
        def cvtColor(image, _mode):
            """グレースケール画像を返す。"""
            return image

        @staticmethod
        def rotate(_image, _rotation):
            """回転画像を返す。"""
            return rotated

    result = detect_faces(image_path, cv2_module=FakeCv2)

    assert result.rotation == 0
    assert result.boxes == ((344, 203, 129, 129),)
