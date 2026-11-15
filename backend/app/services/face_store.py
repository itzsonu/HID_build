import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from app.core.config import settings

EMBEDDING_SIZE = 128
EMBEDDING_BYTES = EMBEDDING_SIZE * np.dtype("<f4").itemsize


class FaceStoreError(Exception):
    """Base exception for local biometric-store errors."""


class DuplicateUserError(FaceStoreError):
    """Raised when a user ID already has an enrolled face."""


class FaceStoreUnavailableError(FaceStoreError):
    """Raised when the SQLite store cannot be read or written."""


class SQLiteFaceStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or settings.BIOMETRIC_DB_PATH)

    def _open(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(str(self.database_path), timeout=5)
        except (OSError, sqlite3.Error) as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS face_embeddings (
                user_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL CHECK(length(embedding) = 512),
                consented_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS face_templates (
                template_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                embedding BLOB NOT NULL CHECK(length(embedding) = 512),
                photo_sha256 TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, photo_sha256)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO face_templates (user_id, embedding, photo_sha256, created_at)
            SELECT legacy.user_id, legacy.embedding, NULL, legacy.created_at
            FROM face_embeddings AS legacy
            WHERE NOT EXISTS (
                SELECT 1
                FROM face_templates AS template
                WHERE template.user_id = legacy.user_id
            )
            """
        )

    @staticmethod
    def _embedding_blob(embedding: np.ndarray) -> bytes:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.size != EMBEDDING_SIZE or not np.isfinite(vector).all():
            raise FaceStoreUnavailableError(
                "The face representation has an unexpected size or value."
            )
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm <= 0:
            raise FaceStoreUnavailableError(
                "The face representation is invalid."
            )
        blob = (vector / norm).astype("<f4").tobytes()
        if len(blob) != EMBEDDING_BYTES:
            raise FaceStoreUnavailableError(
                "The face representation has an unexpected size."
            )
        return blob

    def contains(self, user_id: str) -> bool:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    row = connection.execute(
                        "SELECT 1 FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    return row is not None
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def insert(self, user_id: str, embedding: np.ndarray) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        embedding_blob = self._embedding_blob(embedding)

        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    connection.execute(
                        """
                        INSERT INTO face_embeddings
                            (user_id, embedding, consented_at, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (user_id, embedding_blob, now, now),
                    )
                    connection.execute(
                        """
                        INSERT INTO face_templates
                            (user_id, embedding, photo_sha256, created_at)
                        VALUES (?, ?, NULL, ?)
                        """,
                        (user_id, embedding_blob, now),
                    )
        except sqlite3.IntegrityError as exc:
            raise DuplicateUserError("This user ID is already enrolled.") from exc
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def upsert_template(self, user_id: str, embedding: np.ndarray) -> bool:
        return self.upsert_templates(user_id, [embedding], [None])

    def upsert_templates(
        self,
        user_id: str,
        embeddings: Sequence[np.ndarray],
        photo_sha256_values: Sequence[str | None],
    ) -> bool:
        if not embeddings or len(embeddings) != len(photo_sha256_values):
            raise FaceStoreUnavailableError(
                "At least one valid face template is required."
            )

        embedding_blobs = [self._embedding_blob(embedding) for embedding in embeddings]
        for digest in photo_sha256_values:
            if digest is not None and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise FaceStoreUnavailableError(
                    "A reference-photo digest is invalid."
                )

        vectors = np.stack(
            [np.frombuffer(blob, dtype="<f4") for blob in embedding_blobs]
        )
        aggregate_blob = self._embedding_blob(np.mean(vectors, axis=0))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    existing = connection.execute(
                        "SELECT 1 FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    connection.execute(
                        "DELETE FROM face_templates WHERE user_id = ?",
                        (user_id,),
                    )
                    connection.executemany(
                        """
                        INSERT INTO face_templates
                            (user_id, embedding, photo_sha256, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (user_id, blob, digest, now)
                            for blob, digest in zip(
                                embedding_blobs,
                                photo_sha256_values,
                                strict=True,
                            )
                        ],
                    )
                    connection.execute(
                        """
                        INSERT INTO face_embeddings
                            (user_id, embedding, consented_at, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            embedding = excluded.embedding,
                            consented_at = excluded.consented_at
                        """,
                        (user_id, aggregate_blob, now, now),
                    )
                    return existing is not None
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def get_templates(self, user_id: str) -> list[bytes]:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    rows = connection.execute(
                        """
                        SELECT embedding
                        FROM face_templates
                        WHERE user_id = ?
                        ORDER BY template_id
                        """,
                        (user_id,),
                    ).fetchall()
                    return [bytes(row[0]) for row in rows]
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def get_all_templates(self) -> list[tuple[str, bytes]]:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    rows = connection.execute(
                        """
                        SELECT user_id, embedding
                        FROM face_templates
                        ORDER BY user_id, template_id
                        """
                    ).fetchall()
                    return [
                        (str(user_id), bytes(embedding))
                        for user_id, embedding in rows
                    ]
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def list_template_counts(self) -> list[tuple[str, int]]:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    rows = connection.execute(
                        """
                        SELECT user_id, COUNT(*)
                        FROM face_templates
                        GROUP BY user_id
                        ORDER BY user_id
                        """
                    ).fetchall()
                    return [(str(user_id), int(count)) for user_id, count in rows]
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def delete_user(self, user_id: str) -> bool:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    existing = connection.execute(
                        "SELECT 1 FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    connection.execute(
                        "DELETE FROM face_templates WHERE user_id = ?",
                        (user_id,),
                    )
                    connection.execute(
                        "DELETE FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    )
                    return existing is not None
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def get_embedding(self, user_id: str) -> bytes | None:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    row = connection.execute(
                        "SELECT embedding FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    return None if row is None else bytes(row[0])
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def get_all_embeddings(self) -> list[tuple[str, bytes]]:
        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    rows = connection.execute(
                        "SELECT user_id, embedding FROM face_embeddings ORDER BY user_id"
                    ).fetchall()
                    return [(str(user_id), bytes(embedding)) for user_id, embedding in rows]
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc


face_store = SQLiteFaceStore()
