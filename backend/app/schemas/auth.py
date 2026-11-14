import base64
import binascii
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.face_detection import (
    MAX_BASE64_LENGTH,
    MAX_IMAGE_SIZE_BYTES,
    FaceImageRequest,
    ImageTooLargeError,
)
from app.services.face_detection import JPEG_SIGNATURE

MIN_ENROLLMENT_PHOTOS = 2
MAX_ENROLLMENT_PHOTOS = 10
MAX_ENROLLMENT_BASE64_BYTES = 20 * 1024 * 1024


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


class BatchFaceRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    consent: bool
    images_base64: list[str] = Field(
        min_length=MIN_ENROLLMENT_PHOTOS,
        max_length=MAX_ENROLLMENT_PHOTOS,
    )

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty.")
        return normalized

    @field_validator("images_base64", mode="before")
    @classmethod
    def validate_images(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        if not MIN_ENROLLMENT_PHOTOS <= len(value) <= MAX_ENROLLMENT_PHOTOS:
            raise ValueError(
                f"Select between {MIN_ENROLLMENT_PHOTOS} and "
                f"{MAX_ENROLLMENT_PHOTOS} reference photos."
            )

        if all(isinstance(image, str) for image in value):
            if sum(len(image) for image in value) > MAX_ENROLLMENT_BASE64_BYTES:
                raise ImageTooLargeError(
                    "Combined enrollment photos must not exceed 20 MiB."
                )

        for index, image in enumerate(value, start=1):
            if not isinstance(image, str) or not image:
                raise ValueError(f"Photo {index} must be a non-empty Base64 string.")
            if len(image) > MAX_BASE64_LENGTH:
                raise ImageTooLargeError(
                    f"Photo {index} exceeds the 5 MiB decoded image limit."
                )
            try:
                decoded = base64.b64decode(image, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError(f"Photo {index} is not valid Base64.") from None
            if not decoded:
                raise ValueError(f"Photo {index} is empty.")
            if len(decoded) > MAX_IMAGE_SIZE_BYTES:
                raise ImageTooLargeError(
                    f"Photo {index} exceeds the 5 MiB decoded image limit."
                )
            if not decoded.startswith(JPEG_SIGNATURE):
                raise ValueError(f"Photo {index} must be a JPEG image.")
        return value

    def decoded_images(self) -> list[bytes]:
        return [
            base64.b64decode(image, validate=True)
            for image in self.images_base64
        ]


class IdentityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_CHECKED = "NOT_CHECKED"
    AMBIGUOUS = "AMBIGUOUS"


class EnrollmentPhotoStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


class EnrollmentPhotoResult(BaseModel):
    photo_index: int = Field(ge=1)
    status: EnrollmentPhotoStatus
    message: str


class BatchFaceRegistrationResponse(BaseModel):
    success: bool
    registered: bool
    updated: bool
    user_id: str
    photo_results: list[EnrollmentPhotoResult]
    accepted_photo_count: int = Field(ge=0)
    rejected_photo_count: int = Field(ge=0)
    duplicate_photo_count: int = Field(ge=0)
    processing_time_ms: float = Field(ge=0)
    message: str


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
