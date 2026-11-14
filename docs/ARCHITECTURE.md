# HID Face Liveness Detection System — Architecture

## Status and scope

This document describes the current prototype and intended application architecture. The FastAPI application shell, baseline Haar Cascade face detection, experimental movement-challenge liveness, SFace identity enrollment and verification, local SQLite embedding storage, and campus-schedule access decision are implemented. The React demo captures frames and displays the backend decision. No physical door controller is connected.

## System overview

The browser is responsible for camera permission and capture. It submits ordered frames and an enrolled user ID to the backend. The backend independently evaluates liveness, identity, and schedule before calculating the access decision. Local SQLite stores the enrolled face representations.

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
- Request camera permission and capture frames in the browser.
- Submit ordered frames, a server-issued challenge ID, and an enrolled user ID to the decision endpoint.
- Display the decision and the backend-returned liveness, identity, and schedule statuses.
- Avoid persisting biometric images in browser storage or logging image payloads.

The frontend does not provide registration UI; it expects an already enrolled user ID.

### FastAPI backend — partially implemented

The current backend initializes a FastAPI application, configures local-development CORS, exposes a root message and `GET /api/health`, and provides `POST /api/face/detect` using OpenCV's built-in Haar Cascade. It also provides a one-time movement challenge, consent-gated registration and 1:1 verification using OpenCV Zoo YuNet/SFace, and `POST /api/access/decision`. The decision route consumes the existing challenge, invokes the existing movement analyzer, reuses the last valid frame with the existing embedding-verification service, and evaluates the configured campus schedule. Face detection, liveness, and identity comparison remain distinct checks; the movement heuristic is not reliable anti-spoofing.

The identity endpoints validate JPEG requests and consent, extract normalized embeddings, and store them in local SQLite. The decision response reports `ALLOW`, `STEP_UP_VERIFICATION`, or `DENY`; it does not operate a physical lock. The backend does not authenticate callers, encrypt the biometric database, implement per-user deletion, or enforce individual student/staff permissions.

### Frame capture and submission

The browser should capture camera frames only after the user grants permission. Frame data is submitted to the API for the active operation and should be handled transiently where possible. Requests should use HTTPS outside local development. The backend must not write raw frames or biometric data to application logs.

The implemented APIs accept JPEG bytes encoded as Base64 in JSON. Face-image requests are limited to 5 MiB decoded; liveness accepts 5–20 ordered frames with a total Base64 payload limit. A later implementation may choose a suitable streaming or multipart transport if payload size or latency requires it; any change must be reflected in the contract and frontend together.

### Processing stages — independent

1. **Face detection (implemented baseline)** locates faces in a submitted JPEG frame using Haar Cascade and reports normalized bounding boxes. It does not determine liveness or identity and is not claimed to be a highly accurate production detector.
2. **Liveness analysis (experimental prototype)** issues a random left/right movement challenge, checks face-center movement across ordered frames, and reports `LIVE`, `SPOOF_SUSPECTED`, or `UNCERTAIN`. This is not reliable anti-spoofing; blink detection alone is also not reliable proof of liveness. No method should be represented as complete protection against photos, replays, 3D masks, or sophisticated attacks.
3. **Identity verification (prototype)** uses YuNet landmarks to align one face, extracts a normalized 128-value embedding with the OpenCV Zoo SFace ONNX model, and compares it with the enrolled user's embedding using cosine similarity. It reports `VERIFIED` or `NOT_VERIFIED`; a match does not establish that the presented subject is live. The default threshold is 0.363 and is not calibrated for this application's population or use.
4. **Access decision (prototype)** combines backend-calculated liveness, identity, and schedule states. Only `LIVE` + `VERIFIED` + `WITHIN_SCHEDULE` returns `ALLOW`. Uncertain evidence or schedule issues request `STEP_UP_VERIFICATION`; suspected spoof and an identity mismatch return `DENY`. It does not grant physical access or apply student/staff role permissions.

The baseline detection, experimental liveness, prototype identity verification, and schedule-based decision stages are implemented.

### Decision engine — prototype

The decision layer calculates the result server-side; client-supplied statuses and decisions are rejected. Suspected spoof and identity mismatch deny; uncertain liveness requests step-up; schedule unavailable cannot allow. Step-up is a request for another approved method and is not an access grant. The `success` response flag means decision evaluation completed, not that access was granted.

### Campus schedule — demonstration configuration

`backend/config/campus_schedule.json` contains weekly windows, closed days, dated overrides, and the IANA timezone (default `Asia/Kolkata`). `CAMPUS_SCHEDULE_PATH` can select another file. The file is validated and read for each decision. Missing/invalid configuration produces `UNAVAILABLE`, which cannot yield `ALLOW`. Included hours and exceptions are demonstration values only, not verified campus policy. Campus-approved schedule and role-specific permissions must be supplied before deployment.

### SQLite — local prototype

SQLite stores user IDs, normalized embedding bytes, and consent/creation timestamps in `backend/data/face_embeddings.sqlite3` by default. Original images are not stored. The database is local, unencrypted, and not protected by caller authentication or authorization. No per-user deletion endpoint is implemented; stop the backend and remove the database file to clear all records. This is not a production storage design.

### Future AWS deployment boundary — future

AWS deployment is outside the current implementation. If deployed later, the React client should communicate with the API over HTTPS; the API should own biometric processing and access to persistence. Managed AWS services should be introduced only when deployment needs justify them. Secrets must be supplied through a secret-management mechanism, never committed to source control. No AWS resources or integration are currently configured.

## Data handling and consent

Registration requires an explicit `consent: true` field, but the API does not provide a legal-consent workflow or verify the user's identity. Callers must explain purpose, retention, deletion, and sharing before sending biometric data. Embeddings are sensitive biometric data. The prototype stores no source image and does not log raw image or embedding contents, but lacks database encryption, authentication, authorization, retention enforcement, and individual deletion. Identity verification is not liveness. The schedule is not campus-approved, no student/staff role permissions exist, step-up is not an access grant, and no door controller is connected.
