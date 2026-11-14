from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
JPEG_SIGNATURE = b"\xff\xd8\xff"


class FaceDetectionError(Exception):
    """Base exception for face-detection processing errors."""


class UnsupportedImageFormatError(FaceDetectionError):
    """Raised when the submitted image is not a JPEG."""


class InvalidImageError(FaceDetectionError):
    """Raised when the submitted JPEG cannot be decoded."""


class FaceDetectorUnavailableError(FaceDetectionError):
    """Raised when the Haar Cascade detector cannot be loaded or run."""


@dataclass(frozen=True)
class DetectionResult:
    image_width: int
    image_height: int
    faces: list[tuple[int, int, int, int]]


def decode_jpeg(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise InvalidImageError("Image data is empty.")
    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise InvalidImageError("Decoded image must not exceed 5 MB.")
    if not image_bytes.startswith(JPEG_SIGNATURE):
        raise UnsupportedImageFormatError("Only JPEG images are supported.")

    encoded_image = np.frombuffer(image_bytes, dtype=np.uint8)
    try:
        image = cv2.imdecode(encoded_image, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise InvalidImageError("JPEG image could not be decoded.") from exc
    if image is None or image.size == 0:
        raise InvalidImageError("JPEG image is invalid or corrupted.")
    return image


class HaarFaceDetector:
    def __init__(
        self,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
        min_size: tuple[int, int] = (24, 24),
    ) -> None:
        if scale_factor <= 1:
            raise ValueError("scale_factor must be greater than 1.")
        if min_neighbors < 0:
            raise ValueError("min_neighbors must not be negative.")
        if min_size[0] <= 0 or min_size[1] <= 0:
            raise ValueError("min_size dimensions must be positive.")
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors
        self.min_size = min_size
        self._cascade: cv2.CascadeClassifier | None = None

    def _load_cascade(self) -> cv2.CascadeClassifier:
        if self._cascade is not None:
            return self._cascade

        cascade_path = (
            Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        )
        if not cascade_path.is_file():
            raise FaceDetectorUnavailableError("Haar Cascade file is unavailable.")

        try:
            cascade = cv2.CascadeClassifier(str(cascade_path))
        except Exception as exc:
            raise FaceDetectorUnavailableError(
                "Haar Cascade detector could not be initialized."
            ) from exc
        if cascade.empty():
            raise FaceDetectorUnavailableError(
                "Haar Cascade detector failed to load its classifier."
            )

        self._cascade = cascade
        return cascade

    def detect(self, image_bytes: bytes) -> DetectionResult:
        image = decode_jpeg(image_bytes)
        image_height, image_width = image.shape[:2]
        try:
            grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cascade = self._load_cascade()
            detected = cascade.detectMultiScale(
                grayscale,
                scaleFactor=self.scale_factor,
                minNeighbors=self.min_neighbors,
                minSize=self.min_size,
            )
        except FaceDetectionError:
            raise
        except Exception as exc:
            raise FaceDetectorUnavailableError(
                "Face detection could not be completed."
            ) from exc

        faces = [
            (int(x), int(y), int(width), int(height))
            for x, y, width, height in detected
        ]
        return DetectionResult(
            image_width=image_width,
            image_height=image_height,
            faces=faces,
        )


face_detector = HaarFaceDetector()
