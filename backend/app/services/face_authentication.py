from dataclasses import dataclass

import numpy as np

from app.schemas.auth import IdentityStatus
from app.services.face_recognition import (
    InvalidFaceEmbeddingError,
    SFaceEmbeddingService,
    normalize_embedding,
)
from app.services.face_store import EMBEDDING_BYTES, EMBEDDING_SIZE, SQLiteFaceStore


@dataclass(frozen=True)
class FaceIdentityEvaluation:
    identity_status: IdentityStatus
    similarity: float | None
    threshold: float
    message: str
    matched_user_id: str | None = None


def _normalized_template_matrix(templates: list[bytes]) -> np.ndarray:
    if not templates or any(len(template) != EMBEDDING_BYTES for template in templates):
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        )
    try:
        matrix = np.stack(
            [
                np.frombuffer(template, dtype="<f4").reshape(EMBEDDING_SIZE)
                for template in templates
            ]
        )
    except ValueError as exc:
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        ) from exc

    if not np.isfinite(matrix).all():
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        )
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        )
    return matrix / norms[:, np.newaxis]


def verify_enrolled_face(
    user_id: str,
    image_bytes: bytes,
    store: SQLiteFaceStore,
    embedding_service: SFaceEmbeddingService,
) -> FaceIdentityEvaluation:
    threshold = embedding_service.threshold
    stored_templates = store.get_templates(user_id)
    if not stored_templates:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            threshold,
            "User is not enrolled.",
        )

    submitted = embedding_service.extract_embedding(image_bytes)
    enrolled_matrix = _normalized_template_matrix(stored_templates)
    similarities = enrolled_matrix @ normalize_embedding(submitted)
    similarity = float(np.max(similarities))
    status = (
        IdentityStatus.VERIFIED
        if similarity >= threshold
        else IdentityStatus.NOT_VERIFIED
    )
    message = (
        "The face matched the enrolled representation."
        if status is IdentityStatus.VERIFIED
        else "The face did not meet the configured similarity threshold."
    )
    return FaceIdentityEvaluation(
        status,
        similarity,
        threshold,
        message,
        user_id if status is IdentityStatus.VERIFIED else None,
    )


def identify_enrolled_face(
    image_bytes: bytes,
    store: SQLiteFaceStore,
    embedding_service: SFaceEmbeddingService,
) -> FaceIdentityEvaluation:
    threshold = embedding_service.threshold
    templates = store.get_all_templates()
    if not templates:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_VERIFIED,
            None,
            threshold,
            "No enrolled face matched this person.",
        )

    submitted = embedding_service.extract_embedding(
        image_bytes,
        require_quality=True,
    )
    normalized_matrix = _normalized_template_matrix(
        [stored_embedding for _, stored_embedding in templates]
    )
    similarities = normalized_matrix @ normalize_embedding(submitted)
    best_by_user: dict[str, float] = {}
    for (user_id, _), similarity in zip(templates, similarities, strict=True):
        best_by_user[user_id] = max(
            best_by_user.get(user_id, float("-inf")),
            float(similarity),
        )

    matching_users = [
        (user_id, similarity)
        for user_id, similarity in best_by_user.items()
        if similarity >= threshold
    ]

    if len(matching_users) > 1:
        return FaceIdentityEvaluation(
            IdentityStatus.AMBIGUOUS,
            max(similarity for _, similarity in matching_users),
            threshold,
            "More than one enrolled user met the configured match threshold; additional verification is required.",
        )
    if not matching_users:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_VERIFIED,
            None,
            threshold,
            "No enrolled face matched this person.",
        )

    matched_user_id, similarity = matching_users[0]
    return FaceIdentityEvaluation(
        IdentityStatus.VERIFIED,
        similarity,
        threshold,
        "The face uniquely matched one enrolled user using the best similarity across that user's templates.",
        matched_user_id,
    )
