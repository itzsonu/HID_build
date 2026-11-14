import base64
import binascii
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_SIZE_BYTES + 2) // 3)


class ImageTooLargeError(ValueError):
    """Raised when a decoded image exceeds the request size limit."""


class FaceImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_base64: str = Field(min_length=1, max_length=MAX_BASE64_LENGTH)

    _image_bytes: bytes = PrivateAttr(default=b"")

    @field_validator("image_base64", mode="before")
    @classmethod
    def validate_image_payload(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if not value:
            raise ValueError("image_base64 must not be empty.")
        if len(value) > MAX_BASE64_LENGTH:
            raise ImageTooLargeError("Decoded image must not exceed 5 MB.")
        try:
            image_bytes = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image_base64 must contain valid Base64 data.") from None
        if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise ImageTooLargeError("Decoded image must not exceed 5 MB.")
        if not image_bytes:
            raise ValueError("image_base64 must not decode to an empty image.")
        return value

    def cache_decoded_image(self) -> None:
        self._image_bytes = base64.b64decode(self.image_base64, validate=True)

    @property
    def image_bytes(self) -> bytes:
        if not self._image_bytes:
            self.cache_decoded_image()
        return self._image_bytes


class FaceDetectionRequest(FaceImageRequest):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class FaceBoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class FaceDetectionResponse(BaseModel):
    success: bool
    request_id: str
    face_detected: bool
    face_count: int = Field(ge=0)
    faces: list[FaceBoundingBox]
    processing_time_ms: float = Field(ge=0)
    message: str
