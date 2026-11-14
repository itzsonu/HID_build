import base64
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.face_detection import MAX_IMAGE_SIZE_BYTES
from app.services.face_detection import (
    FaceDetectorUnavailableError,
    HaarFaceDetector,
    face_detector,
)

client = TestClient(app)


def encode_jpeg(image: np.ndarray) -> bytes:
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    return encoded.tobytes()


def encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def test_mocked_detector_returns_face_for_valid_jpeg(monkeypatch) -> None:
    synthetic_face = np.full((100, 200, 3), 240, dtype=np.uint8)
    cv2.ellipse(synthetic_face, (100, 50), (30, 40), 0, 0, 360, (180, 180, 180), -1)
    cv2.circle(synthetic_face, (88, 42), 4, (20, 20, 20), -1)
    cv2.circle(synthetic_face, (112, 42), 4, (20, 20, 20), -1)
    cv2.line(synthetic_face, (100, 45), (96, 58), (30, 30, 30), 2)
    cv2.ellipse(synthetic_face, (100, 68), (10, 4), 0, 0, 180, (20, 20, 20), 2)
    payload = encode_image(encode_jpeg(synthetic_face))

    class FakeCascade:
        def detectMultiScale(self, image, **kwargs):
            assert image.ndim == 2
            assert image.shape == (100, 200)
            assert kwargs["scaleFactor"] == 1.1
            assert kwargs["minSize"] == (24, 24)
            return ((25, 10, 50, 60),)

    monkeypatch.setattr(face_detector, "_load_cascade", lambda: FakeCascade())
    response = client.post(
        "/api/face/detect",
        json={"request_id": "test-face", "image_base64": payload},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["request_id"] == "test-face"
    assert body["face_detected"] is True
    assert body["face_count"] == 1
    assert body["faces"] == [{"x": 0.125, "y": 0.1, "width": 0.25, "height": 0.6}]
    assert body["processing_time_ms"] >= 0
    assert "liveness_status" not in body
    assert "identity_status" not in body
    assert "image_base64" not in body


def test_default_detector_accepts_smaller_faces() -> None:
    assert HaarFaceDetector().min_size == (24, 24)


def test_actual_haar_cascade_returns_no_faces_for_blank_jpeg() -> None:
    blank_image = np.full((100, 200, 3), 127, dtype=np.uint8)
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encode_jpeg(blank_image))},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["face_detected"] is False
    assert body["face_count"] == 0
    assert body["faces"] == []


def test_mocked_detector_returns_multiple_faces_at_image_boundaries(monkeypatch) -> None:
    image = np.full((100, 200, 3), 127, dtype=np.uint8)

    class FakeCascade:
        def detectMultiScale(self, image, **kwargs):
            return ((0, 0, 20, 10), (180, 90, 20, 10))

    monkeypatch.setattr(face_detector, "_load_cascade", lambda: FakeCascade())
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encode_jpeg(image))},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["face_count"] == 2
    assert body["faces"] == [
        {"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1},
        {"x": 0.9, "y": 0.9, "width": 0.1, "height": 0.1},
    ]


@pytest.mark.parametrize("height,width", [(80, 120), (240, 320)])
def test_mocked_detector_normalizes_different_image_dimensions(
    monkeypatch, height: int, width: int
) -> None:
    image = np.full((height, width, 3), 127, dtype=np.uint8)

    class FakeCascade:
        def detectMultiScale(self, grayscale, **kwargs):
            return ((width // 4, height // 4, width // 2, height // 2),)

    monkeypatch.setattr(face_detector, "_load_cascade", lambda: FakeCascade())
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encode_jpeg(image))},
    )

    assert response.status_code == 200
    assert response.json()["faces"] == [
        {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}
    ]


def test_actual_haar_cascade_safely_handles_very_small_jpeg() -> None:
    tiny_image = np.full((1, 1, 3), 127, dtype=np.uint8)
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encode_jpeg(tiny_image))},
    )

    assert response.status_code == 200
    assert response.json()["face_count"] == 0
    assert response.json()["faces"] == []


def test_missing_cascade_resource_returns_detector_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cv2.data, "haarcascades", str(tmp_path))
    detector = HaarFaceDetector()
    image = np.full((100, 100, 3), 127, dtype=np.uint8)

    with pytest.raises(FaceDetectorUnavailableError, match="unavailable"):
        detector.detect(encode_jpeg(image))


def test_invalid_cascade_resource_returns_detector_error(
    monkeypatch, tmp_path: Path
) -> None:
    cascade_path = tmp_path / "haarcascade_frontalface_default.xml"
    cascade_path.write_text("invalid cascade data", encoding="utf-8")
    monkeypatch.setattr(cv2.data, "haarcascades", str(tmp_path))
    detector = HaarFaceDetector()
    image = np.full((100, 100, 3), 127, dtype=np.uint8)

    with pytest.raises(
        FaceDetectorUnavailableError, match="could not be initialized"
    ):
        detector.detect(encode_jpeg(image))


def test_invalid_base64_returns_validation_error_without_echoing_input() -> None:
    invalid_payload = "not-valid-base64!"
    response = client.post(
        "/api/face/detect",
        json={"image_base64": invalid_payload},
    )

    assert response.status_code == 422
    assert "valid Base64" in response.text
    assert invalid_payload not in response.text


def test_empty_image_returns_validation_error() -> None:
    response = client.post("/api/face/detect", json={"image_base64": ""})

    assert response.status_code == 422
    assert "must not be empty" in response.text


def test_oversized_image_is_rejected_before_detection(monkeypatch) -> None:
    oversized_payload = encode_image(b"x" * (MAX_IMAGE_SIZE_BYTES + 1))

    def detector_must_not_run():
        raise AssertionError("Detector must not run for oversized input.")

    monkeypatch.setattr(face_detector, "_load_cascade", detector_must_not_run)
    response = client.post(
        "/api/face/detect",
        json={"image_base64": oversized_payload},
    )

    assert response.status_code == 413
    assert "5 MB" in response.text


def test_corrupted_jpeg_returns_validation_error() -> None:
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(b"\xff\xd8\xffnot-a-valid-jpeg")},
    )

    assert response.status_code == 422
    assert "invalid or corrupted" in response.text


def test_non_jpeg_image_is_rejected() -> None:
    image = np.full((20, 20, 3), 127, dtype=np.uint8)
    success, encoded_png = cv2.imencode(".png", image)
    assert success

    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encoded_png.tobytes())},
    )

    assert response.status_code == 415
    assert "Only JPEG" in response.text


def test_detector_failure_returns_safe_error(monkeypatch) -> None:
    image = np.full((100, 100, 3), 127, dtype=np.uint8)

    class BrokenCascade:
        def detectMultiScale(self, image, **kwargs):
            raise RuntimeError("private detector diagnostic")

    monkeypatch.setattr(face_detector, "_load_cascade", lambda: BrokenCascade())
    response = client.post(
        "/api/face/detect",
        json={"image_base64": encode_image(encode_jpeg(image))},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Face detector is unavailable."}
    assert "private detector diagnostic" not in response.text
