import base64
import binascii
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.face_detection import (
    MAX_BASE64_LENGTH,
    MAX_IMAGE_SIZE_BYTES,
    ImageTooLargeError,
)
from app.services.face_detection import JPEG_SIGNATURE

MIN_FRAME_COUNT = 5
MAX_FRAME_COUNT = 20
MAX_TOTAL_BASE64_BYTES = 20 * 1024 * 1024


class LivenessChallenge(str, Enum):
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"


class LivenessStatus(str, Enum):
    LIVE = "LIVE"
    UNCERTAIN = "UNCERTAIN"
    SPOOF_SUSPECTED = "SPOOF_SUSPECTED"


class LivenessPayloadTooLargeError(ValueError):
    """Raised when the sequence exceeds its aggregate Base64 size limit."""


class LivenessChallengeResponse(BaseModel):
    challenge_id: str
    challenge: LivenessChallenge
    expires_in_seconds: int


class LivenessCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=1, max_length=128)
    frames: list[str] = Field(min_length=MIN_FRAME_COUNT, max_length=MAX_FRAME_COUNT)

    @field_validator("frames", mode="before")
    @classmethod
    def validate_frames(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        if not MIN_FRAME_COUNT <= len(value) <= MAX_FRAME_COUNT:
            raise ValueError("frames must contain between 5 and 20 JPEG images.")

        if all(isinstance(frame, str) for frame in value):
            encoded_size = sum(len(frame) for frame in value)
            if encoded_size > MAX_TOTAL_BASE64_BYTES:
                raise LivenessPayloadTooLargeError(
                    "Combined Base64 frame data must not exceed 20 MiB."
                )

        for index, frame in enumerate(value):
            if not isinstance(frame, str) or not frame:
                raise ValueError(f"Frame {index} must be a non-empty Base64 string.")
            if len(frame) > MAX_BASE64_LENGTH:
                raise ImageTooLargeError(
                    f"Frame {index} exceeds the 5 MB decoded image limit."
                )
            try:
                image_bytes = base64.b64decode(frame, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError(f"Frame {index} is not valid Base64.") from None
            if not image_bytes:
                raise ValueError(f"Frame {index} decodes to an empty image.")
            if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
                raise ImageTooLargeError(
                    f"Frame {index} exceeds the 5 MB decoded image limit."
                )
            if not image_bytes.startswith(JPEG_SIGNATURE):
                raise ValueError(f"Frame {index} is not a JPEG image.")
        return value

    def decoded_frames(self) -> list[bytes]:
        return [base64.b64decode(frame, validate=True) for frame in self.frames]


class LivenessCheckResponse(BaseModel):
    success: bool
    challenge_id: str
    liveness_status: LivenessStatus
    confidence: float = Field(ge=0, le=1)
    movement_detected: bool
    valid_frame_count: int = Field(ge=0)
    total_frame_count: int = Field(ge=MIN_FRAME_COUNT, le=MAX_FRAME_COUNT)
    processing_time_ms: float = Field(ge=0)
    message: str
