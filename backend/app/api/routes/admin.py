from fastapi import APIRouter, Depends, HTTPException

from app.api.admin_auth import require_admin
from app.schemas.admin import (
    EnrollmentDeleteRequest,
    EnrollmentDeleteResponse,
    EnrollmentListResponse,
    EnrollmentSummary,
)
from app.services.face_store import FaceStoreUnavailableError, face_store

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/enrollments", response_model=EnrollmentListResponse)
def list_enrollments() -> EnrollmentListResponse:
    try:
        counts = face_store.list_template_counts()
    except FaceStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return EnrollmentListResponse(
        enrollments=[
            EnrollmentSummary(user_id=user_id, template_count=count)
            for user_id, count in counts
        ]
    )


@router.delete("/enrollments", response_model=EnrollmentDeleteResponse)
def delete_enrollment(
    request: EnrollmentDeleteRequest,
) -> EnrollmentDeleteResponse:
    user_id = request.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id must not be empty.")
    try:
        deleted = face_store.delete_user(user_id)
    except FaceStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="The enrolled user was not found.")

    return EnrollmentDeleteResponse(deleted=True, user_id=user_id)
