import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import liveness as liveness_routes
from app.main import app
from app.schemas.liveness import (
    LivenessChallenge,
    LivenessCheckRequest,
    LivenessPayloadTooLargeError,
)
from app.services.face_detection import DetectionResult, face_detector
from app.services.liveness import InMemoryChallengeStore

client = TestClient(app)
FRAME_COUNT = 8


@pytest.fixture
def challenge_store(monkeypatch) -> InMemoryChallengeStore:
    store = InMemoryChallengeStore()
    monkeypatch.setattr(liveness_routes, "challenge_store", store)
    return store


def jpeg_base64() -> str:
    image = np.full((100, 100, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def create_challenge(
    monkeypatch, challenge: LivenessChallenge | None = None
) -> dict[str, object]:
    if challenge is not None:
        monkeypatch.setattr(
            "app.services.liveness.secrets.choice",
            lambda _: challenge,
        )
    response = client.post("/api/liveness/challenge")
    assert response.status_code == 200
    return response.json()


def check_payload(
    challenge_id: str,
    frame: str | None = None,
    frame_count: int = FRAME_COUNT,
) -> dict[str, object]:
    return {
        "challenge_id": challenge_id,
        "frames": [frame if frame is not None else jpeg_base64()] * frame_count,
    }


def mock_face_centers(monkeypatch, centers: list[float]) -> None:
    next_center = iter(centers)

    def detect(_: bytes) -> DetectionResult:
        center = next(next_center)
        x = round(center * 100 - 20)
        return DetectionResult(100, 100, [(x, 30, 40, 40)])

    monkeypatch.setattr(face_detector, "detect", detect)


def mock_face_count(monkeypatch, count: int) -> None:
    faces = [(30, 30, 40, 40)] * count
    monkeypatch.setattr(
        face_detector,
        "detect",
        lambda _: DetectionResult(100, 100, faces),
    )


def test_challenge_generation_returns_random_supported_challenge(
    challenge_store,
) -> None:
    response = client.post("/api/liveness/challenge")

    assert response.status_code == 200
    body = response.json()
    assert body["challenge_id"]
    assert body["challenge"] in {"MOVE_LEFT", "MOVE_RIGHT"}
    assert body["expires_in_seconds"] == 60


def test_move_left_sequence_passes_experimental_heuristic(
    monkeypatch, challenge_store
) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_LEFT)
    mock_face_centers(monkeypatch, [0.72, 0.68, 0.64, 0.60, 0.56, 0.52, 0.48, 0.44])

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["liveness_status"] == "LIVE"
    assert body["movement_detected"] is True
    assert body["valid_frame_count"] == FRAME_COUNT
    assert body["total_frame_count"] == FRAME_COUNT
    assert body["confidence"] > 0
    assert "not proof of liveness" in body["message"]
    assert "identity_status" not in body
    assert "decision" not in body


def test_move_right_sequence_passes_experimental_heuristic(
    monkeypatch, challenge_store
) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_RIGHT)
    mock_face_centers(monkeypatch, [0.28, 0.32, 0.36, 0.40, 0.44, 0.48, 0.52, 0.56])

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 200
    assert response.json()["liveness_status"] == "LIVE"
    assert response.json()["movement_detected"] is True


def test_no_movement_fails_challenge_without_proving_spoof(
    monkeypatch, challenge_store
) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_LEFT)
    mock_face_centers(monkeypatch, [0.5] * FRAME_COUNT)

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 200
    assert response.json()["liveness_status"] == "SPOOF_SUSPECTED"
    assert response.json()["movement_detected"] is False
    assert "does not prove a spoof" in response.json()["message"]


def test_no_face_observations_return_uncertain(monkeypatch, challenge_store) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_LEFT)
    mock_face_count(monkeypatch, 0)

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 200
    assert response.json()["liveness_status"] == "UNCERTAIN"
    assert response.json()["valid_frame_count"] == 0


def test_five_valid_frames_out_of_ten_remain_uncertain(
    monkeypatch, challenge_store
) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_LEFT)
    detection_count = 0

    def detect_five_frames(_: bytes) -> DetectionResult:
        nonlocal detection_count
        detection_count += 1
        if detection_count % 2 == 0:
            return DetectionResult(100, 100, [])
        center = 0.72 - ((detection_count - 1) // 2) * 0.08
        return DetectionResult(100, 100, [(round(center * 100 - 20), 30, 40, 40)])

    monkeypatch.setattr(face_detector, "detect", detect_five_frames)
    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"], frame_count=10),
    )

    assert response.status_code == 200
    assert response.json()["liveness_status"] == "UNCERTAIN"
    assert response.json()["movement_detected"] is False
    assert response.json()["valid_frame_count"] == 5
    assert response.json()["total_frame_count"] == 10


def test_multiple_faces_return_uncertain(monkeypatch, challenge_store) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_RIGHT)
    mock_face_count(monkeypatch, 2)

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 200
    assert response.json()["liveness_status"] == "UNCERTAIN"
    assert response.json()["valid_frame_count"] == 0


def test_unknown_challenge_is_rejected(challenge_store) -> None:
    response = client.post(
        "/api/liveness/check",
        json=check_payload("unknown-challenge"),
    )

    assert response.status_code == 404


def test_expired_challenge_is_rejected(monkeypatch) -> None:
    now = [100.0]
    store = InMemoryChallengeStore(clock=lambda: now[0])
    monkeypatch.setattr(liveness_routes, "challenge_store", store)
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_LEFT)
    now[0] += 61

    response = client.post(
        "/api/liveness/check",
        json=check_payload(issued["challenge_id"]),
    )

    assert response.status_code == 410


def test_challenge_can_only_be_used_once(monkeypatch, challenge_store) -> None:
    issued = create_challenge(monkeypatch, LivenessChallenge.MOVE_RIGHT)
    mock_face_centers(monkeypatch, [0.28, 0.32, 0.36, 0.40, 0.44, 0.48, 0.52, 0.56])
    payload = check_payload(issued["challenge_id"])

    first_response = client.post("/api/liveness/check", json=payload)
    second_response = client.post("/api/liveness/check", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_invalid_base64_frame_is_rejected(challenge_store) -> None:
    response = client.post(
        "/api/liveness/check",
        json=check_payload("unknown-challenge", frame="not-valid-base64!"),
    )

    assert response.status_code == 422
    assert "not-valid-base64!" not in response.text


def test_too_few_frames_are_rejected(challenge_store) -> None:
    response = client.post(
        "/api/liveness/check",
        json=check_payload("unknown-challenge", frame_count=4),
    )

    assert response.status_code == 422
    assert "between 5 and 20" in response.text


def test_oversized_frame_is_rejected(challenge_store) -> None:
    oversized_frame = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode("ascii")
    response = client.post(
        "/api/liveness/check",
        json={
            "challenge_id": "unknown-challenge",
            "frames": [oversized_frame] + [jpeg_base64()] * 4,
        },
    )

    assert response.status_code == 413
    assert "5 MB" in response.text


def test_aggregate_frame_payload_is_bounded() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LivenessCheckRequest.model_validate(
            {
                "challenge_id": "test",
                "frames": ["a" * 4_194_305] * 5,
            }
        )
    assert isinstance(
        exc_info.value.errors()[0]["ctx"]["error"],
        LivenessPayloadTooLargeError,
    )
