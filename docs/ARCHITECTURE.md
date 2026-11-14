# HID Face Liveness Detection System — Architecture

## Status and scope

This document describes the current prototype and intended application architecture. The FastAPI application shell, baseline Haar Cascade face detection, experimental movement-challenge liveness, consent-based single and batch SFace enrollment, automatic template matching, local SQLite embedding storage, and campus-schedule access decision are implemented. The React demo supports batch enrollment and captures frames for backend decisions. No physical door controller is connected.

## System overview

The browser is responsible for camera permission, selected reference photos, and live capture. Enrollment submits consent and a selected image batch once. Access attempts submit ordered frames and a server-issued challenge; the backend searches local enrolled templates without requiring a visitor-supplied user ID. The backend independently evaluates liveness, identity, and schedule before calculating the access decision. Local SQLite stores only aggregate face representations.

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
| normalized face embeddings  |
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

The identity endpoints validate JPEG requests and consent, extract normalized embeddings, and store them in local SQLite. The batch enrollment route processes each photo once, rejects poor-quality or non-single-face images, deduplicates similar embeddings, and stores one normalized aggregate template per user ID. The access decision route reuses the liveness-selected frame to search stored templates; only a unique match is `VERIFIED`, no match is denied, and multiple plausible matches request step-up. The decision response reports `ALLOW`, `STEP_UP_VERIFICATION`, or `DENY`; it does not operate a physical lock. The backend does not authenticate callers, encrypt the biometric database, implement per-user deletion, or enforce individual student/staff permissions.

### Frame capture and submission

The browser should capture camera frames only after the user grants permission. Frame data is submitted to the API for the active operation and should be handled transiently where possible. Requests should use HTTPS outside local development. The backend must not write raw frames or biometric data to application logs.

The implemented APIs accept JPEG bytes encoded as Base64 in JSON. Face-image requests are limited to 5 MiB decoded; liveness accepts 5–20 ordered frames with a total Base64 payload limit. A later implementation may choose a suitable streaming or multipart transport if payload size or latency requires it; any change must be reflected in the contract and frontend together.

### Processing stages — independent

1. **Face detection (implemented baseline)** locates faces in a submitted JPEG frame using Haar Cascade and reports normalized bounding boxes. It does not determine liveness or identity and is not claimed to be a highly accurate production detector.
2. **Liveness analysis (experimental prototype)** issues a random left/right movement challenge, checks face-center movement across ordered frames, and reports `LIVE`, `SPOOF_SUSPECTED`, or `UNCERTAIN`. This is not reliable anti-spoofing; blink detection alone is also not reliable proof of liveness. No method should be represented as complete protection against photos, replays, 3D masks, or sophisticated attacks.
3. **Identity enrollment and verification (prototype)** uses YuNet landmarks to align one face, extracts a normalized 128-value embedding with the OpenCV Zoo SFace ONNX model, and compares embeddings with cosine similarity. Batch enrollment combines multiple accepted, distinct reference embeddings into one stored template; access-time matching infers one embedding from the current selected frame and searches those precomputed templates. One unique match reports `VERIFIED`, no match `NOT_VERIFIED`, and multiple matches `AMBIGUOUS`. A match does not establish that the presented subject is live. The default threshold is 0.363 and is not calibrated for this application's population or use.
4. **Access decision (prototype)** combines backend-calculated liveness, identity, and schedule states. Only `LIVE` + `VERIFIED` + `WITHIN_SCHEDULE` can return `ALLOW`. Uncertain evidence, ambiguous identity, or schedule issues request `STEP_UP_VERIFICATION`; suspected spoof and an identity mismatch return `DENY`. It does not grant physical access or apply student/staff role permissions.

The baseline detection, experimental liveness, prototype identity verification, and schedule-based decision stages are implemented.

### Decision engine — prototype

The decision layer calculates the result server-side; client-supplied statuses and decisions are rejected. Suspected spoof and identity mismatch deny; uncertain liveness requests step-up; schedule unavailable cannot allow. Step-up is a request for another approved method and is not an access grant. The `success` response flag means decision evaluation completed, not that access was granted.

### Campus schedule — demonstration configuration

`backend/config/campus_schedule.json` contains weekly windows, closed days, dated overrides, and the IANA timezone (default `Asia/Kolkata`). `CAMPUS_SCHEDULE_PATH` can select another file. The file is validated and read for each decision. Missing/invalid configuration produces `UNAVAILABLE`, which cannot yield `ALLOW`. Included hours and exceptions are demonstration values only, not verified campus policy. Campus-approved schedule and role-specific permissions must be supplied before deployment.

### SQLite — local prototype

SQLite stores one normalized aggregate embedding per user ID, along with consent/creation timestamps, in `backend/data/face_embeddings.sqlite3` by default. Batch enrollment updates the existing template rather than storing a gallery of source photos or per-photo embeddings. Original images are not stored. The database is local, unencrypted, and not protected by caller authentication or authorization. No per-user deletion endpoint is implemented; stop the backend and remove the database file to clear all records. This is not a production storage design.

### Future AWS deployment boundary — future

AWS deployment is outside the current implementation. If deployed later, the React client should communicate with the API over HTTPS; the API should own biometric processing and access to persistence. Managed AWS services should be introduced only when deployment needs justify them. Secrets must be supplied through a secret-management mechanism, never committed to source control. No AWS resources or integration are currently configured.

## Data handling and consent

Single and batch registration require an explicit `consent: true` field, but the API does not provide a legal-consent workflow or verify the user's identity. Callers must explain purpose, retention, deletion, and sharing before sending biometric data. Embeddings are sensitive biometric data. The prototype stores no source image and does not log raw image or embedding contents, but lacks database encryption, authentication, authorization, retention enforcement, and individual deletion. Identity verification is not liveness. The movement heuristic can be replayed and is not a reliable anti-spoof method; no guarantee is made against printed photos, video replay, or masks. The schedule is not campus-approved, no student/staff role permissions exist, step-up is not an access grant, and no door controller is connected.
