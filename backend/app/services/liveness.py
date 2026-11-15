import math
import secrets
import threading
import time
from dataclasses import dataclass
from statistics import median
from typing import Callable

from app.schemas.liveness import LivenessChallenge, LivenessStatus
from app.services.face_detection import FaceDetectionError, face_detector

CHALLENGE_TTL_SECONDS = 60


class ChallengeError(Exception):
    """Base exception for challenge-store errors."""


class ChallengeNotFoundError(ChallengeError):
    """Raised when a challenge identifier is unknown."""


class ChallengeExpiredError(ChallengeError):
    """Raised when a challenge has expired."""


class ChallengeAlreadyUsedError(ChallengeError):
    """Raised when a challenge has already been consumed."""


@dataclass(frozen=True)
class _ChallengeRecord:
    challenge: LivenessChallenge
    expires_at: float


class InMemoryChallengeStore:
    def __init__(
        self,
        ttl_seconds: int = CHALLENGE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._active: dict[str, _ChallengeRecord] = {}
        self._expired: dict[str, float] = {}
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for challenge_id, record in list(self._active.items()):
            if record.expires_at <= now:
                del self._active[challenge_id]
                self._expired[challenge_id] = now + self.ttl_seconds
        for challenge_id, expires_at in list(self._expired.items()):
            if expires_at <= now:
                del self._expired[challenge_id]
        for challenge_id, expires_at in list(self._used.items()):
            if expires_at <= now:
                del self._used[challenge_id]
                self._expired[challenge_id] = now + self.ttl_seconds

    def create(self) -> tuple[str, LivenessChallenge]:
        with self._lock:
            now = self._clock()
            self._purge(now)
            challenge_id = secrets.token_urlsafe(24)
            while (
                challenge_id in self._active
                or challenge_id in self._expired
                or challenge_id in self._used
            ):
                challenge_id = secrets.token_urlsafe(24)
            challenge = secrets.choice(tuple(LivenessChallenge))
            self._active[challenge_id] = _ChallengeRecord(
                challenge=challenge,
                expires_at=now + self.ttl_seconds,
            )
            return challenge_id, challenge

    def consume(self, challenge_id: str) -> LivenessChallenge:
        with self._lock:
            now = self._clock()
            self._purge(now)
            record = self._active.pop(challenge_id, None)
            if record is not None:
                self._used[challenge_id] = record.expires_at
                return record.challenge
            if challenge_id in self._expired:
                raise ChallengeExpiredError("Challenge has expired.")
            if challenge_id in self._used:
                raise ChallengeAlreadyUsedError("Challenge has already been used.")
            raise ChallengeNotFoundError("Challenge was not found.")


@dataclass(frozen=True)
class LivenessEvaluation:
    status: LivenessStatus
    confidence: float
    movement_detected: bool
    valid_frame_count: int
    total_frame_count: int
    message: str
    last_valid_frame_index: int | None


class MovementAnalyzer:
    def __init__(
        self,
        movement_threshold: float = 0.08,
        consistency_threshold: float = 0.6,
        minimum_valid_ratio: float = 0.6,
        minimum_valid_frames: int = 5,
        noise_step_threshold: float = 0.005,
        stationary_threshold: float = 0.025,
    ) -> None:
        if not 0 < movement_threshold <= 1:
            raise ValueError("movement_threshold must be in (0, 1].")
        if not 0 < consistency_threshold <= 1:
            raise ValueError("consistency_threshold must be in (0, 1].")
        if not 0 < minimum_valid_ratio <= 1:
            raise ValueError("minimum_valid_ratio must be in (0, 1].")
        if minimum_valid_frames < 5:
            raise ValueError("minimum_valid_frames must be at least 5.")
        if not 0 <= noise_step_threshold < movement_threshold:
            raise ValueError(
                "noise_step_threshold must be non-negative and below movement_threshold."
            )
        if not 0 <= stationary_threshold < movement_threshold:
            raise ValueError(
                "stationary_threshold must be non-negative and below movement_threshold."
            )
        self.movement_threshold = movement_threshold
        self.consistency_threshold = consistency_threshold
        self.minimum_valid_ratio = minimum_valid_ratio
        self.minimum_valid_frames = minimum_valid_frames
        self.noise_step_threshold = noise_step_threshold
        self.stationary_threshold = stationary_threshold

    def evaluate(
        self,
        challenge: LivenessChallenge,
        frames: list[bytes],
    ) -> LivenessEvaluation:
        centers_x: list[float] = []
        last_valid_frame_index: int | None = None
        for frame_index, frame in enumerate(frames):
            result = face_detector.detect(frame)
            if len(result.faces) != 1:
                continue
            x, _, width, _ = result.faces[0]
            centers_x.append((x + width / 2) / result.image_width)
            last_valid_frame_index = frame_index

        total_frames = len(frames)
        minimum_valid = max(
            self.minimum_valid_frames,
            math.ceil(total_frames * self.minimum_valid_ratio),
        )
        if len(centers_x) < minimum_valid:
            return self._result(
                LivenessStatus.UNCERTAIN,
                0.0,
                False,
                len(centers_x),
                total_frames,
                "Liveness is uncertain because too few frames contained exactly one detectable face; no tested presentation-attack detection model is configured.",
                last_valid_frame_index,
            )

        window_size = max(1, math.ceil(len(centers_x) / 3))
        early_center = median(centers_x[:window_size])
        late_center = median(centers_x[-window_size:])
        direction = -1 if challenge is LivenessChallenge.MOVE_LEFT else 1
        directed_shift = (late_center - early_center) * direction
        movement_detected = directed_shift >= self.movement_threshold

        directed_steps = [
            (later - earlier) * direction
            for earlier, later in zip(centers_x, centers_x[1:])
            if abs(later - earlier) >= self.noise_step_threshold
        ]
        consistency = (
            sum(step > 0 for step in directed_steps) / len(directed_steps)
            if directed_steps
            else 0.0
        )
        progress = max(0.0, min(1.0, directed_shift / self.movement_threshold))
        confidence = round(progress * consistency, 3)
        total_range = max(centers_x) - min(centers_x)

        if total_range < self.stationary_threshold or directed_shift <= -self.movement_threshold:
            return self._result(
                LivenessStatus.SPOOF_SUSPECTED,
                confidence,
                movement_detected,
                len(centers_x),
                total_frames,
                "The requested movement was not observed; the challenge failed. This does not prove a spoof, and no tested presentation-attack detection model is configured.",
                last_valid_frame_index,
            )
        if movement_detected and consistency >= self.consistency_threshold:
            return self._result(
                LivenessStatus.UNCERTAIN,
                confidence,
                True,
                len(centers_x),
                total_frames,
                "Requested movement was observed, but movement alone cannot distinguish a live person from a video replay. No tested presentation-attack detection model is configured; additional approved verification is required.",
                last_valid_frame_index,
            )
        return self._result(
            LivenessStatus.UNCERTAIN,
            confidence,
            movement_detected,
            len(centers_x),
            total_frames,
            "Movement evidence was insufficient or inconsistent, and no tested presentation-attack detection model is configured.",
            last_valid_frame_index,
        )

    @staticmethod
    def _result(
        status: LivenessStatus,
        confidence: float,
        movement_detected: bool,
        valid_frame_count: int,
        total_frame_count: int,
        message: str,
        last_valid_frame_index: int | None,
    ) -> LivenessEvaluation:
        return LivenessEvaluation(
            status=status,
            confidence=confidence,
            movement_detected=movement_detected,
            valid_frame_count=valid_frame_count,
            total_frame_count=total_frame_count,
            message=message,
            last_valid_frame_index=last_valid_frame_index,
        )


challenge_store = InMemoryChallengeStore()
movement_analyzer = MovementAnalyzer()
