import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

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
        embedding_blob = np.asarray(embedding, dtype="<f4").reshape(-1).tobytes()
        if len(embedding_blob) != EMBEDDING_BYTES:
            raise FaceStoreUnavailableError(
                "The face representation has an unexpected size."
            )

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
        except sqlite3.IntegrityError as exc:
            raise DuplicateUserError("This user ID is already enrolled.") from exc
        except sqlite3.Error as exc:
            raise FaceStoreUnavailableError(
                "The local face database is unavailable."
            ) from exc

    def upsert_template(self, user_id: str, embedding: np.ndarray) -> bool:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        embedding_blob = np.asarray(embedding, dtype="<f4").reshape(-1).tobytes()
        if len(embedding_blob) != EMBEDDING_BYTES:
            raise FaceStoreUnavailableError(
                "The face representation has an unexpected size."
            )

        try:
            with closing(self._open()) as connection:
                with connection:
                    self._initialize(connection)
                    existing = connection.execute(
                        "SELECT 1 FROM face_embeddings WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO face_embeddings
                            (user_id, embedding, consented_at, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            embedding = excluded.embedding,
                            consented_at = excluded.consented_at
                        """,
                        (user_id, embedding_blob, now, now),
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
