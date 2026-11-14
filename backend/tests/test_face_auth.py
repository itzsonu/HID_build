import base64
import sqlite3
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.main import app
from app.services.face_recognition import (
    MultipleFacesDetectedError,
    NoFaceDetectedError,
)
from app.services.face_store import SQLiteFaceStore

client = TestClient(app)


@pytest.fixture
def isolated_store(monkeypatch, tmp_path: Path) -> SQLiteFaceStore:
    store = SQLiteFaceStore(tmp_path / "faces.sqlite3")
    monkeypatch.setattr(auth_routes, "face_store", store)
    return store


def jpeg_bytes() -> bytes:
    success, encoded = cv2.imencode(
        ".jpg",
        np.full((100, 120, 3), 127, dtype=np.uint8),
    )
    assert success
    return encoded.tobytes()


def request_image() -> str:
    return base64.b64encode(jpeg_bytes()).decode("ascii")


def registration_payload(
    user_id: str = "demo-user",
    consent: bool = True,
    image: str | None = None,
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "consent": consent,
        "image_base64": image if image is not None else request_image(),
    }


def verification_payload(
    user_id: str = "demo-user",
    image: str | None = None,
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "image_base64": image if image is not None else request_image(),
    }


def set_mock_embedding(monkeypatch, embeddings: list[np.ndarray]) -> list[bytes]:
    submitted_images: list[bytes] = []
    remaining = iter(embeddings)

    def extract(image: bytes) -> np.ndarray:
        submitted_images.append(image)
        return next(remaining)

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        extract,
    )
    return submitted_images


def unit_vector(index: int) -> np.ndarray:
    value = np.zeros(128, dtype=np.float32)
    value[index] = 1.0
    return value


def test_mocked_enrollment_stores_only_embedding_and_consent_time(
    monkeypatch, isolated_store
) -> None:
    image = jpeg_bytes()
    submitted_images = set_mock_embedding(monkeypatch, [unit_vector(0)])

    response = client.post(
        "/api/auth/register",
        json=registration_payload(image=base64.b64encode(image).decode("ascii")),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["registered"] is True
    assert body["user_id"] == "demo-user"
    assert "embedding" not in body
    assert "image_base64" not in body
    assert submitted_images == [image]

    with sqlite3.connect(isolated_store.database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(face_embeddings)")
        }
        stored = connection.execute(
            "SELECT embedding, consented_at FROM face_embeddings WHERE user_id = ?",
            ("demo-user",),
        ).fetchone()
    assert columns == {"user_id", "embedding", "consented_at", "created_at"}
    assert stored is not None
    assert len(stored[0]) == 128 * 4
    assert stored[1]
    assert image not in isolated_store.database_path.read_bytes()


def test_consent_false_rejects_enrollment_before_model_inference(
    monkeypatch, isolated_store
) -> None:
    def inference_must_not_run(_: bytes) -> np.ndarray:
        raise AssertionError("Enrollment inference must not run without consent.")

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        inference_must_not_run,
    )

    response = client.post(
        "/api/auth/register",
        json=registration_payload(consent=False),
    )

    assert response.status_code == 403
    assert "consent is required" in response.json()["detail"].lower()


def test_missing_consent_field_is_rejected(isolated_store) -> None:
    payload = registration_payload()
    payload.pop("consent")

    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 422
    assert "consent" in response.text


def test_invalid_jpeg_is_rejected(isolated_store) -> None:
    corrupted = base64.b64encode(b"\xff\xd8\xffnot-a-jpeg").decode("ascii")

    response = client.post(
        "/api/auth/register",
        json=registration_payload(image=corrupted),
    )

    assert response.status_code == 422
    assert "invalid or corrupted" in response.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (NoFaceDetectedError("No face was detected in the image."), "No face"),
        (
            MultipleFacesDetectedError("Exactly one face must be present in the image."),
            "Exactly one face",
        ),
    ],
)
def test_mocked_no_or_multiple_faces_are_rejected(
    monkeypatch, isolated_store, error: Exception, message: str
) -> None:
    def fail_detection(_: bytes) -> np.ndarray:
        raise error

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        fail_detection,
    )
    response = client.post("/api/auth/register", json=registration_payload())

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_duplicate_user_is_rejected_without_second_inference(
    monkeypatch, isolated_store
) -> None:
    submitted_images = set_mock_embedding(monkeypatch, [unit_vector(0)])
    first = client.post("/api/auth/register", json=registration_payload())
    second = client.post("/api/auth/register", json=registration_payload())

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(submitted_images) == 1


def test_unknown_user_is_rejected_before_inference(
    monkeypatch, isolated_store
) -> None:
    def inference_must_not_run(_: bytes) -> np.ndarray:
        raise AssertionError("Unknown identities must not run model inference.")

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        inference_must_not_run,
    )
    response = client.post("/api/auth/verify", json=verification_payload("unknown"))

    assert response.status_code == 404


def test_mocked_matching_embedding_returns_verified(isolated_store, monkeypatch) -> None:
    set_mock_embedding(monkeypatch, [unit_vector(0), unit_vector(0)])
    enrolled = client.post("/api/auth/register", json=registration_payload())
    verified = client.post("/api/auth/verify", json=verification_payload())

    assert enrolled.status_code == 201
    assert verified.status_code == 200
    body = verified.json()
    assert body["success"] is True
    assert body["identity_status"] == "VERIFIED"
    assert body["similarity"] == pytest.approx(1.0)
    assert body["threshold"] == pytest.approx(0.363)
    assert "liveness_status" not in body
    assert "decision" not in body


def test_mocked_non_matching_embedding_returns_not_verified(
    isolated_store, monkeypatch
) -> None:
    set_mock_embedding(monkeypatch, [unit_vector(0), unit_vector(1)])
    client.post("/api/auth/register", json=registration_payload())
    response = client.post("/api/auth/verify", json=verification_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["identity_status"] == "NOT_VERIFIED"
    assert body["similarity"] == pytest.approx(0.0)


def test_mocked_enrollment_persists_across_store_instances(
    isolated_store, monkeypatch
) -> None:
    embedding = unit_vector(3)
    set_mock_embedding(monkeypatch, [embedding])
    response = client.post("/api/auth/register", json=registration_payload())
    reopened_store = SQLiteFaceStore(isolated_store.database_path)
    stored = reopened_store.get_embedding("demo-user")

    assert response.status_code == 201
    assert stored is not None
    assert np.array_equal(
        np.frombuffer(stored, dtype="<f4"),
        embedding,
    )
