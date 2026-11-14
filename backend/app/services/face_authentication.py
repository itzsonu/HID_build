from dataclasses import dataclass

import numpy as np

from app.schemas.auth import IdentityStatus
from app.services.face_recognition import (
    InvalidFaceEmbeddingError,
    SFaceEmbeddingService,
    cosine_similarity,
    normalize_embedding,
)
from app.services.face_store import EMBEDDING_SIZE, SQLiteFaceStore


@dataclass(frozen=True)
class FaceIdentityEvaluation:
    identity_status: IdentityStatus
    similarity: float | None
    threshold: float
    message: str
    matched_user_id: str | None = None


def verify_enrolled_face(
    user_id: str,
    image_bytes: bytes,
    store: SQLiteFaceStore,
    embedding_service: SFaceEmbeddingService,
) -> FaceIdentityEvaluation:
    threshold = embedding_service.threshold
    stored_embedding = store.get_embedding(user_id)
    if stored_embedding is None:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_CHECKED,
            None,
            threshold,
            "User is not enrolled.",
        )

    submitted = embedding_service.extract_embedding(image_bytes)
    enrolled = normalize_embedding(np.frombuffer(stored_embedding, dtype="<f4"))
    similarity = cosine_similarity(submitted, enrolled)
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
    templates = store.get_all_embeddings()
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
    try:
        enrolled_matrix = np.frombuffer(
            b"".join(stored_embedding for _, stored_embedding in templates),
            dtype="<f4",
        ).reshape(len(templates), EMBEDDING_SIZE)
    except ValueError as exc:
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        ) from exc

    if not np.isfinite(enrolled_matrix).all():
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        )
    norms = np.linalg.norm(enrolled_matrix, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
        raise InvalidFaceEmbeddingError(
            "An enrolled face representation is invalid."
        )
    normalized_matrix = enrolled_matrix / norms[:, np.newaxis]
    similarities = normalized_matrix @ normalize_embedding(submitted)
    matching_indices = np.flatnonzero(similarities >= threshold)

    if matching_indices.size > 1:
        return FaceIdentityEvaluation(
            IdentityStatus.AMBIGUOUS,
            float(np.max(similarities[matching_indices])),
            threshold,
            "More than one enrolled identity matched; additional verification is required.",
        )
    if matching_indices.size == 0:
        return FaceIdentityEvaluation(
            IdentityStatus.NOT_VERIFIED,
            None,
            threshold,
            "No enrolled face matched this person.",
        )

    matched_index = int(matching_indices[0])
    matched_user_id = templates[matched_index][0]
    similarity = float(similarities[matched_index])
    return FaceIdentityEvaluation(
        IdentityStatus.VERIFIED,
        similarity,
        threshold,
        "The face matched one enrolled representation.",
        matched_user_id,
    )
