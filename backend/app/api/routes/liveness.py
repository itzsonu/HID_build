from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.schemas.liveness import (
    LivenessChallengeResponse,
    LivenessCheckRequest,
    LivenessCheckResponse,
)
from app.services.liveness import (
    ChallengeAlreadyUsedError,
    ChallengeExpiredError,
    ChallengeNotFoundError,
    challenge_store,
    movement_analyzer,
)
from app.services.face_detection import FaceDetectionError, InvalidImageError

router = APIRouter()


@router.post(
    "/challenge",
    response_model=LivenessChallengeResponse,
    tags=["Liveness"],
)
def create_liveness_challenge() -> LivenessChallengeResponse:
    challenge_id, challenge = challenge_store.create()
    return LivenessChallengeResponse(
        challenge_id=challenge_id,
        challenge=challenge,
        expires_in_seconds=challenge_store.ttl_seconds,
    )


@router.post(
    "/check",
    response_model=LivenessCheckResponse,
    tags=["Liveness"],
)
def check_liveness(request: LivenessCheckRequest) -> LivenessCheckResponse:
    try:
        challenge = challenge_store.consume(request.challenge_id)
    except ChallengeExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ChallengeAlreadyUsedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChallengeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    started_at = perf_counter()
    try:
        evaluation = movement_analyzer.evaluate(challenge, request.decoded_frames())
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceDetectionError as exc:
        raise HTTPException(
            status_code=500,
            detail="Face detector is unavailable.",
        ) from exc

    return LivenessCheckResponse(
        success=True,
        challenge_id=request.challenge_id,
        liveness_status=evaluation.status,
        confidence=evaluation.confidence,
        movement_detected=evaluation.movement_detected,
        valid_frame_count=evaluation.valid_frame_count,
        total_frame_count=evaluation.total_frame_count,
        processing_time_ms=(perf_counter() - started_at) * 1000,
        message=evaluation.message,
    )
