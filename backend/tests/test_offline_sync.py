from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.routes import access as access_routes
from app.main import app
from app.services.offline_events import OfflineEventStore

client = TestClient(app)


def offline_event(event_id: str | None = None) -> dict[str, str]:
    return {
        "event_id": event_id or str(uuid4()),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "decision": "STEP_UP_VERIFICATION",
        "reason": "BACKEND_UNAVAILABLE",
    }


def test_offline_events_sync_idempotently_without_identity_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        access_routes,
        "offline_event_store",
        OfflineEventStore(tmp_path / "offline.sqlite3"),
    )
    event = offline_event()

    first = client.post("/api/access/offline-sync", json={"events": [event]})
    second = client.post("/api/access/offline-sync", json={"events": [event]})

    assert first.status_code == 200
    assert first.json()["accepted_event_ids"] == [event["event_id"]]
    assert second.json()["accepted_event_ids"] == [event["event_id"]]


def test_offline_sync_refuses_allow_and_unnecessary_personal_data():
    event = offline_event()
    event["decision"] = "ALLOW"
    allow_response = client.post("/api/access/offline-sync", json={"events": [event]})
    assert allow_response.status_code == 422

    event = offline_event()
    event["user_id"] = "student-123"
    pii_response = client.post("/api/access/offline-sync", json={"events": [event]})
    assert pii_response.status_code == 422


def test_offline_store_discards_events_older_than_retention(tmp_path):
    store = OfflineEventStore(tmp_path / "offline.sqlite3")
    event = offline_event()
    event["occurred_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    from app.schemas.access import OfflineDecisionEvent

    accepted = store.accept([OfflineDecisionEvent.model_validate(event)])

    assert accepted == []
