import os
from pathlib import Path
from typing import Final

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings:
    APP_NAME: Final[str] = os.getenv("APP_NAME", "HID Face Liveness Detection System")
    APP_VERSION: Final[str] = os.getenv("APP_VERSION", "0.1.0")
    APP_ENVIRONMENT: Final[str] = os.getenv("APP_ENVIRONMENT", "development")
    FACE_MATCH_THRESHOLD: Final[float] = float(
        os.getenv("FACE_MATCH_THRESHOLD", "0.363")
    )
    BIOMETRIC_DB_PATH: Final[str] = os.getenv(
        "BIOMETRIC_DB_PATH",
        str(BACKEND_DIR / "data" / "face_embeddings.sqlite3"),
    )
    CAMPUS_SCHEDULE_PATH: Final[str] = os.getenv(
        "CAMPUS_SCHEDULE_PATH",
        str(BACKEND_DIR / "config" / "campus_schedule.json"),
    )
    OFFLINE_EVENTS_DB_PATH: Final[str] = os.getenv(
        "OFFLINE_EVENTS_DB_PATH",
        str(BACKEND_DIR / "data" / "offline_events.sqlite3"),
    )
    ALLOWED_ORIGINS: Final[list[str]] = [
        origin.strip()
        for origin in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
        ).split(",")
        if origin.strip()
    ]


settings = Settings()
