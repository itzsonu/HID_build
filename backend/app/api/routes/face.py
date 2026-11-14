from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.schemas.face_detection import (
    FaceBoundingBox,
    FaceDetectionRequest,
    FaceDetectionResponse,
)
from app.services.face_detection import (
    FaceDetectionError,
    InvalidImageError,
    UnsupportedImageFormatError,
    face_detector,
)

router = APIRouter()


@router.post(
    "/detect",
    response_model=FaceDetectionResponse,
    tags=["Face Detection"],
)
async def detect_face(request: FaceDetectionRequest) -> FaceDetectionResponse:
    request_id = request.request_id or uuid4().hex
    started_at = perf_counter()

    try:
        result = face_detector.detect(request.image_bytes)
    except UnsupportedImageFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceDetectionError as exc:
        raise HTTPException(
            status_code=500,
            detail="Face detector is unavailable.",
        ) from exc

    faces = [
        FaceBoundingBox(
            x=x / result.image_width,
            y=y / result.image_height,
            width=width / result.image_width,
            height=height / result.image_height,
        )
        for x, y, width, height in result.faces
    ]
    return FaceDetectionResponse(
        success=True,
        request_id=request_id,
        face_detected=bool(faces),
        face_count=len(faces),
        faces=faces,
        processing_time_ms=(perf_counter() - started_at) * 1000,
        message="Face detected." if faces else "No face detected.",
    )
