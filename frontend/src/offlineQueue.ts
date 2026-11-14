export interface OfflineDecisionEvent {
  event_id: string;
  occurred_at: string;
  decision: "STEP_UP_VERIFICATION";
  reason: "BACKEND_UNAVAILABLE";
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const STORAGE_KEY = "hid-offline-decisions-v1";
const MAX_PENDING_EVENTS = 50;
const EVENT_TTL_MS = 24 * 60 * 60 * 1000;

function validEvent(value: unknown, now: number): value is OfflineDecisionEvent {
  if (!value || typeof value !== "object") {
    return false;
  }
  const event = value as Partial<OfflineDecisionEvent>;
  const timestamp = Date.parse(event.occurred_at ?? "");
  return (
    typeof event.event_id === "string" &&
    event.event_id.length <= 64 &&
    Number.isFinite(timestamp) &&
    timestamp >= now - EVENT_TTL_MS &&
    timestamp <= now + 5 * 60 * 1000 &&
    event.decision === "STEP_UP_VERIFICATION" &&
    event.reason === "BACKEND_UNAVAILABLE"
  );
}

export function loadOfflineEvents(
  storage: StorageLike = localStorage,
  now = Date.now(),
): OfflineDecisionEvent[] {
  try {
    const stored = storage.getItem(STORAGE_KEY);
    const parsed: unknown = stored ? JSON.parse(stored) : [];
    if (!Array.isArray(parsed)) {
      storage.setItem(STORAGE_KEY, "[]");
      return [];
    }
    const events = parsed.filter((event) => validEvent(event, now)).slice(-MAX_PENDING_EVENTS);
    storage.setItem(STORAGE_KEY, JSON.stringify(events));
    return events;
  } catch {
    return [];
  }
}

export function recordOfflineStepUp(
  storage: StorageLike = localStorage,
  now = Date.now(),
  eventId = crypto.randomUUID(),
): OfflineDecisionEvent | null {
  try {
    const events = loadOfflineEvents(storage, now);
    if (events.length >= MAX_PENDING_EVENTS) {
      return null;
    }
    const event: OfflineDecisionEvent = {
      event_id: eventId,
      occurred_at: new Date(now).toISOString(),
      decision: "STEP_UP_VERIFICATION",
      reason: "BACKEND_UNAVAILABLE",
    };
    storage.setItem(STORAGE_KEY, JSON.stringify([...events, event]));
    return event;
  } catch {
    return null;
  }
}

export function removeSyncedOfflineEvents(
  acceptedIds: string[],
  storage: StorageLike = localStorage,
): OfflineDecisionEvent[] {
  const accepted = new Set(acceptedIds);
  const remaining = loadOfflineEvents(storage).filter((event) => !accepted.has(event.event_id));
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(remaining));
  } catch {
    return [];
  }
  return remaining;
}
