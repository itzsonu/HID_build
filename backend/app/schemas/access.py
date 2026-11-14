from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import IdentityStatus
from app.schemas.liveness import LivenessCheckRequest, LivenessStatus


class AccessDecision(str, Enum):
    ALLOW = "ALLOW"
    STEP_UP_VERIFICATION = "STEP_UP_VERIFICATION"
    DENY = "DENY"


class ScheduleStatus(str, Enum):
    WITHIN_SCHEDULE = "WITHIN_SCHEDULE"
    OUTSIDE_SCHEDULE = "OUTSIDE_SCHEDULE"
    UNAVAILABLE = "UNAVAILABLE"


class AccessDecisionRequest(LivenessCheckRequest):
    user_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be empty.")
        return normalized


class AccessDecisionResponse(BaseModel):
    success: bool
    decision: AccessDecision
    challenge_id: str
    liveness_status: LivenessStatus
    identity_status: IdentityStatus
    schedule_status: ScheduleStatus
    reason: str
    identity_message: str = ""
    matched_user_id: str | None = None
    requires_step_up: bool
    movement_detected: bool
    valid_frame_count: int = Field(ge=0)
    total_frame_count: int = Field(ge=5, le=20)
    processing_time_ms: float = Field(ge=0)


class OfflineDecisionEvent(BaseModel):
    model_config = {"extra": "forbid"}

    event_id: UUID
    occurred_at: datetime
    decision: Literal[AccessDecision.STEP_UP_VERIFICATION]
    reason: Literal["BACKEND_UNAVAILABLE"]

    @field_validator("occurred_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone.")
        return value.astimezone(timezone.utc)


class OfflineSyncRequest(BaseModel):
    model_config = {"extra": "forbid"}

    events: list[OfflineDecisionEvent] = Field(min_length=1, max_length=50)


class OfflineSyncResponse(BaseModel):
    accepted_event_ids: list[UUID]
