from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.schemas.access import (
    AccessDecision,
    AccessDecisionRequest,
    AccessDecisionResponse,
    OfflineSyncRequest,
    OfflineSyncResponse,
)
from app.schemas.auth import IdentityStatus
from app.services.access_decision import decide_access
from app.services.campus_schedule import campus_schedule
from app.services.face_authentication import (
    FaceIdentityEvaluation,
    identify_enrolled_face,
    verify_claimed_user,
)
from app.services.face_detection import (
    FaceDetectionError,
    InvalidImageError,
    UnsupportedImageFormatError,
)
from app.services.face_recognition import (
    FaceRecognitionError,
    FaceRecognitionUnavailableError,
    MultipleFacesDetectedError,
    NoFaceDetectedError,
    PoorImageQualityError,
    face_embedding_service,
)
from app.services.face_store import FaceStoreUnavailableError, face_store
from app.services.liveness import (
    ChallengeAlreadyUsedError,
    ChallengeExpiredError,
    ChallengeNotFoundError,
    challenge_store,
    movement_analyzer,
)
from app.services.offline_events import offline_event_store

router = APIRouter()


@router.post("/offline-sync", response_model=OfflineSyncResponse)
def sync_offline_decisions(request: OfflineSyncRequest) -> OfflineSyncResponse:
    try:
        accepted_event_ids = offline_event_store.accept(request.events)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OfflineSyncResponse(accepted_event_ids=accepted_event_ids)


@router.post(
    "/decision",
    response_model=AccessDecisionResponse,
    tags=["Access Decision"],
)
def make_access_decision(
    request: AccessDecisionRequest,
) -> AccessDecisionResponse:
    started_at = perf_counter()
    try:
        challenge = challenge_store.consume(request.challenge_id)
    except ChallengeExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ChallengeAlreadyUsedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChallengeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    frames = request.decoded_frames()
    try:
        liveness = movement_analyzer.evaluate(challenge, frames)
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceDetectionError as exc:
        raise HTTPException(
            status_code=500,
            detail="Face detector is unavailable.",
        ) from exc

    identity = _check_identity(
        request.user_id,
        frames,
        liveness.last_valid_frame_index,
    )
    schedule = campus_schedule.evaluate()
    policy = decide_access(
        liveness.status,
        identity.identity_status,
        schedule.status,
    )
    return AccessDecisionResponse(
        success=True,
        decision=policy.decision,
        challenge_id=request.challenge_id,
        liveness_status=liveness.status,
        identity_status=identity.identity_status,
        schedule_status=schedule.status,
        reason=policy.reason,
        identity_message=identity.message,
        matched_user_id=identity.matched_user_id,
        requires_step_up=policy.decision is AccessDecision.STEP_UP_VERIFICATION,
        movement_detected=liveness.movement_detected,
        valid_frame_count=liveness.valid_frame_count,
        total_frame_count=liveness.total_frame_count,
        processing_time_ms=(perf_counter() - started_at) * 1000,
    )


def _check_identity(
    user_id: str | None,
    frames: list[bytes],
    last_valid_frame_index: int | None,
) -> FaceIdentityEvaluation:
    if last_valid_frame_index is None:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "No valid face frame was available for identity verification.",
        )

    try:
        image_bytes = frames[last_valid_frame_index]
        if user_id is None:
            return identify_enrolled_face(
                image_bytes,
                face_store,
                face_embedding_service,
            )
        return verify_claimed_user(
            user_id,
            image_bytes,
            face_store,
            face_embedding_service,
        )
    except (
        FaceStoreUnavailableError,
    ):
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "Local enrollment database is unavailable.",
        )
    except NoFaceDetectedError:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "The identity model did not detect a face in the selected frame.",
        )
    except MultipleFacesDetectedError:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "The identity model detected multiple faces in the selected frame.",
        )
    except PoorImageQualityError as exc:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            str(exc),
        )
    except FaceRecognitionUnavailableError:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "The identity recognition model is unavailable.",
        )
    except (InvalidImageError, UnsupportedImageFormatError, FaceRecognitionError):
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            face_embedding_service.threshold,
            "Identity verification could not process the selected frame.",
        )
