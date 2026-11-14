import cv2
import numpy as np
import pytest

from app.services.face_recognition import (
    NoFaceDetectedError,
    SFACE_MODEL_PATH,
    SFaceEmbeddingService,
)


def blank_jpeg() -> bytes:
    success, encoded = cv2.imencode(
        ".jpg",
        np.full((240, 320, 3), 127, dtype=np.uint8),
    )
    assert success
    return encoded.tobytes()


def test_actual_yunet_and_sface_models_load_and_reject_blank_jpeg() -> None:
    assert SFACE_MODEL_PATH.is_file()
    service = SFaceEmbeddingService()

    with pytest.raises(NoFaceDetectedError):
        service.extract_embedding(blank_jpeg())


def test_actual_sface_cpu_inference_returns_embedding_for_synthetic_aligned_crop() -> None:
    recognizer = cv2.FaceRecognizerSF_create(str(SFACE_MODEL_PATH), "")
    synthetic_crop = np.zeros((112, 112, 3), dtype=np.uint8)
    embedding = recognizer.feature(synthetic_crop)

    assert embedding.shape == (1, 128)
    assert np.isfinite(embedding).all()


