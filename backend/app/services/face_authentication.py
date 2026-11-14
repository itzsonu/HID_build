from dataclasses import dataclass

import numpy as np

from app.schemas.auth import IdentityStatus
from app.services.face_recognition import (
    SFaceEmbeddingService,
    cosine_similarity,
    normalize_embedding,
)
from app.services.face_store import SQLiteFaceStore


@dataclass(frozen=True)
class FaceIdentityEvaluation:
    identity_status: IdentityStatus
    similarity: float | None
    threshold: float
    message: str


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
    return FaceIdentityEvaluation(status, similarity, threshold, message)
