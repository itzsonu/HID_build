export interface HealthResponse {
  status: string;
  application: string;
  version: string;
}

export type LivenessChallenge = "MOVE_LEFT" | "MOVE_RIGHT";
export type LivenessStatus = "LIVE" | "UNCERTAIN" | "SPOOF_SUSPECTED";

export interface ChallengeResponse {
  challenge_id: string;
  challenge: LivenessChallenge;
  expires_in_seconds: number;
}

export interface LivenessResponse {
  success: boolean;
  challenge_id: string;
  liveness_status: LivenessStatus;
  confidence: number;
  movement_detected: boolean;
  valid_frame_count: number;
  total_frame_count: number;
  processing_time_ms: number;
  message: string;
}

export type IdentityStatus = "VERIFIED" | "NOT_VERIFIED";
export type AccessIdentityStatus = IdentityStatus | "NOT_CHECKED" | "AMBIGUOUS";
export type AccessDecision = "ALLOW" | "STEP_UP_VERIFICATION" | "DENY";
export type ScheduleStatus = "WITHIN_SCHEDULE" | "OUTSIDE_SCHEDULE" | "UNAVAILABLE";

export interface AccessDecisionResponse {
  success: boolean;
  decision: AccessDecision;
  challenge_id: string;
  liveness_status: LivenessStatus;
  identity_status: AccessIdentityStatus;
  schedule_status: ScheduleStatus;
  reason: string;
  identity_message: string;
  matched_user_id: string | null;
  requires_step_up: boolean;
  movement_detected: boolean;
  valid_frame_count: number;
  total_frame_count: number;
  processing_time_ms: number;
}

export interface OfflineDecisionSyncResponse {
  accepted_event_ids: string[];
}

export interface FaceRegistrationResponse {
  success: boolean;
  registered: boolean;
  user_id: string;
  processing_time_ms: number;
  message: string;
}

export type EnrollmentPhotoStatus = "ACCEPTED" | "REJECTED" | "DUPLICATE";

export interface EnrollmentPhotoResult {
  photo_index: number;
  status: EnrollmentPhotoStatus;
  message: string;
}

export interface BatchFaceRegistrationResponse {
  success: boolean;
  registered: boolean;
  updated: boolean;
  user_id: string;
  photo_results: EnrollmentPhotoResult[];
  accepted_photo_count: number;
  rejected_photo_count: number;
  duplicate_photo_count: number;
  processing_time_ms: number;
  message: string;
}

export interface EnrollmentSummary {
  user_id: string;
  template_count: number;
}

export interface EnrollmentListResponse {
  enrollments: EnrollmentSummary[];
}

export interface EnrollmentDeleteResponse {
  deleted: boolean;
  user_id: string;
}

export interface FaceVerificationResponse {
  success: boolean;
  user_id: string;
  identity_status: IdentityStatus;
  similarity: number;
  threshold: number;
  processing_time_ms: number;
  message: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export const configuredApiUrl = API_BASE_URL;

function getErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String(item.msg)
            : "",
        )
        .filter(Boolean);
      if (messages.length) {
        return messages.join(" ");
      }
    }
  }
  return `The backend returned HTTP ${status}.`;
}

async function requestJson<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "DELETE";
    body?: unknown;
    signal?: AbortSignal;
    timeoutMs?: number;
    headers?: Record<string, string>;
  } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? 15_000;
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const relayAbort = () => controller.abort();
  options.signal?.addEventListener("abort", relayAbort, { once: true });

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      throw new ApiError(getErrorMessage(payload, response.status), response.status);
    }
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (timedOut) {
      throw new ApiError("The backend request timed out. Please try again.");
    }
    if (options.signal?.aborted) {
      throw new ApiError("The verification request was cancelled.");
    }
    throw new ApiError(
      "Could not reach the backend. Check that it is running and accessible from this device.",
    );
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", relayAbort);
  }
}

export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/health", { timeoutMs: 5_000 });
}

export function createChallenge(signal: AbortSignal): Promise<ChallengeResponse> {
  return requestJson<ChallengeResponse>("/api/liveness/challenge", {
    method: "POST",
    body: {},
    signal,
  });
}

export function checkLiveness(
  challengeId: string,
  frames: string[],
  signal: AbortSignal,
): Promise<LivenessResponse> {
  return requestJson<LivenessResponse>("/api/liveness/check", {
    method: "POST",
    body: { challenge_id: challengeId, frames },
    signal,
    timeoutMs: 30_000,
  });
}

export function decideAccess(
  challengeId: string,
  frames: string[],
  signal: AbortSignal,
  userId?: string,
): Promise<AccessDecisionResponse> {
  return requestJson<AccessDecisionResponse>("/api/access/decision", {
    method: "POST",
    body: {
      challenge_id: challengeId,
      frames,
      ...(userId ? { user_id: userId } : {}),
    },
    signal,
    timeoutMs: 45_000,
  });
}

export function syncOfflineDecisions(
  events: OfflineDecisionEvent[],
): Promise<OfflineDecisionSyncResponse> {
  return requestJson<OfflineDecisionSyncResponse>("/api/access/offline-sync", {
    method: "POST",
    body: { events },
    timeoutMs: 10_000,
  });
}

export function registerFace(
  userId: string,
  imageBase64: string,
  consent: boolean,
  adminSecret: string,
  signal?: AbortSignal,
): Promise<FaceRegistrationResponse> {
  return requestJson<FaceRegistrationResponse>("/api/auth/register", {
    method: "POST",
    body: { user_id: userId, consent, image_base64: imageBase64 },
    signal,
    headers: { Authorization: `Bearer ${adminSecret}` },
  });
}

export function registerFacesBatch(
  userId: string,
  imagesBase64: string[],
  consent: boolean,
  adminSecret: string,
  signal?: AbortSignal,
): Promise<BatchFaceRegistrationResponse> {
  return requestJson<BatchFaceRegistrationResponse>("/api/auth/register/batch", {
    method: "POST",
    body: {
      user_id: userId,
      images_base64: imagesBase64,
      consent,
    },
    signal,
    timeoutMs: 60_000,
    headers: { Authorization: `Bearer ${adminSecret}` },
  });
}

export function getAdminEnrollments(
  adminSecret: string,
): Promise<EnrollmentListResponse> {
  return requestJson<EnrollmentListResponse>("/api/admin/enrollments", {
    headers: { Authorization: `Bearer ${adminSecret}` },
  });
}

export function deleteAdminEnrollment(
  userId: string,
  adminSecret: string,
): Promise<EnrollmentDeleteResponse> {
  return requestJson<EnrollmentDeleteResponse>("/api/admin/enrollments", {
    method: "DELETE",
    body: { user_id: userId },
    headers: { Authorization: `Bearer ${adminSecret}` },
  });
}

export function verifyFace(
  userId: string,
  imageBase64: string,
  signal?: AbortSignal,
): Promise<FaceVerificationResponse> {
  return requestJson<FaceVerificationResponse>("/api/auth/verify", {
    method: "POST",
    body: { user_id: userId, image_base64: imageBase64 },
    signal,
  });
}
import type { OfflineDecisionEvent } from "../offlineQueue";

