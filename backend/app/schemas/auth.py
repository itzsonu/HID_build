from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.schemas.face_detection import FaceImageRequest


class FaceRegistrationRequest(FaceImageRequest):
    user_id: str = Field(min_length=1, max_length=128)
    consent: bool

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty.")
        return normalized


class FaceVerificationRequest(FaceImageRequest):
    user_id: str = Field(min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty.")
        return normalized


class IdentityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_CHECKED = "NOT_CHECKED"


class FaceRegistrationResponse(BaseModel):
    success: bool
    registered: bool
    user_id: str
    processing_time_ms: float = Field(ge=0)
    message: str


class FaceVerificationResponse(BaseModel):
    success: bool
    user_id: str
    identity_status: IdentityStatus
    similarity: float = Field(ge=-1, le=1)
    threshold: float = Field(gt=0, le=1)
    processing_time_ms: float = Field(ge=0)
    message: str
