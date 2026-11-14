import assert from "node:assert/strict";
import test from "node:test";

import {
  loadOfflineEvents,
  recordOfflineStepUp,
  removeSyncedOfflineEvents,
} from "../src/offlineQueue.ts";

function createStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test("offline decisions store only a step-up event and clear after sync", () => {
  const storage = createStorage();
  const occurredAt = Date.UTC(2026, 10, 14, 8);
  const event = recordOfflineStepUp(storage, occurredAt, "event-1");

  assert.deepEqual(event, {
    event_id: "event-1",
    occurred_at: new Date(occurredAt).toISOString(),
    decision: "STEP_UP_VERIFICATION",
    reason: "BACKEND_UNAVAILABLE",
  });
  assert.deepEqual(removeSyncedOfflineEvents(["event-1"], storage), []);
});

test("offline queue drops expired, malformed, and access-grant records", () => {
  const storage = createStorage();
  storage.setItem(
    "hid-offline-decisions-v1",
    JSON.stringify([
      { event_id: "allow", occurred_at: new Date().toISOString(), decision: "ALLOW" },
      { event_id: "old", occurred_at: "2020-01-01T00:00:00Z", decision: "STEP_UP_VERIFICATION", reason: "BACKEND_UNAVAILABLE" },
      { event_id: "kept", occurred_at: new Date().toISOString(), decision: "STEP_UP_VERIFICATION", reason: "BACKEND_UNAVAILABLE" },
    ]),
  );

  assert.deepEqual(loadOfflineEvents(storage).map((event) => event.event_id), ["kept"]);
});
