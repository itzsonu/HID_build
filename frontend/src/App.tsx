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
import BatchEnrollment from "./components/BatchEnrollment";
import {
  enrollmentForAttempt,
  waitForEnrollment,
  type EnrollmentOperation,
} from "./enrollmentCoordination";
import {
  loadOfflineEvents,
  recordOfflineStepUp,
  removeSyncedOfflineEvents,
} from "./offlineQueue";

type ConnectionState = "checking" | "online" | "offline";
type VerificationPhase = "idle" | "countdown" | "capturing" | "waiting-enrollment" | "submitting";
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
  captureDurationMs?: number;
  responseTimeMs?: number;
  message: string;
  identityMessage?: string;
  matchedUserId?: string | null;
}

const FRAME_COUNT = 10;
const FRAME_INTERVAL_MS = 180;

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
  const enrollmentGenerationRef = useRef(0);
  const enrollmentRequestRef = useRef<EnrollmentOperation & {
    resolve: (registered: boolean) => void;
    settled: boolean;
  } | null>(null);

  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [healthMessage, setHealthMessage] = useState("Checking backend...");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [cameraStarting, setCameraStarting] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [phase, setPhase] = useState<VerificationPhase>("idle");
  const [isVerifying, setIsVerifying] = useState(false);
  const [challenge, setChallenge] = useState<ChallengeResponse | null>(null);
  const [frameProgress, setFrameProgress] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [result, setResult] = useState<DisplayResult | null>(null);
  const [offlineEventCount, setOfflineEventCount] = useState(
    () => loadOfflineEvents().length,
  );

  const handleEnrollmentStarted = useCallback(() => {
    if (enrollmentRequestRef.current && !enrollmentRequestRef.current.settled) {
      return;
    }
    let resolveEnrollment!: (registered: boolean) => void;
    const promise = new Promise<boolean>((resolve) => {
      resolveEnrollment = resolve;
    });
    enrollmentGenerationRef.current += 1;
    enrollmentRequestRef.current = {
      promise,
      resolve: resolveEnrollment,
      generation: enrollmentGenerationRef.current,
      settled: false,
    };
  }, []);

  const handleEnrollmentFinished = useCallback((registered: boolean) => {
    const pendingEnrollment = enrollmentRequestRef.current;
    if (!pendingEnrollment || pendingEnrollment.settled) {
      return;
    }
    pendingEnrollment.settled = true;
    pendingEnrollment.resolve(registered);
  }, []);

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
    const enrollmentGenerationAtStart = enrollmentGenerationRef.current;
    const enrollmentAtStart =
      enrollmentRequestRef.current && !enrollmentRequestRef.current.settled
        ? enrollmentRequestRef.current
        : null;
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
    let captureDurationMs: number | undefined;
    try {
      const captureStartedAt = performance.now();
      issuedChallenge = await createChallenge(controller.signal);
      ensureActive();
      setChallenge(issuedChallenge);
      setPhase("countdown");
      for (let value = 2; value > 0; value -= 1) {
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
      captureDurationMs = performance.now() - captureStartedAt;
      const frameError = validateFrameBatch(frames);
      if (frameError) {
        throw new Error(frameError);
      }

      const pendingEnrollment = enrollmentForAttempt(
        enrollmentGenerationAtStart,
        enrollmentAtStart,
        enrollmentRequestRef.current,
      );
      if (pendingEnrollment) {
        setPhase("waiting-enrollment");
        const enrollmentSucceeded = await waitForEnrollment(
          pendingEnrollment.promise,
          controller.signal,
        );
        ensureActive();
        if (!enrollmentSucceeded) {
          throw new Error("Reference-photo enrollment did not complete. No access decision was submitted.");
        }
      }

      setPhase("submitting");
      const responseStartedAt = performance.now();
      const response = await decideAccess(
        issuedChallenge.challenge_id,
        frames,
        controller.signal,
      );
      const responseTimeMs = performance.now() - responseStartedAt;
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
        captureDurationMs,
        responseTimeMs,
        message: response.reason,
        identityMessage: response.identity_message,
        matchedUserId: response.matched_user_id,
      });
      setPhase("idle");
    } catch (error) {
      if (activeRunRef.current !== runId || controller.signal.aborted) {
        return;
      }
      setResult({
        status: "ERROR",
        challenge: issuedChallenge?.challenge ?? null,
        captureDurationMs,
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

  const flowLabel = phase === "countdown"
    ? `Get ready · ${countdown}s`
    : phase === "capturing"
      ? `Capturing frames · ${frameProgress}/${FRAME_COUNT}`
      : phase === "waiting-enrollment"
        ? "Waiting for reference enrollment…"
      : phase === "submitting"
        ? "Checking with backend…"
        : result
          ? "Attempt complete"
          : "Ready to verify";

  return (
    <main className="app-shell stitch-shell">
      <header className="stitch-header">
        <a className="stitch-brand" href="/" aria-label="HID Lab home">
          <span className="stitch-brand-mark" aria-hidden="true">H</span>
          <span>
            <span className="stitch-brand-name">HID LAB</span>
            <span className="stitch-brand-caption">CAMPUS ACCESS VERIFICATION</span>
          </span>
        </a>
        <div className="stitch-header-actions">
          {offlineEventCount > 0 && (
            <span className="stitch-queue" role="status">
              {offlineEventCount} offline event{offlineEventCount === 1 ? "" : "s"} waiting
            </span>
          )}
          <div className={`stitch-connection ${connection}`} role="status">
            <span className="stitch-status-dot" />
            {connection === "online" ? "Backend connected" : connection === "checking" ? "Checking backend" : "Backend offline"}
          </div>
          <button
            className="stitch-retry"
            type="button"
            onClick={() => void checkBackend()}
            disabled={connection === "checking"}
          >
            {connection === "checking" ? "Checking…" : "Retry"}
          </button>
        </div>
      </header>

      <section className="stitch-intro">
        <div className="stitch-intro-copy">
          <div className="stitch-kicker"><span /> PROTOTYPE · 01</div>
          <div className="stitch-title-row">
            <h1>Movement check<span>.</span></h1>
            <span className="stitch-prototype-tag">KIOSK DEMO</span>
          </div>
          <p>
            Follow the one-time movement prompt. The backend checks the submitted frames,
            matches the current face against enrolled templates, and checks the configured
            campus schedule before returning a decision.
          </p>
        </div>
      </section>

      <BatchEnrollment
        backendOnline={connection === "online"}
        accessStream={stream}
        onEnrollmentStarted={handleEnrollmentStarted}
        onEnrollmentFinished={handleEnrollmentFinished}
      />

      <section className="stitch-workspace" aria-label="Camera liveness verification">
        <section className="stitch-card stitch-camera-card">
          <div className="stitch-card-heading stitch-camera-heading">
            <div className="stitch-heading-title">
              <span className="stitch-step">01</span>
              <div>
                <h2>Camera feed</h2>
                <span className={`stitch-camera-state ${stream ? "active" : ""}`}>
                  <span className="stitch-status-dot" /> {stream ? "CAMERA ON" : "CAMERA OFF"}
                </span>
              </div>
            </div>
            <div className="stitch-camera-actions">
              {!stream ? (
                <button
                  className="stitch-button stitch-button-primary"
                  type="button"
                  onClick={() => void startCamera()}
                  disabled={cameraStarting}
                >
                  {cameraStarting ? "Requesting…" : "Start camera"}
                </button>
              ) : (
                <button className="stitch-button stitch-button-secondary" type="button" onClick={stopCamera}>
                  {isVerifying ? "Cancel" : "Stop camera"}
                </button>
              )}
            </div>
          </div>

          <div className={`stitch-camera-view ${stream ? "is-active" : ""}`}>
            <video ref={videoRef} className="stitch-video" autoPlay muted playsInline aria-label="Live camera preview" />
            {!stream && (
              <div className="stitch-standby">
                <span className="stitch-camera-icon" aria-hidden="true"><i /></span>
                <strong>Camera is inactive</strong>
                <span>Start the camera to show the live preview.</span>
              </div>
            )}
            {stream && (
              <div className="stitch-face-guide" aria-hidden="true">
                <span className="stitch-guide-label">POSITION FACE HERE</span>
                <i />
              </div>
            )}
            <span className="stitch-corner top-left" />
            <span className="stitch-corner top-right" />
            <span className="stitch-corner bottom-left" />
            <span className="stitch-corner bottom-right" />
            {isVerifying && (
              <div className="stitch-capture-status" aria-live="polite">
                <span className={phase === "submitting" ? "stitch-spinner" : "stitch-status-dot"} />
                {flowLabel}
              </div>
            )}
            {cameraError && <div className="stitch-camera-error" role="alert">{cameraError}</div>}
          </div>

          <div className="stitch-camera-footer">
            <span>Mobile camera requires HTTPS. Keep your face in view during capture.</span>
            <span className="stitch-mirror-note">MIRRORED PREVIEW</span>
          </div>
          {!secureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1" && (
            <p className="stitch-secure-note">Open this page over HTTPS to use a phone camera.</p>
          )}
        </section>

        <div className="stitch-side-column">
          <section className="stitch-card stitch-challenge-card">
            <div className="stitch-card-heading">
              <div className="stitch-heading-title">
                <span className="stitch-step">02</span>
                <div><h2>Challenge response</h2><span className="stitch-subheading">ONE-TIME PROMPT</span></div>
              </div>
              <span className="stitch-challenge-badge">{displayedChallenge ?? "RANDOMIZED"}</span>
            </div>

            <div className="stitch-prompt-label">YOUR INSTRUCTION <span>{challenge ? `${challenge.expires_in_seconds}s` : "READY"}</span></div>
            <div className="stitch-prompt-box">
              <span className={`stitch-direction-icon ${screenDirection === "left" ? "left" : "right"}`} aria-hidden="true">➜</span>
              <h3>{instruction}</h3>
            </div>
            <p className="stitch-prompt-help">Keep your face in view and move naturally. The preview is mirrored, so follow the screen-side instruction.</p>

            <div className="stitch-progress-heading">
              <span>{flowLabel}</span>
              <span>{frameProgress.toString().padStart(2, "0")} / {FRAME_COUNT}</span>
            </div>
            <div className="stitch-progress-track" aria-label={`${frameProgress} of ${FRAME_COUNT} frames captured`}>
              {Array.from({ length: FRAME_COUNT }, (_, index) => (
                <span key={index} className={index < frameProgress ? "filled" : ""} />
              ))}
            </div>

            <button
              className="stitch-button stitch-button-primary stitch-verify-button"
              type="button"
              onClick={() => void startVerification()}
              disabled={!stream || connection !== "online" || isVerifying}
            >
              {isVerifying ? "Verification in progress…" : result ? "Try again" : "Start verification"}
              {!isVerifying && <span aria-hidden="true">↗</span>}
            </button>
            <p className="stitch-action-note">
              {!stream
                ? "Start your camera to continue."
                : connection !== "online"
                  ? "Connect to the backend before verifying."
                  : "A new movement prompt is generated for each attempt; the backend searches enrolled templates."}
            </p>
          </section>

          {connection === "offline" && (
            <section className="stitch-offline-card">
              <div><span className="stitch-offline-dot" /><strong>Offline fallback</strong></div>
              <p>Recording this event does not grant access. Contact authorized campus staff for verification.</p>
              <button className="stitch-button stitch-button-secondary" type="button" onClick={recordOfflineFallback}>
                Record offline step-up
              </button>
            </section>
          )}
          {connection !== "offline" && offlineEventCount > 0 && (
            <p className="stitch-sync-note" role="status">{offlineEventCount} offline event{offlineEventCount === 1 ? "" : "s"} waiting to sync.</p>
          )}
        </div>
      </section>

      <section className="stitch-results" aria-live="polite">
        <div className="stitch-results-heading">
          <div className="stitch-heading-title"><span className="stitch-step">03</span><h2>Access decision</h2></div>
          <span>BACKEND-CALCULATED · NO DOOR CONTROL</span>
        </div>
        {result ? (
          <div className={`stitch-result-card ${resultTone}`}>
            <div className="stitch-result-topline">
              <span className="stitch-result-mark">{result.status === "ALLOW" ? "✓" : result.status === "ERROR" || result.status === "DENY" ? "!" : "·"}</span>
              <div><span>ACCESS DECISION</span><strong>{statusLabel(result.status)}</strong></div>
            </div>
            <div className="stitch-result-metrics">
              <div><span>CHALLENGE</span><strong>{screenChallengeLabel(result.challenge) ?? "—"}</strong></div>
              <div><span>LIVENESS</span><strong>{result.livenessStatus ?? "—"}</strong></div>
              <div><span>IDENTITY</span><strong>{result.identityStatus ?? "—"}</strong></div>
              <div><span>MATCHED USER</span><strong>{result.matchedUserId ?? "—"}</strong></div>
              <div><span>CAMPUS SCHEDULE</span><strong>{result.scheduleStatus ?? "—"}</strong></div>
              <div><span>STEP-UP REQUIRED</span><strong>{result.requiresStepUp === undefined ? "—" : result.requiresStepUp ? "YES" : "NO"}</strong></div>
              <div><span>MOVEMENT EVIDENCE</span><strong>{result.movementDetected === undefined ? "—" : result.movementDetected ? "DETECTED" : "NOT DETECTED"}</strong></div>
              <div><span>VALID FRAMES</span><strong>{result.validFrameCount === undefined ? "—" : `${result.validFrameCount} / ${result.totalFrameCount}`}</strong></div>
              <div><span>BACKEND TIME</span><strong>{result.processingTimeMs === undefined ? "—" : `${result.processingTimeMs.toFixed(1)} ms`}</strong></div>
              <div>
                <span>CAPTURE · CHALLENGE TO FINAL FRAME</span>
                <strong>{result.captureDurationMs === undefined ? "—" : `${result.captureDurationMs.toFixed(1)} ms`}</strong>
              </div>
              <div>
                <span>API ROUND-TRIP · &lt;1.5s TARGET</span>
                <strong className={result.responseTimeMs === undefined ? "" : result.responseTimeMs < 1_500 ? "within-target" : "over-target"}>
                  {result.responseTimeMs === undefined ? "—" : `${result.responseTimeMs.toFixed(1)} ms`}
                </strong>
              </div>
            </div>
            <div className="stitch-result-messages">
              <p>{result.message}</p>
              {result.identityMessage && <p>Identity check: {result.identityMessage}</p>}
              {result.responseTimeMs !== undefined && (
                <p>API round-trip is measured in this browser from sending the captured frames until the backend decision response arrives. Capture duration includes the challenge request and countdown through the final frame.</p>
              )}
            </div>
          </div>
        ) : (
          <div className="stitch-result-empty"><span>—</span><p>Your backend-calculated result will appear here after verification.</p></div>
        )}
      </section>

      <footer className="stitch-disclaimer">
        <span aria-hidden="true">i</span>
        <p><strong>Experimental prototype.</strong> Movement alone cannot distinguish a live person from a video replay. Step-up is not an access grant. No physical door is connected.</p>
        <span className="stitch-footer-mark">HID · DEMO</span>
      </footer>
    </main>
  );
}

export default App;
