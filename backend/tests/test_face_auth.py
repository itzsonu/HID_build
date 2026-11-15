import base64
import sqlite3
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes import admin as admin_routes
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.main import app
from app.schemas.auth import IdentityStatus
from app.services.face_recognition import (
    MultipleFacesDetectedError,
    NoFaceDetectedError,
    PoorImageQualityError,
    SFaceEmbeddingService,
)
from app.services.face_authentication import identify_enrolled_face, verify_claimed_user
from app.services.face_store import SQLiteFaceStore

ADMIN_SECRET = "unit-test-admin-secret"
client = TestClient(
    app,
    headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
)


@pytest.fixture(autouse=True)
def configured_admin_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_SECRET", SecretStr(ADMIN_SECRET))


@pytest.fixture
def isolated_store(monkeypatch, tmp_path: Path) -> SQLiteFaceStore:
    store = SQLiteFaceStore(tmp_path / "faces.sqlite3")
    monkeypatch.setattr(auth_routes, "face_store", store)
    monkeypatch.setattr(admin_routes, "face_store", store)
    return store


def jpeg_bytes(value: int = 127) -> bytes:
    success, encoded = cv2.imencode(
        ".jpg",
        np.full((100, 120, 3), value, dtype=np.uint8),
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


def batch_registration_payload(
    images: list[bytes] | None = None,
    *,
    consent: bool = True,
) -> dict[str, object]:
    selected = images or [jpeg_bytes(90), jpeg_bytes(150)]
    return {
        "user_id": "demo-user",
        "consent": consent,
        "images_base64": [
            base64.b64encode(image).decode("ascii")
            for image in selected
        ],
    }


def set_mock_batch_embeddings(
    monkeypatch,
    results: list[np.ndarray | Exception],
) -> list[tuple[bytes, bool]]:
    submitted: list[tuple[bytes, bool]] = []
    remaining = iter(results)

    def extract(image: bytes, *, require_quality: bool = False) -> np.ndarray:
        submitted.append((image, require_quality))
        value = next(remaining)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        extract,
    )
    return submitted


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


def test_admin_enrollment_routes_fail_closed_without_configured_secret(
    monkeypatch, isolated_store
) -> None:
    monkeypatch.setattr(settings, "ADMIN_SECRET", None)
    response = client.get("/api/admin/enrollments")

    assert response.status_code == 503
    assert "configure ADMIN_SECRET" in response.json()["detail"]


def test_admin_enrollment_routes_reject_invalid_secret(isolated_store) -> None:
    response = client.get(
        "/api/admin/enrollments",
        headers={"Authorization": "Bearer incorrect"},
    )

    assert response.status_code == 401
    assert "enrollments" not in response.json()


def test_admin_can_list_template_counts_and_delete_user(isolated_store) -> None:
    isolated_store.upsert_templates(
        "user-one",
        [unit_vector(0), unit_vector(1)],
        ["0" * 64, "1" * 64],
    )
    isolated_store.upsert_template("user-two", unit_vector(2))

    listed = client.get("/api/admin/enrollments")

    assert listed.status_code == 200
    assert listed.json() == {
        "enrollments": [
            {"user_id": "user-one", "template_count": 2},
            {"user_id": "user-two", "template_count": 1},
        ]
    }
    assert "embedding" not in listed.text

    deleted = client.request(
        "DELETE",
        "/api/admin/enrollments",
        json={"user_id": "user-one"},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "user_id": "user-one"}
    assert isolated_store.get_templates("user-one") == []
    assert isolated_store.get_embedding("user-one") is None

    missing = client.request(
        "DELETE",
        "/api/admin/enrollments",
        json={"user_id": "missing"},
    )
    assert missing.status_code == 404


def test_single_and_batch_enrollment_require_admin_secret(
    monkeypatch, isolated_store
) -> None:
    monkeypatch.setattr(settings, "ADMIN_SECRET", None)
    single = client.post("/api/auth/register", json=registration_payload())
    batch = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload(),
    )

    assert single.status_code == 503
    assert batch.status_code == 503


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


def test_batch_enrollment_deduplicates_and_persists_each_distinct_template(
    monkeypatch, isolated_store
) -> None:
    images = [jpeg_bytes(90), jpeg_bytes(150), jpeg_bytes(210)]
    submitted = set_mock_batch_embeddings(
        monkeypatch,
        [unit_vector(0), unit_vector(0), unit_vector(1)],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload(images),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["registered"] is True
    assert body["updated"] is False
    assert body["accepted_photo_count"] == 2
    assert body["duplicate_photo_count"] == 1
    assert [item["status"] for item in body["photo_results"]] == [
        "ACCEPTED",
        "DUPLICATE",
        "ACCEPTED",
    ]
    assert "decision" not in body
    assert "image_base64" not in body
    assert [image for image, _ in submitted] == images
    assert all(require_quality for _, require_quality in submitted)
    persisted = SQLiteFaceStore(isolated_store.database_path).get_all_embeddings()
    assert [user_id for user_id, _ in persisted] == ["demo-user"]
    templates = SQLiteFaceStore(isolated_store.database_path).get_templates("demo-user")
    assert len(templates) == 2
    assert all(len(template) == 128 * 4 for template in templates)
    database_bytes = isolated_store.database_path.read_bytes()
    assert all(image not in database_bytes for image in images)


def test_exact_duplicate_reference_is_not_inferred_or_stored(
    monkeypatch, isolated_store
) -> None:
    repeated = jpeg_bytes(90)
    images = [repeated, repeated, jpeg_bytes(150)]
    submitted = set_mock_batch_embeddings(
        monkeypatch,
        [unit_vector(0), unit_vector(1)],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload(images),
    )

    assert response.status_code == 200
    assert response.json()["registered"] is True
    assert response.json()["duplicate_photo_count"] == 1
    assert [item["status"] for item in response.json()["photo_results"]] == [
        "ACCEPTED",
        "DUPLICATE",
        "ACCEPTED",
    ]
    assert [image for image, _ in submitted] == [repeated, images[2]]
    assert len(isolated_store.get_templates("demo-user")) == 2


def test_existing_aggregate_template_migrates_without_deletion(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    embedding = unit_vector(11).astype("<f4").tobytes()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE face_embeddings (
                user_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL CHECK(length(embedding) = 512),
                consented_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO face_embeddings
                (user_id, embedding, consented_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("legacy-user", embedding, "consented", "created"),
        )

    store = SQLiteFaceStore(database_path)
    migrated = store.get_templates("legacy-user")
    with sqlite3.connect(database_path) as connection:
        legacy_row = connection.execute(
            "SELECT embedding, consented_at FROM face_embeddings WHERE user_id = ?",
            ("legacy-user",),
        ).fetchone()
        template_row = connection.execute(
            "SELECT photo_sha256 FROM face_templates WHERE user_id = ?",
            ("legacy-user",),
        ).fetchone()

    assert migrated == [embedding]
    assert legacy_row == (embedding, "consented")
    assert template_row == (None,)
    assert store.get_templates("legacy-user") == [embedding]


def test_batch_enrollment_requires_consent_before_inference(
    monkeypatch, isolated_store
) -> None:
    def inference_must_not_run(_: bytes, *, require_quality: bool = False) -> np.ndarray:
        raise AssertionError("Batch inference must not run without consent.")

    monkeypatch.setattr(
        auth_routes.face_embedding_service,
        "extract_embedding",
        inference_must_not_run,
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload(consent=False),
    )

    assert response.status_code == 403
    assert "consent is required" in response.json()["detail"].lower()


def test_batch_enrollment_updates_an_existing_template(monkeypatch, isolated_store) -> None:
    isolated_store.upsert_template("demo-user", unit_vector(0))
    set_mock_batch_embeddings(
        monkeypatch,
        [unit_vector(1), unit_vector(2)],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload([jpeg_bytes(90), jpeg_bytes(150)]),
    )

    assert response.status_code == 200
    assert response.json()["registered"] is True
    assert response.json()["updated"] is True
    stored_blob = isolated_store.get_embedding("demo-user")
    assert stored_blob is not None
    stored = np.frombuffer(stored_blob, dtype="<f4")
    assert not np.array_equal(stored, unit_vector(0))


def test_batch_enrollment_reports_rejected_photo_without_saving_partial_template(
    monkeypatch, isolated_store
) -> None:
    submitted = set_mock_batch_embeddings(
        monkeypatch,
        [NoFaceDetectedError("No face was detected."), unit_vector(1)],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload([jpeg_bytes(90), jpeg_bytes(150)]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["registered"] is False
    assert body["accepted_photo_count"] == 1
    assert body["rejected_photo_count"] == 1
    assert [photo["status"] for photo in body["photo_results"]] == [
        "REJECTED",
        "ACCEPTED",
    ]
    assert isolated_store.get_all_embeddings() == []
    assert len(submitted) == 2


def test_batch_enrollment_rejects_a_photo_with_multiple_faces(
    monkeypatch, isolated_store
) -> None:
    set_mock_batch_embeddings(
        monkeypatch,
        [
            MultipleFacesDetectedError("Exactly one face must be present."),
            unit_vector(1),
        ],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload([jpeg_bytes(90), jpeg_bytes(150)]),
    )

    assert response.status_code == 200
    assert response.json()["photo_results"][0]["status"] == "REJECTED"
    assert "Exactly one face" in response.json()["photo_results"][0]["message"]


def test_batch_payload_rejects_non_jpeg_and_invalid_photo_count(isolated_store) -> None:
    non_jpeg = base64.b64encode(b"not-a-jpeg").decode("ascii")
    invalid_format = client.post(
        "/api/auth/register/batch",
        json={
            "user_id": "demo-user",
            "consent": True,
            "images_base64": [non_jpeg, non_jpeg],
        },
    )
    invalid_count = client.post(
        "/api/auth/register/batch",
        json={
            "user_id": "demo-user",
            "consent": True,
            "images_base64": [request_image()],
        },
    )

    assert invalid_format.status_code == 422
    assert "JPEG" in invalid_format.text
    assert invalid_count.status_code == 422


def test_automatic_identity_lookup_handles_unique_missing_and_ambiguous_matches(
    isolated_store,
) -> None:
    class MockEmbeddingService:
        threshold = 0.363

        def __init__(self, vector: np.ndarray) -> None:
            self.vector = vector
            self.quality_checks: list[bool] = []

        def extract_embedding(
            self,
            _: bytes,
            *,
            require_quality: bool = False,
        ) -> np.ndarray:
            self.quality_checks.append(require_quality)
            return self.vector

    isolated_store.upsert_templates(
        "user-one",
        [unit_vector(0), unit_vector(3)],
        ["0" * 64, "1" * 64],
    )
    unique_service = MockEmbeddingService(unit_vector(3))
    unique = identify_enrolled_face(
        b"current-frame",
        isolated_store,
        unique_service,  # type: ignore[arg-type]
    )
    assert unique.identity_status is IdentityStatus.VERIFIED
    assert unique.matched_user_id == "user-one"
    assert unique_service.quality_checks == [True]

    no_match_service = MockEmbeddingService(unit_vector(2))
    no_match = identify_enrolled_face(
        b"current-frame",
        isolated_store,
        no_match_service,  # type: ignore[arg-type]
    )
    assert no_match.identity_status is IdentityStatus.NOT_VERIFIED
    assert no_match.matched_user_id is None

    isolated_store.upsert_template("user-two", unit_vector(3))
    ambiguous = identify_enrolled_face(
        b"current-frame",
        isolated_store,
        unique_service,  # type: ignore[arg-type]
    )
    assert ambiguous.identity_status is IdentityStatus.AMBIGUOUS
    assert ambiguous.matched_user_id is None


def test_claimed_identity_must_match_and_be_the_strongest_enrolled_profile(
    isolated_store,
) -> None:
    class MockEmbeddingService:
        threshold = 0.363

        def __init__(self, vector: np.ndarray) -> None:
            self.vector = vector
            self.calls = 0

        def extract_embedding(
            self,
            _: bytes,
            *,
            require_quality: bool = False,
        ) -> np.ndarray:
            assert require_quality is True
            self.calls += 1
            return self.vector

    isolated_store.upsert_template("user-one", unit_vector(0))
    isolated_store.upsert_template("user-two", unit_vector(1))

    correct = verify_claimed_user(
        "user-one", b"frame", isolated_store, MockEmbeddingService(unit_vector(0))
    )
    assert correct.identity_status is IdentityStatus.VERIFIED
    assert correct.matched_user_id == "user-one"

    wrong = verify_claimed_user(
        "user-one", b"frame", isolated_store, MockEmbeddingService(unit_vector(1))
    )
    assert wrong.identity_status is IdentityStatus.NOT_VERIFIED
    assert wrong.matched_user_id is None

    missing_service = MockEmbeddingService(unit_vector(0))
    missing = verify_claimed_user(
        "unknown", b"frame", isolated_store, missing_service
    )
    assert missing.identity_status is IdentityStatus.NOT_CHECKED
    assert missing_service.calls == 0


def test_batch_quality_rejection_is_reported_per_photo(
    monkeypatch, isolated_store
) -> None:
    set_mock_batch_embeddings(
        monkeypatch,
        [PoorImageQualityError("The photo is too blurred."), unit_vector(2)],
    )

    response = client.post(
        "/api/auth/register/batch",
        json=batch_registration_payload([jpeg_bytes(90), jpeg_bytes(150)]),
    )

    assert response.status_code == 200
    assert response.json()["photo_results"][0]["message"] == "The photo is too blurred."
    assert response.json()["photo_results"][0]["status"] == "REJECTED"


def test_image_quality_gate_rejects_small_and_blurred_inputs() -> None:
    with pytest.raises(PoorImageQualityError, match="too small"):
        SFaceEmbeddingService._validate_image_quality(
            np.zeros((80, 120, 3), dtype=np.uint8)
        )

    with pytest.raises(PoorImageQualityError, match="blurred"):
        SFaceEmbeddingService._validate_image_quality(
            np.full((120, 120, 3), 127, dtype=np.uint8)
        )
