from dataclasses import dataclass

from app.schemas.access import AccessDecision, ScheduleStatus
from app.schemas.auth import IdentityStatus
from app.schemas.liveness import LivenessStatus


@dataclass(frozen=True)
class AccessPolicyResult:
    decision: AccessDecision
    reason: str


def decide_access(
    liveness_status: LivenessStatus,
    identity_status: IdentityStatus,
    schedule_status: ScheduleStatus,
) -> AccessPolicyResult:
    if liveness_status is LivenessStatus.SPOOF_SUSPECTED:
        return AccessPolicyResult(
            AccessDecision.DENY,
            "The movement challenge failed; access is denied. This heuristic does not prove a spoof.",
        )
    if identity_status is IdentityStatus.NOT_VERIFIED:
        return AccessPolicyResult(
            AccessDecision.DENY,
            "The face did not match the enrolled user; access is denied.",
        )
    if identity_status is IdentityStatus.AMBIGUOUS:
        return AccessPolicyResult(
            AccessDecision.STEP_UP_VERIFICATION,
            "More than one enrolled identity matched; an additional approved verification step is required.",
        )
    if identity_status is IdentityStatus.NOT_CHECKED:
        return AccessPolicyResult(
            AccessDecision.DENY,
            "Identity could not be checked; access is denied.",
        )
    if liveness_status is LivenessStatus.UNCERTAIN:
        return AccessPolicyResult(
            AccessDecision.STEP_UP_VERIFICATION,
            "Liveness is uncertain; no tested presentation-attack detection model is configured. An additional approved verification step is required.",
        )
    if identity_status is not IdentityStatus.VERIFIED:
        return AccessPolicyResult(
            AccessDecision.DENY,
            "Identity status was unavailable; access is denied.",
        )
    if schedule_status is ScheduleStatus.UNAVAILABLE:
        return AccessPolicyResult(
            AccessDecision.STEP_UP_VERIFICATION,
            "The campus schedule is unavailable or invalid; access is not granted and additional verification is required.",
        )
    if schedule_status is ScheduleStatus.OUTSIDE_SCHEDULE:
        return AccessPolicyResult(
            AccessDecision.STEP_UP_VERIFICATION,
            "The campus schedule does not permit access now; access is not granted.",
        )
    if liveness_status in (LivenessStatus.LIVE, LivenessStatus.UNCERTAIN):
        return AccessPolicyResult(
            AccessDecision.STEP_UP_VERIFICATION,
            "A tested presentation-attack detection model is not configured. Movement evidence cannot establish liveness; additional approved verification is required.",
        )
    return AccessPolicyResult(
        AccessDecision.STEP_UP_VERIFICATION,
        "Liveness could not be established by a tested presentation-attack detection model; access is not granted.",
    )
