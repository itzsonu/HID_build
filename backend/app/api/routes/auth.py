import hashlib
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
import numpy as np

from app.api.admin_auth import require_admin
from app.schemas.auth import (
    BatchFaceRegistrationRequest,
    BatchFaceRegistrationResponse,
    EnrollmentPhotoResult,
    EnrollmentPhotoStatus,
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
    PoorImageQualityError,
    cosine_similarity,
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
    "/register/batch",
    response_model=BatchFaceRegistrationResponse,
    tags=["Face Authentication"],
    dependencies=[Depends(require_admin)],
)
def register_faces_batch(
    request: BatchFaceRegistrationRequest,
) -> BatchFaceRegistrationResponse:
    if not request.consent:
        raise HTTPException(
            status_code=403,
            detail="Explicit consent is required before face enrollment.",
        )

    started_at = perf_counter()
    photo_results: list[EnrollmentPhotoResult] = []
    accepted_embeddings: list[np.ndarray] = []
    accepted_hashes: set[str] = set()
    accepted_digests: list[str] = []

    for photo_index, image_bytes in enumerate(request.decoded_images(), start=1):
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        if image_hash in accepted_hashes:
            photo_results.append(
                EnrollmentPhotoResult(
                    photo_index=photo_index,
                    status=EnrollmentPhotoStatus.DUPLICATE,
                    message="This photo duplicates a previously selected reference.",
                )
            )
            continue

        try:
            embedding = normalize_embedding(
                face_embedding_service.extract_embedding(
                    image_bytes,
                    require_quality=True,
                )
            )
        except (
            InvalidImageError,
            UnsupportedImageFormatError,
            NoFaceDetectedError,
            MultipleFacesDetectedError,
            PoorImageQualityError,
        ) as exc:
            photo_results.append(
                EnrollmentPhotoResult(
                    photo_index=photo_index,
                    status=EnrollmentPhotoStatus.REJECTED,
                    message=str(exc),
                )
            )
            continue
        except FaceRecognitionUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FaceRecognitionError as exc:
            raise HTTPException(
                status_code=503,
                detail="Face recognition could not be completed.",
            ) from exc

        if any(
            cosine_similarity(embedding, previous) >= 0.995
            for previous in accepted_embeddings
        ):
            photo_results.append(
                EnrollmentPhotoResult(
                    photo_index=photo_index,
                    status=EnrollmentPhotoStatus.DUPLICATE,
                    message="This photo is too similar to another selected reference.",
                )
            )
            continue

        accepted_hashes.add(image_hash)
        accepted_embeddings.append(embedding)
        accepted_digests.append(image_hash)
        photo_results.append(
            EnrollmentPhotoResult(
                photo_index=photo_index,
                status=EnrollmentPhotoStatus.ACCEPTED,
                message="Reference photo passed validation and was processed.",
            )
        )

    accepted_count = len(accepted_embeddings)
    rejected_count = sum(
        result.status is EnrollmentPhotoStatus.REJECTED
        for result in photo_results
    )
    duplicate_count = sum(
        result.status is EnrollmentPhotoStatus.DUPLICATE
        for result in photo_results
    )
    processing_time_ms = (perf_counter() - started_at) * 1000

    if accepted_count < 2:
        return BatchFaceRegistrationResponse(
            success=True,
            registered=False,
            updated=False,
            user_id=request.user_id,
            photo_results=photo_results,
            accepted_photo_count=accepted_count,
            rejected_photo_count=rejected_count,
            duplicate_photo_count=duplicate_count,
            processing_time_ms=processing_time_ms,
            message="At least two distinct usable photos are required; the stored template was not changed.",
        )

    try:
        updated = face_store.upsert_templates(
            request.user_id,
            accepted_embeddings,
            accepted_digests,
        )
    except FaceStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BatchFaceRegistrationResponse(
        success=True,
        registered=True,
        updated=updated,
        user_id=request.user_id,
        photo_results=photo_results,
        accepted_photo_count=accepted_count,
        rejected_photo_count=rejected_count,
        duplicate_photo_count=duplicate_count,
        processing_time_ms=(perf_counter() - started_at) * 1000,
        message=(
            "The face template was updated from the accepted reference photos."
            if updated
            else "The face template was created from the accepted reference photos. No access was granted."
        ),
    )


@router.post(
    "/register",
    response_model=FaceRegistrationResponse,
    status_code=201,
    tags=["Face Authentication"],
    dependencies=[Depends(require_admin)],
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
