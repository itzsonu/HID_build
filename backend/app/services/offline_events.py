import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from app.core.config import settings
from app.schemas.access import OfflineDecisionEvent

RETENTION = timedelta(days=7)


class OfflineEventStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or settings.OFFLINE_EVENTS_DB_PATH)

    def accept(self, events: list[OfflineDecisionEvent]) -> list[UUID]:
        now = datetime.now(timezone.utc)
        oldest_allowed = now - RETENTION
        newest_allowed = now + timedelta(minutes=5)
        accepted: list[UUID] = []
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(str(self.database_path), timeout=5)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS offline_decision_events (
                            event_id TEXT PRIMARY KEY,
                            occurred_at TEXT NOT NULL,
                            received_at TEXT NOT NULL,
                            reason TEXT NOT NULL CHECK(reason = 'BACKEND_UNAVAILABLE')
                        )
                        """
                    )
                    connection.execute(
                        "DELETE FROM offline_decision_events WHERE received_at < ?",
                        ((now - RETENTION).isoformat(),),
                    )
                    for event in events:
                        if not oldest_allowed <= event.occurred_at <= newest_allowed:
                            continue
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO offline_decision_events
                                (event_id, occurred_at, received_at, reason)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                str(event.event_id),
                                event.occurred_at.isoformat(),
                                now.isoformat(),
                                event.reason,
                            ),
                        )
                        accepted.append(event.event_id)
            return accepted
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError("Offline decision synchronization is unavailable.") from exc


offline_event_store = OfflineEventStore()
