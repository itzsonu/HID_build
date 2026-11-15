import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.routes import access as access_routes
from app.main import app
from app.schemas.access import AccessDecision, ScheduleStatus
from app.schemas.auth import IdentityStatus
from app.schemas.liveness import LivenessChallenge, LivenessStatus
from app.services.face_detection import DetectionResult, face_detector
from app.services.access_decision import decide_access
from app.services.campus_schedule import (
    CampusScheduleEvaluator,
    ScheduleEvaluation,
)
from app.services.face_authentication import (
    FaceIdentityEvaluation,
    verify_claimed_user,
)
from app.services.liveness import (
    InMemoryChallengeStore,
    LivenessEvaluation,
    MovementAnalyzer,
)
from app.services.face_recognition import PoorImageQualityError
from app.services.face_store import SQLiteFaceStore

client = TestClient(app)
FRAME_COUNT = 8
JPEG_FRAMES = [
    base64.b64encode(b"\xff\xd8\xff" + bytes([index]) + b"\xff\xd9").decode("ascii")
    for index in range(FRAME_COUNT)
]


@pytest.fixture
def access_environment(monkeypatch):
    store = InMemoryChallengeStore()
    state = {
        "liveness": LivenessEvaluation(
            LivenessStatus.UNCERTAIN,
            0.9,
            True,
            FRAME_COUNT,
            FRAME_COUNT,
            "Movement alone cannot establish liveness.",
            5,
        ),
        "identity": FaceIdentityEvaluation(
            IdentityStatus.VERIFIED,
            0.9,
            0.363,
            "Mocked identity result.",
            "demo-user",
        ),
        "schedule": ScheduleEvaluation(ScheduleStatus.WITHIN_SCHEDULE),
        "identity_frames": [],
        "automatic_identity_frames": [],
    }

    def evaluate_liveness(challenge, frames):
        assert challenge in LivenessChallenge
        assert len(frames) == FRAME_COUNT
        return state["liveness"]

    def verify_identity(user_id, image_bytes, face_store, embedding_service):
        state["identity_frames"].append((user_id, image_bytes))
        return state["identity"]

    def identify_identity(image_bytes, face_store, embedding_service):
        state["automatic_identity_frames"].append(image_bytes)
        return state["identity"]

    monkeypatch.setattr(access_routes, "challenge_store", store)
    monkeypatch.setattr(
        access_routes.movement_analyzer,
        "evaluate",
        evaluate_liveness,
    )
    monkeypatch.setattr(access_routes, "verify_claimed_user", verify_identity)
    monkeypatch.setattr(access_routes, "identify_enrolled_face", identify_identity)
    monkeypatch.setattr(
        access_routes.campus_schedule,
        "evaluate",
        lambda: state["schedule"],
    )
    return store, state


def issue_challenge(store: InMemoryChallengeStore) -> str:
    challenge_id, _ = store.create()
    return challenge_id


def decision_payload(challenge_id: str, **extra: object) -> dict[str, object]:
    return {
        "user_id": "demo-user",
        "challenge_id": challenge_id,
        "frames": JPEG_FRAMES,
        **extra,
    }


def test_movement_analyzer_records_last_valid_frame_for_identity_reuse(
    monkeypatch,
) -> None:
    no_face = DetectionResult(100, 100, [])
    one_face = DetectionResult(100, 100, [(30, 20, 40, 50)])
    detections = iter([no_face, one_face, no_face, no_face, no_face, one_face, no_face, no_face])
    monkeypatch.setattr(face_detector, "detect", lambda _: next(detections))

    evaluation = MovementAnalyzer().evaluate(
        LivenessChallenge.MOVE_LEFT,
        [b"mocked-frame"] * FRAME_COUNT,
    )

    assert evaluation.valid_frame_count == 2
    assert evaluation.last_valid_frame_index == 5


def test_uncertain_liveness_with_verified_identity_steps_up_and_reuses_last_valid_frame(
    access_environment,
) -> None:
    store, state = access_environment
    challenge_id = issue_challenge(store)

    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "STEP_UP_VERIFICATION"
    assert body["liveness_status"] == "UNCERTAIN"
    assert body["identity_status"] == "VERIFIED"
    assert body["schedule_status"] == "WITHIN_SCHEDULE"
    assert body["requires_step_up"] is True
    assert "no tested presentation-attack detection model is configured" in body["reason"]
    assert body["processing_time_ms"] >= 0
    assert body["matched_user_id"] == "demo-user"
    assert state["identity_frames"] == [
        ("demo-user", base64.b64decode(JPEG_FRAMES[5]))
    ]


def test_matching_motion_sequence_cannot_allow_video_replay(
    access_environment, monkeypatch
) -> None:
    store, _ = access_environment
    challenge = LivenessChallenge.MOVE_RIGHT
    monkeypatch.setattr("app.services.liveness.secrets.choice", lambda _: challenge)
    challenge_id = issue_challenge(store)
    centers = iter([0.28, 0.32, 0.36, 0.40, 0.44, 0.48, 0.52, 0.56])

    def detect_moving_face(_: bytes) -> DetectionResult:
        center = next(centers)
        x = round(center * 100 - 20)
        return DetectionResult(100, 100, [(x, 30, 40, 40)])

    monkeypatch.setattr(face_detector, "detect", detect_moving_face)
    monkeypatch.setattr(
        access_routes.movement_analyzer,
        "evaluate",
        MovementAnalyzer().evaluate,
    )

    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "STEP_UP_VERIFICATION"
    assert body["liveness_status"] == "UNCERTAIN"
    assert body["movement_detected"] is True
    assert body["identity_status"] == "VERIFIED"
    assert body["schedule_status"] == "WITHIN_SCHEDULE"


@pytest.mark.parametrize(
    ("liveness", "identity", "schedule", "expected"),
    [
        (
            LivenessStatus.LIVE,
            IdentityStatus.VERIFIED,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.STEP_UP_VERIFICATION,
        ),
        (
            LivenessStatus.LIVE,
            IdentityStatus.VERIFIED,
            ScheduleStatus.OUTSIDE_SCHEDULE,
            AccessDecision.STEP_UP_VERIFICATION,
        ),
        (
            LivenessStatus.UNCERTAIN,
            IdentityStatus.VERIFIED,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.STEP_UP_VERIFICATION,
        ),
        (
            LivenessStatus.SPOOF_SUSPECTED,
            IdentityStatus.VERIFIED,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.DENY,
        ),
        (
            LivenessStatus.LIVE,
            IdentityStatus.NOT_VERIFIED,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.DENY,
        ),
        (
            LivenessStatus.LIVE,
            IdentityStatus.NOT_CHECKED,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.DENY,
        ),
        (
            LivenessStatus.LIVE,
            IdentityStatus.AMBIGUOUS,
            ScheduleStatus.WITHIN_SCHEDULE,
            AccessDecision.STEP_UP_VERIFICATION,
        ),
        (
            LivenessStatus.LIVE,
            IdentityStatus.VERIFIED,
            ScheduleStatus.UNAVAILABLE,
            AccessDecision.STEP_UP_VERIFICATION,
        ),
    ],
)
def test_mocked_decision_policy(
    liveness: LivenessStatus,
    identity: IdentityStatus,
    schedule: ScheduleStatus,
    expected: AccessDecision,
) -> None:
    result = decide_access(liveness, identity, schedule)

    assert result.decision is expected
    assert result.reason
    if expected is AccessDecision.STEP_UP_VERIFICATION:
        assert result.decision is not AccessDecision.ALLOW


def test_unknown_user_is_denied_without_running_face_inference(
    access_environment, monkeypatch, tmp_path: Path
) -> None:
    store, _ = access_environment
    empty_store = SQLiteFaceStore(tmp_path / "empty.sqlite3")
    monkeypatch.setattr(access_routes, "face_store", empty_store)
    monkeypatch.setattr(access_routes, "verify_claimed_user", verify_claimed_user)

    def inference_must_not_run(_: bytes) -> np.ndarray:
        raise AssertionError("Unknown users must not run face inference.")

    monkeypatch.setattr(
        access_routes.face_embedding_service,
        "extract_embedding",
        inference_must_not_run,
    )
    challenge_id = issue_challenge(store)
    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "DENY"
    assert response.json()["identity_status"] == "NOT_CHECKED"


@pytest.mark.parametrize(
    ("challenge_id", "expected_status"),
    [
        ("missing-challenge", 404),
        ("", 422),
    ],
)
def test_missing_or_unknown_challenge_never_allows(
    access_environment,
    challenge_id: str,
    expected_status: int,
) -> None:
    _, state = access_environment
    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id),
    )

    assert response.status_code == expected_status
    assert response.status_code != 200
    assert state["identity_frames"] == []


def test_automatic_identity_search_uses_backend_match_without_user_id(
    access_environment,
) -> None:
    store, state = access_environment
    challenge_id = issue_challenge(store)
    payload = decision_payload(challenge_id)
    payload.pop("user_id")

    response = client.post("/api/access/decision", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "STEP_UP_VERIFICATION"
    assert body["identity_status"] == "VERIFIED"
    assert body["matched_user_id"] == "demo-user"
    assert state["identity_frames"] == []
    assert state["automatic_identity_frames"] == [
        base64.b64decode(JPEG_FRAMES[5])
    ]


@pytest.mark.parametrize(
    ("identity_status", "expected_decision"),
    [
        (IdentityStatus.NOT_VERIFIED, AccessDecision.DENY),
        (IdentityStatus.AMBIGUOUS, AccessDecision.STEP_UP_VERIFICATION),
    ],
)
def test_automatic_identity_no_match_or_ambiguity_fails_closed(
    access_environment,
    identity_status: IdentityStatus,
    expected_decision: AccessDecision,
) -> None:
    store, state = access_environment
    state["identity"] = FaceIdentityEvaluation(
        identity_status,
        None,
        0.363,
        "Mocked automatic identity result.",
    )
    challenge_id = issue_challenge(store)
    payload = decision_payload(challenge_id)
    payload.pop("user_id")

    response = client.post("/api/access/decision", json=payload)

    assert response.status_code == 200
    assert response.json()["decision"] == expected_decision.value
    assert response.json()["identity_status"] == identity_status.value
    assert response.json()["matched_user_id"] is None


def test_reference_photo_alone_cannot_request_an_access_decision(
    access_environment,
) -> None:
    _, state = access_environment

    response = client.post(
        "/api/access/decision",
        json={"image_base64": base64.b64encode(b"\xff\xd8\xffphoto").decode("ascii")},
    )

    assert response.status_code == 422
    assert "decision" not in response.json()
    assert state["identity_frames"] == []
    assert state["automatic_identity_frames"] == []


def test_missing_challenge_field_is_rejected(access_environment) -> None:
    _, state = access_environment
    payload = decision_payload("not-used")
    payload.pop("challenge_id")

    response = client.post("/api/access/decision", json=payload)

    assert response.status_code == 422
    assert state["identity_frames"] == []


def test_poor_quality_live_frame_fails_closed(access_environment, monkeypatch) -> None:
    store, _ = access_environment

    def reject_poor_quality(*_args, **_kwargs):
        raise PoorImageQualityError("The photo is too blurred.")

    monkeypatch.setattr(access_routes, "identify_enrolled_face", reject_poor_quality)
    challenge_id = issue_challenge(store)
    payload = decision_payload(challenge_id)
    payload.pop("user_id")

    response = client.post("/api/access/decision", json=payload)

    assert response.status_code == 200
    assert response.json()["decision"] == "DENY"
    assert response.json()["identity_status"] == "NOT_CHECKED"
    assert "blurred" in response.json()["identity_message"]


def test_expired_challenge_is_rejected(monkeypatch, access_environment) -> None:
    now = [100.0]
    store = InMemoryChallengeStore(clock=lambda: now[0])
    monkeypatch.setattr(access_routes, "challenge_store", store)
    challenge_id = issue_challenge(store)
    now[0] += 61

    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id),
    )

    assert response.status_code == 410


def test_reused_challenge_is_rejected(access_environment) -> None:
    store, _ = access_environment
    challenge_id = issue_challenge(store)
    payload = decision_payload(challenge_id)

    first = client.post("/api/access/decision", json=payload)
    second = client.post("/api/access/decision", json=payload)

    assert first.status_code == 200
    assert first.json()["decision"] == "STEP_UP_VERIFICATION"
    assert second.status_code == 409


@pytest.mark.parametrize(
    "forged_fields",
    [
        {"decision": "ALLOW"},
        {"liveness_status": "LIVE"},
        {"identity_status": "VERIFIED"},
        {"schedule_status": "WITHIN_SCHEDULE"},
        {"confidence": 1.0},
    ],
)
def test_client_cannot_supply_trusted_results(
    access_environment,
    forged_fields: dict[str, object],
) -> None:
    store, state = access_environment
    challenge_id = issue_challenge(store)

    response = client.post(
        "/api/access/decision",
        json=decision_payload(challenge_id, **forged_fields),
    )

    assert response.status_code == 422
    assert state["identity_frames"] == []


def schedule_json(
    *,
    timezone_name: str = "Asia/Kolkata",
    sunday: dict[str, object] | None = None,
    exceptions: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    weekly = {
        day: {"open": "08:00", "close": "20:00"}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    weekly["sunday"] = sunday or {"closed": True}
    return {
        "timezone": timezone_name,
        "weekly": weekly,
        "exceptions": exceptions or {},
    }


def write_schedule(tmp_path: Path, config: dict[str, object]) -> CampusScheduleEvaluator:
    path = tmp_path / "campus_schedule.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return CampusScheduleEvaluator(path)


def test_invalid_or_missing_schedule_is_unavailable(tmp_path: Path) -> None:
    missing = CampusScheduleEvaluator(tmp_path / "missing.json")
    invalid = write_schedule(tmp_path, schedule_json(timezone_name="Invalid/Zone"))

    assert missing.evaluate().status is ScheduleStatus.UNAVAILABLE
    assert (
        invalid.evaluate(datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)).status
        is ScheduleStatus.UNAVAILABLE
    )


def test_closed_day_does_not_permit_access(tmp_path: Path) -> None:
    evaluator = write_schedule(tmp_path, schedule_json())

    result = evaluator.evaluate(datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc))

    assert result.status is ScheduleStatus.OUTSIDE_SCHEDULE


def test_timezone_boundary_and_exception_override(tmp_path: Path) -> None:
    evaluator = write_schedule(
        tmp_path,
        schedule_json(
            exceptions={
                "2026-11-16": {"closed": True},
                "2026-11-17": {"open": "10:00", "close": "12:00"},
            }
        ),
    )

    before_open = evaluator.evaluate(datetime(2026, 11, 23, 2, 29, tzinfo=timezone.utc))
    at_open = evaluator.evaluate(datetime(2026, 11, 23, 2, 30, tzinfo=timezone.utc))
    holiday = evaluator.evaluate(datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc))
    special_window = evaluator.evaluate(
        datetime(2026, 11, 17, 4, 30, tzinfo=timezone.utc)
    )

    assert before_open.status is ScheduleStatus.OUTSIDE_SCHEDULE
    assert at_open.status is ScheduleStatus.WITHIN_SCHEDULE
    assert holiday.status is ScheduleStatus.OUTSIDE_SCHEDULE
    assert special_window.status is ScheduleStatus.WITHIN_SCHEDULE
