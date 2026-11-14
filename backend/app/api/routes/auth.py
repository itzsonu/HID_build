from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.schemas.auth import (
    FaceRegistrationRequest,
    FaceRegistrationResponse,
    FaceVerificationRequest,
    FaceVerificationResponse,
    IdentityStatus,
)
from app.services.face_detection import InvalidImageError, UnsupportedImageFormatError
from app.services.face_recognition import (
    FaceRecognitionError,
    FaceRecognitionUnavailableError,
    MultipleFacesDetectedError,
    NoFaceDetectedError,
    face_embedding_service,
    normalize_embedding,
)
from app.services.face_authentication import verify_enrolled_face
from app.services.face_store import (
    DuplicateUserError,
    FaceStoreUnavailableError,
    face_store,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=FaceRegistrationResponse,
    status_code=201,
    tags=["Face Authentication"],
)
def register_face(request: FaceRegistrationRequest) -> FaceRegistrationResponse:
    if not request.consent:
        raise HTTPException(
            status_code=403,
            detail="Explicit consent is required before face enrollment.",
        )

    started_at = perf_counter()
    try:
        if face_store.contains(request.user_id):
            raise HTTPException(
                status_code=409,
                detail="This user ID is already enrolled.",
            )
        embedding = normalize_embedding(
            face_embedding_service.extract_embedding(request.image_bytes)
        )
        face_store.insert(request.user_id, embedding)
    except UnsupportedImageFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except (InvalidImageError, NoFaceDetectedError, MultipleFacesDetectedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FaceRecognitionUnavailableError, FaceStoreUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FaceRecognitionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Face recognition could not be completed.",
        ) from exc

    return FaceRegistrationResponse(
        success=True,
        registered=True,
        user_id=request.user_id,
        processing_time_ms=(perf_counter() - started_at) * 1000,
        message="Face enrollment completed. Identity was not verified.",
    )


@router.post(
    "/verify",
    response_model=FaceVerificationResponse,
    tags=["Face Authentication"],
)
def verify_face(request: FaceVerificationRequest) -> FaceVerificationResponse:
    started_at = perf_counter()
    try:
        evaluation = verify_enrolled_face(
            request.user_id,
            request.image_bytes,
            face_store,
            face_embedding_service,
        )
        if (
            evaluation.identity_status is IdentityStatus.NOT_CHECKED
            or evaluation.similarity is None
        ):
            raise HTTPException(status_code=404, detail="User is not enrolled.")
        similarity = evaluation.similarity
    except UnsupportedImageFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except (InvalidImageError, NoFaceDetectedError, MultipleFacesDetectedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (FaceRecognitionUnavailableError, FaceRecognitionError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Face recognition could not be completed.",
        ) from exc

    return FaceVerificationResponse(
        success=True,
        user_id=request.user_id,
        identity_status=evaluation.identity_status,
        similarity=similarity,
        threshold=evaluation.threshold,
        processing_time_ms=(perf_counter() - started_at) * 1000,
        message=evaluation.message,
    )
