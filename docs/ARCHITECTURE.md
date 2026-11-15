# HID Face Liveness Detection System — Architecture

## Status and scope

This document describes the current prototype and intended application architecture. The FastAPI application shell, baseline Haar Cascade face detection, experimental movement challenge, consent-based single and batch SFace enrollment, automatic per-template matching, authenticated enrollment management, local SQLite storage, and campus-schedule evaluation are implemented. No tested presentation-attack detection model is configured, so the access policy fails closed and cannot return `ALLOW`. No physical door controller is connected.

## System overview

The browser is responsible for camera permission, selected reference photos, and live capture. Enrollment submits consent and a selected image batch once. Access attempts submit ordered live-camera frames and a server-issued challenge; the backend searches local enrolled templates without requiring a visitor-supplied user ID. The backend independently evaluates liveness, identity, and schedule before calculating the access decision. SQLite stores only per-photo embeddings, photo hashes, user IDs, and a legacy aggregate used for compatibility; it does not store source photos.

```text
+-----------------------------+
| React frontend (demo)       |
| UI, camera capture          |
+--------------+--------------+
               | HTTPS / JSON API
               | selected frames; no raw-image logging
               v
+-----------------------------+
| FastAPI backend             |
|                             |
| Face detection (Haar)       |
| Liveness (experimental)     |-----> Liveness result
| Identity (YuNet + SFace)    |-----> Identity result
| (independent of liveness)   |
| Campus schedule policy       |-----> Access decision
+--------------+--------------+
               |
               v
+-----------------------------+
| Local SQLite                |
| per-photo face templates    |
+-----------------------------+

Future deployment boundary:
Browser <-> deployed API service <-> managed database/object storage as justified
```

## Component responsibilities

### React frontend — liveness demo

- Present backend health, camera preview, and movement-challenge access decisions.
- Offer consent-gated batch enrollment with photo previews, local format/size checks, and backend per-photo results.
- Allow an enrollment photo to be captured from the active access-camera stream, or from a separately owned stream that is stopped when the panel closes.
- Request camera permission and capture frames in the browser.
- Allow access capture to overlap enrollment upload, wait for a successful enrollment before sending the access decision, and allow cancellation while waiting.
- Submit the ordered frames and server-issued challenge ID; the backend performs automatic matching against stored templates.
- Display the matched user ID, decision, and backend-returned liveness, identity, and schedule statuses.
- Report challenge-to-final-frame time, browser API round-trip, and backend processing time as separate measured values.
- Avoid persisting biometric images in browser storage or logging image payloads.

The frontend does not calculate identity or access outcomes. It converts supported selected images to JPEG locally for enrollment, then sends them only to the configured backend. It never grants access from enrollment photos.

### FastAPI backend — partially implemented

The current backend initializes a FastAPI application, configures local-development CORS, exposes a root message and `GET /api/health`, and provides `POST /api/face/detect` using OpenCV's built-in Haar Cascade. It also provides a one-time movement challenge, consent-gated registration and 1:1 verification using OpenCV Zoo YuNet/SFace, and `POST /api/access/decision`. The decision route consumes the existing challenge, invokes the existing movement analyzer, reuses the last valid frame with the existing embedding-verification service, and evaluates the configured campus schedule. Face detection, liveness, and identity comparison remain distinct checks; the movement heuristic is not reliable anti-spoofing.

The registration routes require a configured admin bearer secret and explicit consent. Batch enrollment processes each photo once, rejects poor-quality or non-single-face images, removes exact and near duplicates, and stores one normalized 128-dimensional float32 template per accepted distinct photo. The admin list/delete routes are protected by the same secret and expose only user IDs and template counts. The access decision route reuses the liveness-selected camera frame to search stored templates: each user's score is the maximum cosine similarity across their template set; one threshold-qualified user is `VERIFIED`, none is `NOT_VERIFIED`, and multiple candidates are `AMBIGUOUS`. Because movement is not reliable proof of liveness and there is no tested presentation-attack detector, even a `LIVE` status cannot produce `ALLOW`. SQLite is local and unencrypted; individual deletion is available, but role permissions and production privacy controls are not.

### Frame capture and submission

The browser should capture camera frames only after the user grants permission. Frame data is submitted to the API for the active operation and should be handled transiently where possible. Requests should use HTTPS outside local development. The backend must not write raw frames or biometric data to application logs.

The implemented APIs accept JPEG bytes encoded as Base64 in JSON. Face-image requests are limited to 5 MiB decoded; liveness accepts 5–20 ordered frames with a total Base64 payload limit. A later implementation may choose a suitable streaming or multipart transport if payload size or latency requires it; any change must be reflected in the contract and frontend together.

### Processing stages — independent

1. **Face detection (implemented baseline)** locates faces in a submitted JPEG frame using Haar Cascade and reports normalized bounding boxes. It does not determine liveness or identity and is not claimed to be a highly accurate production detector.
2. **Liveness analysis (experimental prototype)** issues a random left/right movement challenge and checks face-center movement across ordered frames. Observed movement and insufficient evidence return `UNCERTAIN`; a failed movement challenge may return `SPOOF_SUSPECTED`, which does not prove a spoof. The analyzer does not report `LIVE`; no tested presentation-attack detector is installed. No method should be represented as protection against photos, replays, 3D masks, or sophisticated attacks.
3. **Identity enrollment and verification (prototype)** uses YuNet landmarks to align one face and extracts a normalized 128-value embedding with the OpenCV Zoo SFace ONNX model. Batch enrollment stores each accepted distinct photo's vector in `face_templates` and filters exact SHA-256 duplicates plus embeddings with cosine similarity at or above `0.995` within the batch. At access time one live-frame embedding is compared vectorially with stored templates; a user's score is the maximum sample-level cosine similarity. Exactly one user must meet the configured threshold; no match is `NOT_VERIFIED`, while multiple matches are `AMBIGUOUS`. The default threshold `0.363` is uncalibrated for this population and is not a probability.
4. **Access decision (fail-closed prototype)** combines backend-calculated liveness, identity, and schedule states. In the absence of a tested presentation-attack detector, no combination returns `ALLOW`, including an upstream `LIVE` + `VERIFIED` + `WITHIN_SCHEDULE`. Uncertain evidence or ambiguous identity requests `STEP_UP_VERIFICATION`; suspected spoof and an identity mismatch return `DENY`. It does not grant physical access or apply student/staff role permissions.

The baseline detection, experimental liveness, prototype identity verification, and schedule-based decision stages are implemented.

### Decision engine — prototype

The decision layer calculates the result server-side; client-supplied statuses and decisions are rejected. Suspected spoof and identity mismatch deny; uncertain liveness requests step-up; schedule unavailable cannot allow. Step-up is a request for another approved method and is not an access grant. The `success` response flag means decision evaluation completed, not that access was granted.

### Campus schedule — demonstration configuration

`backend/config/campus_schedule.json` contains weekly windows, closed days, dated overrides, and the IANA timezone (default `Asia/Kolkata`). `CAMPUS_SCHEDULE_PATH` can select another file. The file is validated and read for each decision. Missing/invalid configuration produces `UNAVAILABLE`, which cannot yield `ALLOW`. Included hours and exceptions are demonstration values only, not verified campus policy. Campus-approved schedule and role-specific permissions must be supplied before deployment.

### SQLite — local prototype

The default SQLite database path is `backend/data/face_embeddings.sqlite3`; `BIOMETRIC_DB_PATH` overrides it. Each row in `face_templates` stores a normalized 128-dimensional float32 vector (512-byte little-endian BLOB), user ID, optional SHA-256 photo digest, and timestamp. Original images are not stored. The legacy `face_embeddings` table remains and is backfilled on first open: each old aggregate row becomes one template with no photo digest, while the old row and all enrollments are retained. New batch enrollments replace that user's template set transactionally and keep the aggregate table synchronized for legacy schema compatibility; current verification uses `face_templates`. Authenticated `DELETE /api/admin/enrollments` removes both tables' rows for one user. Admin routes require a server-configured `ADMIN_SECRET` with no default; the database itself remains local and unencrypted.

### Future AWS deployment boundary — future

AWS deployment is outside the current implementation. If deployed later, the React client should communicate with the API over HTTPS; the API should own biometric processing and access to persistence. Managed AWS services should be introduced only when deployment needs justify them. Secrets must be supplied through a secret-management mechanism, never committed to source control. No AWS resources or integration are currently configured.

## Data handling and consent

Single and batch registration require `consent: true` and a configured admin bearer secret, but the API does not provide a legal-consent workflow or verify the subject's identity. Callers must explain purpose, retention, deletion, and sharing before sending biometric data. Embeddings are sensitive biometric data. The prototype stores no source image and does not log raw image or embedding contents, but lacks database encryption, retention enforcement, and individual student/staff permissions. Identity verification is not liveness. The movement heuristic can be replayed and is not a reliable anti-spoof method; no guarantee is made against printed photos, video replay, or masks. No real-participant performance benchmark has been run, so the 1.5-second target is not confirmed. The schedule is not campus-approved, step-up is not an access grant, and no door controller is connected.
