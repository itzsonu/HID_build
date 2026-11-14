import math
import threading
from pathlib import Path

import cv2
import numpy as np

from app.core.config import BACKEND_DIR, settings
from app.services.face_detection import decode_jpeg
from app.services.face_store import EMBEDDING_SIZE

MODEL_DIR = BACKEND_DIR / "models"
YUNET_MODEL_PATH = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_MODEL_PATH = MODEL_DIR / "face_recognition_sface_2021dec.onnx"


class FaceRecognitionError(Exception):
    """Base exception for face-embedding pipeline errors."""


class NoFaceDetectedError(FaceRecognitionError):
    """Raised when the submitted image contains no detectable face."""


class MultipleFacesDetectedError(FaceRecognitionError):
    """Raised when the submitted image contains more than one face."""


class FaceRecognitionUnavailableError(FaceRecognitionError):
    """Raised when a recognition model is unavailable or fails."""


class InvalidFaceEmbeddingError(FaceRecognitionError):
    """Raised when the model returns an unusable face representation."""


class PoorImageQualityError(FaceRecognitionError):
    """Raised when an image is too small, blurred, or poorly exposed."""


def normalize_embedding(value: np.ndarray) -> np.ndarray:
    embedding = np.asarray(value, dtype=np.float32).reshape(-1)
    if embedding.size != EMBEDDING_SIZE or not np.isfinite(embedding).all():
        raise InvalidFaceEmbeddingError("The face representation is invalid.")

    norm = float(np.linalg.norm(embedding))
    if not math.isfinite(norm) or norm <= 0:
        raise InvalidFaceEmbeddingError("The face representation is invalid.")
    return (embedding / norm).astype(np.float32)


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_normalized = normalize_embedding(left)
    right_normalized = normalize_embedding(right)
    similarity = float(np.dot(left_normalized, right_normalized))
    return max(-1.0, min(1.0, similarity))


class SFaceEmbeddingService:
    def __init__(
        self,
        yunet_model_path: Path = YUNET_MODEL_PATH,
        sface_model_path: Path = SFACE_MODEL_PATH,
    ) -> None:
        threshold = settings.FACE_MATCH_THRESHOLD
        if not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("FACE_MATCH_THRESHOLD must be in the range (0, 1].")
        self.threshold = threshold
        self.yunet_model_path = Path(yunet_model_path)
        self.sface_model_path = Path(sface_model_path)
        self._detector: cv2.FaceDetectorYN | None = None
        self._recognizer: cv2.FaceRecognizerSF | None = None
        self._lock = threading.Lock()

    def _load_models(self) -> tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
        if self._detector is not None and self._recognizer is not None:
            return self._detector, self._recognizer
        if not self.yunet_model_path.is_file() or not self.sface_model_path.is_file():
            raise FaceRecognitionUnavailableError(
                "A local face recognition model is unavailable."
            )

        try:
            detector = cv2.FaceDetectorYN_create(
                str(self.yunet_model_path),
                "",
                (320, 320),
                0.9,
                0.3,
                5000,
                cv2.dnn.DNN_BACKEND_OPENCV,
                cv2.dnn.DNN_TARGET_CPU,
            )
            recognizer = cv2.FaceRecognizerSF_create(
                str(self.sface_model_path),
                "",
                cv2.dnn.DNN_BACKEND_OPENCV,
                cv2.dnn.DNN_TARGET_CPU,
            )
        except (cv2.error, OSError) as exc:
            raise FaceRecognitionUnavailableError(
                "A local face recognition model could not be loaded."
            ) from exc

        if detector is None or recognizer is None:
            raise FaceRecognitionUnavailableError(
                "A local face recognition model could not be loaded."
            )
        self._detector = detector
        self._recognizer = recognizer
        return detector, recognizer

    def warm_up(self) -> None:
        """Load the local models and run a harmless inference before access requests."""
        with self._lock:
            detector, recognizer = self._load_models()
            blank_frame = np.zeros((320, 320, 3), dtype=np.uint8)
            try:
                detector.setInputSize((320, 320))
                detector.detect(blank_frame)
                recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8))
            except cv2.error as exc:
                raise FaceRecognitionUnavailableError(
                    "The local face models could not complete warm-up."
                ) from exc

    def extract_embedding(
        self,
        image_bytes: bytes,
        *,
        require_quality: bool = False,
    ) -> np.ndarray:
        image = decode_jpeg(image_bytes)
        image_height, image_width = image.shape[:2]
        if require_quality:
            self._validate_image_quality(image)
        with self._lock:
            detector, recognizer = self._load_models()
            try:
                detector.setInputSize((image_width, image_height))
                _, detected_faces = detector.detect(image)
                face_count = 0 if detected_faces is None else len(detected_faces)
                if face_count == 0:
                    raise NoFaceDetectedError("No face was detected in the image.")
                if face_count != 1:
                    raise MultipleFacesDetectedError(
                        "Exactly one face must be present in the image."
                    )
                aligned_face = recognizer.alignCrop(image, detected_faces[0])
                embedding = recognizer.feature(aligned_face)
            except (NoFaceDetectedError, MultipleFacesDetectedError):
                raise
            except cv2.error as exc:
                raise FaceRecognitionUnavailableError(
                    "Face embedding could not be extracted."
                ) from exc

        return normalize_embedding(embedding)

    @staticmethod
    def _validate_image_quality(image: np.ndarray) -> None:
        image_height, image_width = image.shape[:2]
        if min(image_height, image_width) < 96:
            raise PoorImageQualityError(
                "The photo is too small; use a clear image at least 96 pixels wide and tall."
            )

        try:
            grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            brightness = float(np.mean(grayscale))
            sharpness = float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
        except cv2.error as exc:
            raise FaceRecognitionUnavailableError(
                "Image quality could not be evaluated."
            ) from exc

        if brightness < 15 or brightness > 240:
            raise PoorImageQualityError(
                "The photo is too dark or overexposed; use even lighting."
            )
        if sharpness < 5:
            raise PoorImageQualityError(
                "The photo is too blurred or low-detail; retake it in focus."
            )


face_embedding_service = SFaceEmbeddingService()
