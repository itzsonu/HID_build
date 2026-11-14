import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  configuredApiUrl,
  createChallenge,
  decideAccess,
  getHealth,
  syncOfflineDecisions,
  type ChallengeResponse,
  type AccessDecision,
  type AccessIdentityStatus,
  type LivenessStatus,
  type ScheduleStatus,
} from "./services/api";
import { captureJpegFrame, validateFrameBatch } from "./frameCapture";
import { screenChallengeLabel, screenDirectionForChallenge } from "./challengeDisplay";
import {
  loadOfflineEvents,
  recordOfflineStepUp,
  removeSyncedOfflineEvents,
} from "./offlineQueue";

type ConnectionState = "checking" | "online" | "offline";
type VerificationPhase = "idle" | "countdown" | "capturing" | "submitting";
type DisplayStatus = AccessDecision | "ERROR";

interface DisplayResult {
  status: DisplayStatus;
  challenge: ChallengeResponse["challenge"] | null;
  livenessStatus?: LivenessStatus;
  identityStatus?: AccessIdentityStatus;
  scheduleStatus?: ScheduleStatus;
  requiresStepUp?: boolean;
  movementDetected?: boolean;
  validFrameCount?: number;
  totalFrameCount?: number;
  processingTimeMs?: number;
  message: string;
  identityMessage?: string;
}

const FRAME_COUNT = 10;
const FRAME_INTERVAL_MS = 260;

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function cameraErrorMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera permission was denied. Allow camera access in your browser settings, then try again.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No camera was found. Connect a camera or use a device with a front-facing camera.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The camera is already in use or unavailable. Close other camera apps and retry.";
  }
  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    return "Camera access on phones requires HTTPS. Use HTTPS on your network address, or open the app on localhost on the same device.";
  }
  return "Could not start the camera. Check browser support and camera permissions.";
}

function verificationErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 404:
        return "The challenge was not found. Start a new verification.";
      case 409:
        return "This challenge was already used. Start a new verification.";
      case 410:
        return "The challenge expired before submission. Start a new attempt.";
      case 413:
        return "The captured frames exceed the backend size limit. Try again with a lower-resolution camera.";
      case 422:
        return "The backend rejected the captured frame data. Start a new attempt and keep the camera preview visible.";
      default:
        return error.message;
    }
  }
  return error instanceof Error ? error.message : "The verification could not be completed.";
}

function statusLabel(status: DisplayStatus): string {
  if (status === "STEP_UP_VERIFICATION") {
    return "STEP-UP VERIFICATION";
  }
  return status;
}

function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const activeRunRef = useRef(0);
  const activeAbortRef = useRef<AbortController | null>(null);
  const cameraRequestRef = useRef(0);
  const mountedRef = useRef(true);
  const offlineSyncInFlightRef = useRef(false);

  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [healthMessage, setHealthMessage] = useState("Checking backend...");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [cameraStarting, setCameraStarting] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [phase, setPhase] = useState<VerificationPhase>("idle");
  const [isVerifying, setIsVerifying] = useState(false);
  const [challenge, setChallenge] = useState<ChallengeResponse | null>(null);
  const [userId, setUserId] = useState("");
  const [frameProgress, setFrameProgress] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [result, setResult] = useState<DisplayResult | null>(null);
  const [offlineEventCount, setOfflineEventCount] = useState(
    () => loadOfflineEvents().length,
  );

  const checkBackend = useCallback(async () => {
    setConnection("checking");
    setHealthMessage("Checking backend...");
    try {
      const health = await getHealth();
      if (mountedRef.current) {
        setConnection("online");
        setHealthMessage(`${health.application} · v${health.version}`);
      }
    } catch {
      if (mountedRef.current) {
        setConnection("offline");
        setHealthMessage("Backend unavailable");
      }
    }
  }, []);

  useEffect(() => {
    if (connection === "offline") {
      const retryTimer = window.setInterval(() => void checkBackend(), 10_000);
      return () => window.clearInterval(retryTimer);
    }
    return undefined;
  }, [connection, checkBackend]);

  useEffect(() => {
    if (connection !== "online" || offlineSyncInFlightRef.current) {
      return;
    }
    const events = loadOfflineEvents();
    setOfflineEventCount(events.length);
    if (events.length === 0) {
      return;
    }
    offlineSyncInFlightRef.current = true;
    void syncOfflineDecisions(events)
      .then((response) => {
        setOfflineEventCount(removeSyncedOfflineEvents(response.accepted_event_ids).length);
      })
      .catch(() => {
        window.setTimeout(() => {
          if (mountedRef.current) {
            void checkBackend();
          }
        }, 10_000);
      })
      .finally(() => {
        offlineSyncInFlightRef.current = false;
      });
  }, [connection, checkBackend]);

  useEffect(() => {
    mountedRef.current = true;
    void checkBackend();
    return () => {
      mountedRef.current = false;
      activeRunRef.current += 1;
      cameraRequestRef.current += 1;
      activeAbortRef.current?.abort();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, [checkBackend]);

  useEffect(() => {
    const stopTracksOnPageHide = () => {
      activeRunRef.current += 1;
      cameraRequestRef.current += 1;
      activeAbortRef.current?.abort();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
    window.addEventListener("pagehide", stopTracksOnPageHide);
    return () => window.removeEventListener("pagehide", stopTracksOnPageHide);
  }, []);

  useEffect(() => {
    if (!stream || !videoRef.current) {
      return;
    }
    const video = videoRef.current;
    video.srcObject = stream;
    void video.play().catch(() => {
      setCameraError("The camera started, but the preview could not play. Check browser permissions and retry.");
    });
    return () => {
      stream.getTracks().forEach((track) => track.stop());
      if (video.srcObject === stream) {
        video.srcObject = null;
      }
    };
  }, [stream]);

  const startCamera = async () => {
    setCameraError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError(
        "This browser does not support camera access. Use a current browser over HTTPS or localhost.",
      );
      return;
    }

    const requestId = cameraRequestRef.current + 1;
    cameraRequestRef.current = requestId;
    setCameraStarting(true);
    try {
      const cameraStream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: "user" },
          width: { ideal: 640 },
          height: { ideal: 480 },
        },
      });
      if (requestId !== cameraRequestRef.current || !mountedRef.current) {
        cameraStream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = cameraStream;
      setStream(cameraStream);
      setResult(null);
    } catch (error) {
      if (requestId === cameraRequestRef.current && mountedRef.current) {
        setCameraError(cameraErrorMessage(error));
      }
    } finally {
      if (requestId === cameraRequestRef.current && mountedRef.current) {
        setCameraStarting(false);
      }
    }
  };

  const stopCamera = () => {
    const cancelledRun = isVerifying;
    activeRunRef.current += 1;
    cameraRequestRef.current += 1;
    activeAbortRef.current?.abort();
    activeAbortRef.current = null;
    setCameraStarting(false);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setStream(null);
    setIsVerifying(false);
    setPhase("idle");
    setCountdown(0);
    setFrameProgress(0);
    if (cancelledRun) {
      setResult({
        status: "ERROR",
        challenge: challenge?.challenge ?? null,
        message: "Camera stopped. Verification was cancelled; start a new attempt.",
      });
    }
  };

  const recordOfflineFallback = () => {
    const event = recordOfflineStepUp();
    if (!event) {
      setResult({
        status: "ERROR",
        challenge: null,
        message: "Offline event storage is unavailable or full. No access was granted; contact authorized campus staff.",
      });
      return;
    }
    setOfflineEventCount(loadOfflineEvents().length);
    setResult({
      status: "STEP_UP_VERIFICATION",
      challenge: null,
      requiresStepUp: true,
      message: "Backend unavailable. No access was granted. Contact authorized campus staff for in-person verification; this event will sync when service returns.",
    });
  };

  const startVerification = async () => {
    if (!userId.trim()) {
      setResult({
        status: "ERROR",
        challenge: null,
        message: "Enter the ID of an already enrolled user before requesting an access decision.",
      });
      return;
    }
    const video = videoRef.current;
    if (!streamRef.current || !video) {
      setResult({
        status: "ERROR",
        challenge: null,
        message: "Start the camera before beginning verification.",
      });
      return;
    }
    if (connection !== "online") {
      setResult({
        status: "ERROR",
        challenge: null,
        message: "Connect to the backend before starting verification.",
      });
      return;
    }

    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    const controller = new AbortController();
    activeAbortRef.current = controller;
    const ensureActive = () => {
      if (activeRunRef.current !== runId || controller.signal.aborted) {
        throw new DOMException("Verification cancelled.", "AbortError");
      }
      if (!streamRef.current?.getVideoTracks().some((track) => track.readyState === "live")) {
        throw new Error("The camera stopped. Restart it and try again.");
      }
    };

    setIsVerifying(true);
    setResult(null);
    setChallenge(null);
    setFrameProgress(0);
    setCountdown(0);

    let issuedChallenge: ChallengeResponse | null = null;
    try {
      issuedChallenge = await createChallenge(controller.signal);
      ensureActive();
      setChallenge(issuedChallenge);
      setPhase("countdown");
      for (let value = 3; value > 0; value -= 1) {
        setCountdown(value);
        await wait(1_000);
        ensureActive();
      }

      if (video.videoWidth === 0 || video.videoHeight === 0) {
        throw new Error("The camera preview is not ready. Restart the camera and try again.");
      }

      setPhase("capturing");
      const frames: string[] = [];
      for (let index = 0; index < FRAME_COUNT; index += 1) {
        ensureActive();
        frames.push(await captureJpegFrame(video));
        setFrameProgress(index + 1);
        if (index < FRAME_COUNT - 1) {
          await wait(FRAME_INTERVAL_MS);
        }
      }

      ensureActive();
      const frameError = validateFrameBatch(frames);
      if (frameError) {
        throw new Error(frameError);
      }

      setPhase("submitting");
      const response = await decideAccess(
        userId.trim(),
        issuedChallenge.challenge_id,
        frames,
        controller.signal,
      );
      ensureActive();
      setResult({
        status: response.decision,
        challenge: issuedChallenge.challenge,
        livenessStatus: response.liveness_status,
        identityStatus: response.identity_status,
        scheduleStatus: response.schedule_status,
        requiresStepUp: response.requires_step_up,
        movementDetected: response.movement_detected,
        validFrameCount: response.valid_frame_count,
        totalFrameCount: response.total_frame_count,
        processingTimeMs: response.processing_time_ms,
        message: response.reason,
        identityMessage: response.identity_message,
      });
      setPhase("idle");
    } catch (error) {
      if (activeRunRef.current !== runId || controller.signal.aborted) {
        return;
      }
      setResult({
        status: "ERROR",
        challenge: issuedChallenge?.challenge ?? null,
        message: verificationErrorMessage(error),
      });
      setPhase("idle");
    } finally {
      if (activeRunRef.current === runId) {
        setIsVerifying(false);
        activeAbortRef.current = null;
        setCountdown(0);
      }
    }
  };

  const screenDirection = screenDirectionForChallenge(challenge?.challenge ?? null);
  const displayedChallenge = screenChallengeLabel(challenge?.challenge ?? null);
  const instruction = screenDirection
    ? `Move your face toward the ${screenDirection} side of the screen`
    : "Your movement challenge will appear here";
  const secureContext = window.isSecureContext;
  const resultTone =
    result?.status === "ALLOW"
      ? "positive"
      : result?.status === "ERROR" || result?.status === "DENY"
        ? "error"
        : result
          ? "caution"
          : "";

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="HID liveness demo home">
          <span className="brand-mark">H</span>
          <span className="brand-name">HID <span>LAB</span></span>
        </a>
        <div className={`connection-pill ${connection}`} role="status">
          <span className="connection-dot" />
          <span>{connection === "online" ? "Backend connected" : connection === "checking" ? "Checking backend" : "Backend offline"}</span>
          <button
            className="text-button connection-retry"
            type="button"
            onClick={() => void checkBackend()}
            disabled={connection === "checking"}
          >
            {connection === "checking" ? "…" : "Retry"}
          </button>
        </div>
      </header>

      <section className="intro">
        <div>
          <p className="eyebrow"><span className="eyebrow-line" /> PROTOTYPE · 01</p>
          <h1>Movement <span>check.</span></h1>
          <p className="intro-copy">
            Complete a randomized movement prompt. The backend independently checks liveness,
            enrolled identity, and the configured campus schedule before returning a decision.
          </p>
        </div>
        <div className="intro-meta">
          <span className="meta-label">API STATUS</span>
          <span className={`meta-value ${connection}`}>{connection === "online" ? "OPERATIONAL" : connection === "checking" ? "CONNECTING" : "UNAVAILABLE"}</span>
          <span className="meta-detail" title={configuredApiUrl}>{healthMessage}</span>
        </div>
      </section>

      <section className="workspace" aria-label="Camera liveness verification">
        <div className="camera-column">
          <div className="section-heading">
            <div>
              <span className="step-index">01</span>
              <h2>Camera preview</h2>
            </div>
            <span className={`camera-state ${stream ? "active" : ""}`}>
              <span className="state-dot" /> {stream ? "CAMERA ON" : "CAMERA OFF"}
            </span>
          </div>

          <div className={`camera-frame ${stream ? "is-live" : ""}`}>
            <video
              ref={videoRef}
              className="camera-video"
              autoPlay
              muted
              playsInline
              aria-label="Live camera preview"
            />
            {!stream && (
              <div className="camera-placeholder">
                <div className="camera-icon" aria-hidden="true">
                  <span />
                </div>
                <p>Camera preview appears here</p>
                <span>Permission is requested only when you start the camera.</span>
              </div>
            )}
            {stream && <div className="camera-guide" aria-hidden="true"><span /></div>}
            <span className="frame-corner corner-tl" />
            <span className="frame-corner corner-tr" />
            <span className="frame-corner corner-bl" />
            <span className="frame-corner corner-br" />
            {isVerifying && (
              <div className="capture-overlay" aria-live="polite">
                {phase === "countdown" && <span>Get ready · {countdown}</span>}
                {phase === "capturing" && <span>Capturing · {frameProgress}/{FRAME_COUNT}</span>}
                {phase === "submitting" && <span>Checking liveness, identity & schedule…</span>}
              </div>
            )}
          </div>

          {cameraError && <p className="inline-error" role="alert">{cameraError}</p>}
          <div className="camera-controls">
            {!stream ? (
              <button
                className="button button-primary"
                type="button"
                onClick={() => void startCamera()}
                disabled={cameraStarting}
              >
                <span className="button-icon">◎</span>
                {cameraStarting ? "Requesting permission…" : "Start camera"}
              </button>
            ) : (
              <button className="button button-secondary" type="button" onClick={stopCamera}>
                {isVerifying ? "Cancel and stop camera" : "Stop camera"}
              </button>
            )}
            <span className="camera-hint">Front camera preferred on mobile</span>
          </div>
          {!secureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1" && (
            <p className="secure-note">
              Mobile camera access requires HTTPS. Plain HTTP is permitted only on localhost on the same device.
            </p>
          )}
        </div>

        <div className="verification-column">
          <div className="section-heading">
            <div>
              <span className="step-index">02</span>
              <h2>Challenge response</h2>
            </div>
            <span className="one-time-tag">ONE-TIME PROMPT</span>
          </div>

          <div className="challenge-card">
            <div className="challenge-card-top">
              <span className="challenge-label">YOUR INSTRUCTION</span>
              <span className="challenge-expiry">
                {challenge ? `${displayedChallenge} · ${challenge.expires_in_seconds}s` : "Randomized"}
              </span>
            </div>
            <div className={`challenge-arrow ${challenge?.challenge === "MOVE_LEFT" ? "right" : ""}`} aria-hidden="true">
              <span />
            </div>
            <h3>{instruction}</h3>
            <p>Keep your face in view and move naturally. Hold still when the capture completes.</p>
            <div className="capture-progress" aria-label={`${frameProgress} of ${FRAME_COUNT} frames captured`}>
              {Array.from({ length: FRAME_COUNT }, (_, index) => (
                <span key={index} className={index < frameProgress ? "filled" : ""} />
              ))}
            </div>
            <div className="progress-caption">
              <span>{phase === "countdown" ? `Starting in ${countdown}…` : phase === "capturing" ? "CAPTURING FRAMES" : phase === "submitting" ? "SENDING TO BACKEND" : "CAPTURE PROGRESS"}</span>
              <span>{frameProgress.toString().padStart(2, "0")} / {FRAME_COUNT}</span>
            </div>
          </div>

          <label className="user-id-field">
            <span>ENROLLED USER ID</span>
            <input
              type="text"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder="Enter an enrolled user ID"
              autoComplete="off"
              disabled={isVerifying}
            />
          </label>
          <p className="enrollment-note">
            Enrollment is performed separately through the backend API. This screen does not register users.
          </p>

          <div className="verification-actions">
            <button
              className="button button-primary verify-button"
              type="button"
              onClick={() => void startVerification()}
              disabled={!stream || connection !== "online" || isVerifying || !userId.trim()}
            >
              {isVerifying ? "Verification in progress…" : result ? "Try again" : "Start verification"}
              {!isVerifying && <span className="button-arrow">↗</span>}
            </button>
            <p className="action-note">
              {!stream
                ? "Start your camera to enable verification."
                : connection !== "online"
                  ? "Connect to the backend to continue."
                  : !userId.trim()
                    ? "Enter an ID that has already been enrolled."
                  : "A new prompt is generated for every attempt."}
            </p>
            {connection === "offline" && (
              <button
                className="button button-secondary"
                type="button"
                onClick={recordOfflineFallback}
              >
                Record offline step-up
              </button>
            )}
            {offlineEventCount > 0 && (
              <p className="action-note" role="status">
                {offlineEventCount} minimal offline event{offlineEventCount === 1 ? "" : "s"} waiting to sync.
              </p>
            )}
          </div>
        </div>
      </section>

      <section className="result-section" aria-live="polite">
        <div className="result-heading">
          <div>
            <span className="step-index">03</span>
            <h2>Access decision</h2>
          </div>
          <span className="result-caption">BACKEND-CALCULATED · NO DOOR CONTROL</span>
        </div>

        {result ? (
          <div className={`result-card ${resultTone}`}>
            <div className="result-status">
              <span className="result-status-mark">{result.status === "ALLOW" ? "✓" : result.status === "ERROR" || result.status === "DENY" ? "!" : "·"}</span>
              <div>
                <span className="result-overline">ACCESS DECISION</span>
                <strong>{statusLabel(result.status)}</strong>
              </div>
            </div>
            <div className="result-details">
              <div>
                <span>CHALLENGE</span>
                <strong>{screenChallengeLabel(result.challenge) ?? "—"}</strong>
              </div>
              <div>
                <span>LIVENESS</span>
                <strong>{result.livenessStatus ?? "—"}</strong>
              </div>
              <div>
                <span>IDENTITY</span>
                <strong>{result.identityStatus ?? "—"}</strong>
              </div>
              <div>
                <span>CAMPUS SCHEDULE</span>
                <strong>{result.scheduleStatus ?? "—"}</strong>
              </div>
              <div>
                <span>STEP-UP REQUIRED</span>
                <strong>{result.requiresStepUp === undefined ? "—" : result.requiresStepUp ? "YES" : "NO"}</strong>
              </div>
              <div>
                <span>MOVEMENT EVIDENCE</span>
                <strong>{result.movementDetected === undefined ? "—" : result.movementDetected ? "DETECTED" : "NOT DETECTED"}</strong>
              </div>
              <div>
                <span>VALID FRAMES</span>
                <strong>{result.validFrameCount === undefined ? "—" : `${result.validFrameCount} / ${result.totalFrameCount}`}</strong>
              </div>
              <div>
                <span>BACKEND TIME</span>
                <strong>{result.processingTimeMs === undefined ? "—" : `${result.processingTimeMs.toFixed(1)} ms`}</strong>
              </div>
            </div>
            <p className="result-message">{result.message}</p>
            {result.identityMessage && (
              <p className="result-message">Identity check: {result.identityMessage}</p>
            )}
          </div>
        ) : (
          <div className="result-empty">
            <span className="empty-mark">—</span>
            <span>Your result will appear here after the challenge.</span>
          </div>
        )}
      </section>

      <footer className="disclaimer">
        <span className="disclaimer-mark">i</span>
        <p><strong>Experimental access-decision prototype.</strong> Movement heuristics may be replayed, and the demonstration schedule is not verified campus policy. Step-up requests are not access grants. No physical door controller is connected.</p>
        <span className="footer-version">HID · MVP</span>
      </footer>
    </main>
  );
}

export default App;
