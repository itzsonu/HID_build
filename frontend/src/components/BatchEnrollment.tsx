import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import {
  ApiError,
  deleteAdminEnrollment,
  getAdminEnrollments,
  registerFacesBatch,
  type BatchFaceRegistrationResponse,
  type EnrollmentSummary,
  type EnrollmentPhotoStatus,
} from "../services/api";
import {
  availableEnrollmentPhotoSlots,
  captureEnrollmentPhoto,
  MAX_ENROLLMENT_PHOTOS,
  prepareEnrollmentPhoto,
  stopOwnedCameraStream,
} from "../enrollmentPhoto";

type PhotoStage =
  | "preparing"
  | "ready"
  | "submitting"
  | EnrollmentPhotoStatus
  | "error";

interface EnrollmentPhoto {
  id: string;
  name: string;
  previewUrl: string;
  base64: string;
  stage: PhotoStage;
  message: string;
}

interface BatchEnrollmentProps {
  backendOnline: boolean;
  accessStream: MediaStream | null;
  onEnrollmentStarted?: () => void;
  onEnrollmentFinished?: (registered: boolean) => void;
}

function cameraErrorMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera permission was denied. Allow camera access, then try again.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No camera was found. Connect a camera or use a device with a camera.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The camera is already in use or unavailable. Close other camera apps and retry.";
  }
  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    return "Camera access on phones requires HTTPS. Open this page over HTTPS and try again.";
  }
  return error instanceof Error ? error.message : "The enrollment camera could not be started.";
}

function statusLabel(stage: PhotoStage): string {
  switch (stage) {
    case "preparing":
      return "Preparing JPEG";
    case "ready":
      return "Ready to submit";
    case "submitting":
      return "Submitted";
    case "ACCEPTED":
      return "Accepted";
    case "REJECTED":
      return "Rejected";
    case "DUPLICATE":
      return "Duplicate";
    case "error":
      return "Request failed";
  }
}

function statusClass(stage: PhotoStage): string {
  if (stage === "ACCEPTED" || stage === "ready") {
    return "accepted";
  }
  if (stage === "REJECTED" || stage === "error") {
    return "rejected";
  }
  if (stage === "DUPLICATE") {
    return "duplicate";
  }
  return "pending";
}

export default function BatchEnrollment({
  backendOnline,
  accessStream,
  onEnrollmentStarted,
  onEnrollmentFinished,
}: BatchEnrollmentProps) {
  const [userId, setUserId] = useState("");
  const [adminSecret, setAdminSecret] = useState("");
  const [enrollments, setEnrollments] = useState<EnrollmentSummary[]>([]);
  const [adminStatus, setAdminStatus] = useState("");
  const [adminError, setAdminError] = useState("");
  const [isLoadingEnrollments, setIsLoadingEnrollments] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState("");
  const [consent, setConsent] = useState(false);
  const [photos, setPhotos] = useState<EnrollmentPhoto[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [response, setResponse] = useState<BatchFaceRegistrationResponse | null>(null);
  const photoIdRef = useRef(0);
  const previewUrlsRef = useRef(new Set<string>());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const captureVideoRef = useRef<HTMLVideoElement>(null);
  const captureRequestRef = useRef(0);
  const photoCaptureRequestRef = useRef(0);
  const ownedCameraStreamRef = useRef<MediaStream | null>(null);
  const capturePreviewUrlRef = useRef<string | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [captureStream, setCaptureStream] = useState<MediaStream | null>(null);
  const [captureStarting, setCaptureStarting] = useState(false);
  const [capturingPhoto, setCapturingPhoto] = useState(false);
  const [captureVideoReady, setCaptureVideoReady] = useState(false);
  const [captureError, setCaptureError] = useState("");
  const [capturedPhoto, setCapturedPhoto] = useState<File | null>(null);
  const [capturePreviewUrl, setCapturePreviewUrl] = useState("");

  useEffect(() => () => {
    captureRequestRef.current += 1;
    photoCaptureRequestRef.current += 1;
    previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewUrlsRef.current.clear();
    if (capturePreviewUrlRef.current) {
      URL.revokeObjectURL(capturePreviewUrlRef.current);
    }
    stopOwnedCameraStream(ownedCameraStreamRef.current, null);
    ownedCameraStreamRef.current = null;
  }, []);

  useEffect(() => {
    const video = captureVideoRef.current;
    if (!captureOpen || !captureStream || !video) {
      return;
    }
    setCaptureVideoReady(false);
    video.srcObject = captureStream;
    void video.play().catch(() => {
      setCaptureError("The camera started, but its preview could not play. Check browser permissions.");
    });
    const videoTrack = captureStream.getVideoTracks()[0];
    const onTrackEnded = () => {
      setCaptureVideoReady(false);
      setCaptureError("The enrollment camera stopped. Restart it and try again.");
    };
    videoTrack?.addEventListener("ended", onTrackEnded);
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      setCaptureVideoReady(true);
    }
    return () => {
      videoTrack?.removeEventListener("ended", onTrackEnded);
      if (video.srcObject === captureStream) {
        video.srcObject = null;
      }
    };
  }, [captureOpen, captureStream]);

  const releaseCapturedPreview = () => {
    if (capturePreviewUrlRef.current) {
      URL.revokeObjectURL(capturePreviewUrlRef.current);
      capturePreviewUrlRef.current = null;
    }
    setCapturePreviewUrl("");
    setCapturedPhoto(null);
  };

  const closeCameraCapture = () => {
    captureRequestRef.current += 1;
    photoCaptureRequestRef.current += 1;
    stopOwnedCameraStream(ownedCameraStreamRef.current, accessStream);
    ownedCameraStreamRef.current = null;
    setCaptureStream(null);
    setCaptureStarting(false);
    setCapturingPhoto(false);
    setCaptureVideoReady(false);
    setCaptureOpen(false);
    releaseCapturedPreview();
  };

  const openCameraCapture = async () => {
    setCaptureError("");
    releaseCapturedPreview();
    setCaptureOpen(true);
    const activeAccessStream = accessStream?.getVideoTracks().some(
      (track) => track.readyState === "live",
    ) ? accessStream : null;
    if (activeAccessStream) {
      setCaptureStream(activeAccessStream);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setCaptureError("This browser does not support camera access. Use a current browser over HTTPS or localhost.");
      return;
    }

    const requestId = captureRequestRef.current + 1;
    captureRequestRef.current = requestId;
    setCaptureStarting(true);
    try {
      const cameraStream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: "user" },
          width: { ideal: 640 },
          height: { ideal: 480 },
        },
      });
      if (requestId !== captureRequestRef.current) {
        stopOwnedCameraStream(cameraStream, accessStream);
        return;
      }
      ownedCameraStreamRef.current = cameraStream;
      setCaptureStream(cameraStream);
    } catch (error) {
      if (requestId === captureRequestRef.current) {
        setCaptureError(cameraErrorMessage(error));
      }
    } finally {
      if (requestId === captureRequestRef.current) {
        setCaptureStarting(false);
      }
    }
  };

  const takePhoto = async () => {
    const video = captureVideoRef.current;
    if (!video) {
      setCaptureError("The enrollment camera preview is unavailable.");
      return;
    }
    const requestId = photoCaptureRequestRef.current + 1;
    photoCaptureRequestRef.current = requestId;
    setCapturingPhoto(true);
    setCaptureError("");
    try {
      const photo = await captureEnrollmentPhoto(video);
      if (requestId !== photoCaptureRequestRef.current) {
        return;
      }
      const previewUrl = URL.createObjectURL(photo);
      if (capturePreviewUrlRef.current) {
        URL.revokeObjectURL(capturePreviewUrlRef.current);
      }
      capturePreviewUrlRef.current = previewUrl;
      setCapturedPhoto(photo);
      setCapturePreviewUrl(previewUrl);
    } catch (error) {
      if (requestId === photoCaptureRequestRef.current) {
        setCaptureError(error instanceof Error ? error.message : "The captured photo could not be prepared.");
      }
    } finally {
      if (requestId === photoCaptureRequestRef.current) {
        setCapturingPhoto(false);
      }
    }
  };

  const addFiles = async (selectedFiles: File[]) => {
    if (selectedFiles.length === 0) {
      return;
    }

    const availableSlots = availableEnrollmentPhotoSlots(photos.length);
    if (selectedFiles.length > availableSlots) {
      setNotice(`You can select up to ${MAX_ENROLLMENT_PHOTOS} photos in one enrollment.`);
    } else {
      setNotice("");
    }

    const filesToAdd = selectedFiles.slice(0, availableSlots);
    const prepared: EnrollmentPhoto[] = filesToAdd.map((file) => {
      const id = `reference-${photoIdRef.current++}`;
      const previewUrl = URL.createObjectURL(file);
      previewUrlsRef.current.add(previewUrl);
      return {
        id,
        name: file.name,
        previewUrl,
        base64: "",
        stage: "preparing",
        message: "Preparing a local JPEG for backend validation.",
      };
    });
    setPhotos((current) => [...current, ...prepared]);
    setResponse(null);

    await Promise.all(prepared.map(async (photo, index) => {
      try {
        const base64 = await prepareEnrollmentPhoto(filesToAdd[index]);
        setPhotos((current) => current.map((item) => item.id === photo.id
          ? { ...item, base64, stage: "ready", message: "Photo is ready; face and quality checks run on the backend." }
          : item));
      } catch (error) {
        const message = error instanceof Error ? error.message : "The photo could not be prepared.";
        setPhotos((current) => current.map((item) => item.id === photo.id
          ? { ...item, stage: "REJECTED", message }
          : item));
      }
    }));
  };

  const addPhotos = async (event: ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    await addFiles(selectedFiles);
  };

  const useCapturedPhoto = async () => {
    if (!capturedPhoto) {
      return;
    }
    const photo = capturedPhoto;
    await addFiles([photo]);
    closeCameraCapture();
  };

  const removePhoto = (photo: EnrollmentPhoto) => {
    URL.revokeObjectURL(photo.previewUrl);
    previewUrlsRef.current.delete(photo.previewUrl);
    setPhotos((current) => current.filter((item) => item.id !== photo.id));
    setResponse(null);
    setNotice("");
  };

  const loadEnrollments = async (secret: string = adminSecret) => {
    if (!secret.trim()) {
      setAdminError("Enter the configured admin secret to manage enrollments.");
      return;
    }
    setIsLoadingEnrollments(true);
    setAdminError("");
    setAdminStatus("");
    try {
      const result = await getAdminEnrollments(secret.trim());
      setEnrollments(result.enrollments);
      setAdminStatus(
        `Loaded ${result.enrollments.length} enrolled ${result.enrollments.length === 1 ? "user" : "users"}.`,
      );
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "Enrollment records could not be loaded.");
      setEnrollments([]);
    } finally {
      setIsLoadingEnrollments(false);
    }
  };

  const deleteEnrollment = async (targetUserId: string) => {
    if (!adminSecret.trim()) {
      setAdminError("Enter the configured admin secret to delete an enrollment.");
      return;
    }
    if (!window.confirm(`Delete all face templates for ${targetUserId}? This cannot be undone.`)) {
      return;
    }
    setDeletingUserId(targetUserId);
    setAdminError("");
    setAdminStatus("");
    try {
      await deleteAdminEnrollment(targetUserId, adminSecret.trim());
      setEnrollments((current) => current.filter((entry) => entry.user_id !== targetUserId));
      setAdminStatus(`Deleted face templates for ${targetUserId}.`);
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "The enrollment could not be deleted.");
    } finally {
      setDeletingUserId("");
    }
  };

  const submitEnrollment = async () => {
    const readyPhotos = photos.filter(
      (photo) =>
        (photo.stage === "ready" || photo.stage === "error" || photo.stage === "ACCEPTED") &&
        photo.base64,
    );
    if (!userId.trim()) {
      setNotice("Enter a user ID before starting enrollment.");
      return;
    }
    if (!consent) {
      setNotice("Confirm consent before sending biometric photos.");
      return;
    }
    if (!adminSecret.trim()) {
      setNotice("Enter the configured admin secret before enrolling a user.");
      return;
    }
    if (readyPhotos.length < 2) {
      setNotice("At least two locally valid photos are needed. Backend face checks may reject additional photos.");
      return;
    }
    if (!backendOnline) {
      setNotice("The backend is offline. Enrollment cannot be completed locally.");
      return;
    }

    setIsSubmitting(true);
    onEnrollmentStarted?.();
    setNotice("");
    setResponse(null);
    setPhotos((current) => current.map((photo) => readyPhotos.some((ready) => ready.id === photo.id)
      ? { ...photo, stage: "submitting", message: "Sent in the batch; awaiting backend validation." }
      : photo));

    let registered = false;
    try {
      const result = await registerFacesBatch(
        userId.trim(),
        readyPhotos.map((photo) => photo.base64),
        true,
        adminSecret.trim(),
      );
      registered = result.registered;
      setResponse(result);
      const resultsByIndex = new Map(result.photo_results.map((item) => [item.photo_index, item]));
      readyPhotos.forEach((photo) => {
        URL.revokeObjectURL(photo.previewUrl);
        previewUrlsRef.current.delete(photo.previewUrl);
      });
      setPhotos((current) => current.map((photo) => {
        const submittedIndex = readyPhotos.findIndex((submitted) => submitted.id === photo.id);
        const photoResult = submittedIndex >= 0 ? resultsByIndex.get(submittedIndex + 1) : undefined;
        if (!photoResult) {
          return photo;
        }
        return {
          ...photo,
          base64: "",
          previewUrl: "",
          stage: photoResult.status,
          message: result.registered
            ? photoResult.message
            : photoResult.status === "ACCEPTED"
              ? "Backend accepted this photo but did not complete enrollment. Reselect it with additional distinct references to retry."
              : photoResult.message,
        };
      }));
      setNotice(result.registered ? "" : result.message);
      if (result.registered) {
        void loadEnrollments(adminSecret.trim());
      }
    } catch (error) {
      const message = error instanceof ApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : "Enrollment could not be completed.";
      readyPhotos.forEach((photo) => {
        URL.revokeObjectURL(photo.previewUrl);
        previewUrlsRef.current.delete(photo.previewUrl);
      });
      setPhotos((current) => current.map((photo) => photo.stage === "submitting"
        ? {
          ...photo,
          base64: "",
          previewUrl: "",
          stage: "error",
          message: "The backend did not confirm this photo's enrollment. Reselect it before retrying.",
        }
        : photo));
      setNotice(message);
    } finally {
      setIsSubmitting(false);
      onEnrollmentFinished?.(registered);
    }
  };

  const readyCount = photos.filter(
    (photo) =>
      (photo.stage === "ready" || photo.stage === "error" || photo.stage === "ACCEPTED") &&
      photo.base64,
  ).length;
  const hasPendingPreparation = photos.some((photo) => photo.stage === "preparing");

  return (
    <section className="stitch-card stitch-enrollment" aria-labelledby="enrollment-title">
      <div className="stitch-card-heading">
        <div className="stitch-heading-title">
          <span className="stitch-step">00</span>
          <div>
            <h2 id="enrollment-title">Reference enrollment</h2>
            <span className="stitch-subheading">CONSENT REQUIRED · BACKEND TEMPLATE</span>
          </div>
        </div>
        <span className={`stitch-enrollment-connection ${backendOnline ? "online" : "offline"}`} role="status">
          {backendOnline ? "BACKEND CONNECTED" : "BACKEND OFFLINE"}
        </span>
      </div>

      <p className="stitch-enrollment-intro">
        Add multiple clear reference photos to create or update one compact face template.
        Reference photos are processed once for enrollment; they do not grant access. You can
        start live capture while this upload runs; the access request waits for enrollment to finish.
      </p>

      <div className="stitch-enrollment-controls">
        <label className="stitch-enrollment-user">
          <span>USER ID</span>
          <input
            type="text"
            value={userId}
            onChange={(event) => setUserId(event.target.value)}
            placeholder="Enter the person’s user ID"
            autoComplete="off"
            disabled={isSubmitting}
          />
        </label>
        <label className="stitch-enrollment-user">
          <span>ADMIN SECRET</span>
          <input
            type="password"
            value={adminSecret}
            onChange={(event) => {
              setAdminSecret(event.target.value);
              setEnrollments([]);
              setAdminStatus("");
              setAdminError("");
            }}
            placeholder="Configured on the backend"
            autoComplete="new-password"
            disabled={isSubmitting || isLoadingEnrollments || Boolean(deletingUserId)}
          />
        </label>
        <div className="stitch-enrollment-select">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            onChange={(event) => void addPhotos(event)}
            disabled={isSubmitting || hasPendingPreparation || photos.length >= MAX_ENROLLMENT_PHOTOS}
            aria-label="Select reference photos"
          />
          <button
            className="stitch-button stitch-button-secondary"
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isSubmitting || hasPendingPreparation || photos.length >= MAX_ENROLLMENT_PHOTOS}
          >
            Add reference photos
          </button>
          <span>JPEG, PNG, or WebP · up to 5 MiB each · maximum {MAX_ENROLLMENT_PHOTOS}</span>
        </div>
      </div>

      <div className="stitch-enrollment-camera-controls">
        {!captureOpen ? (
          <button
            className="stitch-button stitch-button-secondary"
            type="button"
            onClick={() => void openCameraCapture()}
            disabled={isSubmitting || photos.length >= MAX_ENROLLMENT_PHOTOS}
          >
            Capture live photo
          </button>
        ) : (
          <section className="stitch-enrollment-camera" aria-label="Enrollment photo camera">
            <div className="stitch-enrollment-camera-view">
              <video
                ref={captureVideoRef}
                className="stitch-enrollment-camera-preview"
                autoPlay
                muted
                playsInline
                aria-label="Live enrollment camera preview"
                onLoadedMetadata={() => setCaptureVideoReady(true)}
                hidden={Boolean(capturedPhoto)}
              />
              {capturedPhoto && (
                <img
                  src={capturePreviewUrl}
                  alt="Captured reference photo preview"
                  className="stitch-enrollment-camera-preview"
                  aria-live="polite"
                />
              )}
              {!captureStream && !captureStarting && !captureError && (
                <span className="stitch-enrollment-camera-message">Waiting for camera…</span>
              )}
              {captureStarting && (
                <span className="stitch-enrollment-camera-message" role="status">Starting camera…</span>
              )}
              {captureStream && captureVideoReady && !capturedPhoto && (
                <span className="stitch-enrollment-camera-status" role="status">CAMERA ACTIVE</span>
              )}
            </div>
            <div className="stitch-enrollment-camera-actions">
              {capturedPhoto ? (
                <>
                  <button
                    className="stitch-button stitch-button-secondary"
                    type="button"
                    onClick={releaseCapturedPreview}
                    disabled={isSubmitting}
                  >
                    Retake
                  </button>
                  <button
                    className="stitch-button stitch-button-primary"
                    type="button"
                    onClick={() => void useCapturedPhoto()}
                    disabled={isSubmitting || hasPendingPreparation || photos.length >= MAX_ENROLLMENT_PHOTOS}
                  >
                    Use photo
                  </button>
                </>
              ) : (
                <button
                  className="stitch-button stitch-button-primary"
                  type="button"
                  onClick={() => void takePhoto()}
                  disabled={!captureStream || !captureVideoReady || captureStarting || capturingPhoto || isSubmitting || photos.length >= MAX_ENROLLMENT_PHOTOS}
                >
                  {capturingPhoto ? "Preparing photo…" : "Capture photo"}
                </button>
              )}
              <button
                className="stitch-button stitch-button-secondary"
                type="button"
                onClick={closeCameraCapture}
              >
                Cancel
              </button>
            </div>
            {captureError && <p className="stitch-enrollment-camera-error" role="alert">{captureError}</p>}
            <p className="stitch-enrollment-camera-note">
              Camera photos stay in browser memory until you choose Use photo. Consent is still required before enrollment.
            </p>
          </section>
        )}
      </div>

      {photos.length === 0 ? (
        <button
          className="stitch-photo-empty"
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={isSubmitting || !backendOnline}
        >
          <span aria-hidden="true">＋</span>
          <strong>No photos selected</strong>
          <span>Select at least two reference photos to begin.</span>
        </button>
      ) : (
        <div className="stitch-photo-grid" aria-live="polite">
          {photos.map((photo) => (
            <article className="stitch-photo-card" key={photo.id}>
              {photo.previewUrl ? (
                <img src={photo.previewUrl} alt={`Selected reference: ${photo.name}`} />
              ) : (
                <div className="stitch-photo-placeholder" aria-hidden="true">HID</div>
              )}
              <button
                className="stitch-photo-remove"
                type="button"
                onClick={() => removePhoto(photo)}
                disabled={isSubmitting}
                aria-label={`Remove ${photo.name}`}
              >
                ×
              </button>
              <div className="stitch-photo-info">
                <strong title={photo.name}>{photo.name}</strong>
                <span className={`stitch-photo-status ${statusClass(photo.stage)}`}>
                  {statusLabel(photo.stage)}
                </span>
                <p>{photo.message}</p>
              </div>
            </article>
          ))}
        </div>
      )}

      <label className="stitch-consent">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
          disabled={isSubmitting}
        />
        <span>
          I confirm that the person has explicitly consented to face-template enrollment.
          Photos are sent only to this backend for processing; original images are not stored.
        </span>
      </label>

      <div className="stitch-enrollment-footer">
        <p>Enrollment is not identity verification and does not authorize or grant access.</p>
        <button
          className="stitch-button stitch-button-primary"
          type="button"
          onClick={() => void submitEnrollment()}
          disabled={!backendOnline || isSubmitting || !adminSecret.trim() || hasPendingPreparation || readyCount < 2}
        >
          {isSubmitting ? "Processing enrollment…" : "Create face template"}
        </button>
      </div>

      {isSubmitting && (
        <p className="stitch-enrollment-progress" role="status" aria-live="polite">
          Sending {readyCount} prepared photo{readyCount === 1 ? "" : "s"}; the backend is validating the batch.
        </p>
      )}
      {notice && <p className="stitch-enrollment-notice" role="alert">{notice}</p>}
      {response && (
        <div className={`stitch-enrollment-result ${response.registered ? "complete" : "incomplete"}`} role="status">
          <strong>
            {response.registered
              ? response.updated ? "Template updated" : "Template created"
              : "Enrollment not completed"}
          </strong>
          <span>{response.message}</span>
          <span>
            {response.accepted_photo_count} accepted · {response.rejected_photo_count} rejected ·
            {" "}{response.duplicate_photo_count} duplicate · {response.processing_time_ms.toFixed(1)} ms backend time
          </span>
        </div>
      )}

      <section className="stitch-admin-enrollments" aria-labelledby="admin-enrollments-title">
        <div className="stitch-admin-heading">
          <div>
            <h3 id="admin-enrollments-title">Enrollment management</h3>
            <p>Authenticated admin access · template vectors are never displayed</p>
          </div>
          <button
            className="stitch-button stitch-button-secondary"
            type="button"
            onClick={() => void loadEnrollments()}
            disabled={!backendOnline || !adminSecret.trim() || isLoadingEnrollments || Boolean(deletingUserId)}
          >
            {isLoadingEnrollments ? "Loading…" : "Load enrolled users"}
          </button>
        </div>
        {adminStatus && <p className="stitch-admin-status" role="status">{adminStatus}</p>}
        {adminError && <p className="stitch-admin-error" role="alert">{adminError}</p>}
        {enrollments.length > 0 && (
          <ul className="stitch-admin-list">
            {enrollments.map((entry) => (
              <li key={entry.user_id}>
                <span>
                  <strong>{entry.user_id}</strong>
                  <small>{entry.template_count} face {entry.template_count === 1 ? "template" : "templates"}</small>
                </span>
                <button
                  className="stitch-button stitch-button-danger"
                  type="button"
                  onClick={() => void deleteEnrollment(entry.user_id)}
                  disabled={Boolean(deletingUserId)}
                >
                  {deletingUserId === entry.user_id ? "Deleting…" : "Delete"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
