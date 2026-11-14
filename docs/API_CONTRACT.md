# HID Face Liveness Detection System — API Contract

## Contract status

`GET /api/health`, `POST /api/face/detect`, `POST /api/liveness/challenge`, `POST /api/liveness/check`, `POST /api/auth/register`, `POST /api/auth/verify`, and `POST /api/access/decision` are implemented. Access decisions are experimental and do not control a physical door.

The API is version `0.1.0` today. Health, face-detection, liveness, face-authentication, and access-decision request/response models are implemented. JSON endpoints use `Content-Type: application/json`.

## Shared vocabulary and conventions

### Status values

- Liveness: `LIVE`, `SPOOF_SUSPECTED`, `UNCERTAIN`
- Identity: `VERIFIED`, `NOT_VERIFIED`, `NOT_CHECKED`
- Access decision: `ALLOW`, `STEP_UP_VERIFICATION`, `DENY`

`NOT_CHECKED` means identity was deliberately not evaluated, for example during face detection or liveness-only analysis. It is not equivalent to a failed identity match.

### Common result fields

Where applicable, operation responses may include:

| Field | Meaning |
| --- | --- |
| `success` | Whether the requested operation completed. A completed evaluation can have a negative result and still have `success: true`. |
| `decision` | Access-policy result; used only when an access decision is made. |
| `liveness_status` | Liveness outcome; omitted when liveness was not evaluated. |
| `identity_status` | Identity outcome; omitted when identity was not evaluated. |
| `confidence` | Optional operation-specific model confidence in `[0, 1]`; not a verified accuracy or a guarantee. |
| `processing_time_ms` | Backend processing duration for the operation, when measured. |
| `message` | Human-readable summary; clients should use status fields for logic. |

Fields are not mandatory on endpoints where they do not apply. In particular, face detection does not establish liveness or identity.

### Planned error format

For planned endpoints, errors should use a stable machine-readable code and a safe message:

```json
{
  "success": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "The request could not be processed."
  }
}
```

Possible HTTP errors include `400` for invalid input, `401`/`403` for missing or insufficient authorization/consent, `404` for an unknown identity, `413` for an oversized payload, `422` for schema validation, `429` for rate limiting, and `500` for an unexpected server failure. Exact codes and endpoint-specific behavior must be finalized during implementation. The current application error handler does not yet implement this planned error envelope for future routes.

### Frame representation for remaining planned endpoints

The planned initial JSON contract for the other camera-processing endpoints represents a frame as:

```json
{
  "image_base64": "<base64-encoded JPEG bytes>",
  "media_type": "image/jpeg",
  "captured_at": "2026-11-14T10:00:00Z"
}
```

The encoded value contains the JPEG bytes only, without a `data:image/...;base64,` prefix. `captured_at` is an ISO 8601 timestamp from the client and is useful for ordering; it is not trusted proof of capture time. Payload size and accepted frame-count limits must be set by implementation before production use.

Do not log frame payloads, raw face images, face embeddings, or other biometric material. Use HTTPS beyond local development and define secure retention/deletion behavior before storing biometric data.

## 1. Health

**Status: Implemented**

- **Method and path:** `GET /api/health`
- **Purpose:** Report that the API process is responding, along with configured application name and version.
- **Request format:** No body.
- **Fields:** No required or optional request fields.
- **Success response:** HTTP `200`.

Example response (actual implementation defaults):

```json
{
  "status": "healthy",
  "application": "HID Face Liveness Detection System",
  "version": "0.1.0"
}
```

The application name and version can be overridden by environment settings. This endpoint checks process responsiveness only; it does not verify database, camera, or biometric-processing readiness.

Possible errors: no endpoint-specific errors are defined. A server-level unexpected failure may return HTTP `500`.

## 2. Face detection

**Status: Implemented — baseline Haar Cascade detector**

- **Method and path:** `POST /api/face/detect`
- **Purpose:** Locate face(s) in a submitted camera frame. Detection does not determine whether a face is live or identify a person.
- **Request format:** JSON.
- **Required fields:**
  - `image_base64`: Base64-encoded JPEG bytes, without a data-URL prefix.
- **Optional fields:**
  - `request_id`: caller-provided string of 1–128 characters; if omitted, the API generates one.
- **Validation:** Empty/malformed Base64 is rejected. Only JPEG input is accepted. The decoded image may not exceed 5 MiB (5 × 1024 × 1024 bytes). Extra request fields are rejected.

Example request:

```json
{
  "request_id": "camera-frame-0001",
  "image_base64": "<base64-encoded JPEG bytes>"
}
```

Example response:

```json
{
  "success": true,
  "request_id": "camera-frame-0001",
  "face_detected": true,
  "face_count": 1,
  "faces": [
    {
      "x": 0.31,
      "y": 0.18,
      "width": 0.27,
      "height": 0.42
    }
  ],
  "processing_time_ms": 42,
  "message": "Face detected."
}
```

`x`, `y`, `width`, and `height` are normalized to the decoded image dimensions; the origin is the top-left. When no face is found, the response has `face_detected: false`, `face_count: 0`, and `faces: []`. The API does not return the submitted image or biometric data.

Possible errors:

- `413` decoded Base64 image exceeds 5 MiB.
- `415` image is not a JPEG.
- `422` empty/malformed Base64 or invalid/corrupted JPEG.
- `500` the Haar Cascade is unavailable or detector processing fails. The response is safe and does not include internal exception details.

The implementation uses OpenCV's built-in `haarcascade_frontalface_default.xml` with configurable `scaleFactor`, `minNeighbors`, and `minSize` service parameters (current defaults: `1.1`, `5`, and `(30, 30)`). It returns every box supplied by the detector and safely returns an empty list when no boxes are found. Images smaller than the configured minimum face size are accepted but may return no detections.

Haar Cascade is a lightweight baseline detector, not a liveness detector or a highly accurate production detector. Results may degrade with low light, blur, small/distant faces, occlusion, non-frontal poses, or unusual camera/image quality. A positive detection does not prove liveness or identity. No accuracy claim is made from the synthetic tests.

### Manual real-image testing

No real-face fixture is included in this repository. For an optional local check:

1. Start the backend with the command in the README.
2. On a laptop, use the Windows Camera app to take and manually save one JPEG still to a temporary directory outside the repository. Do not automate webcam capture.
3. Alternatively, capture an image separately on a mobile phone and transfer/export it as JPEG to a temporary directory outside the repository. HEIC and other formats are not accepted.
4. Send that local file to the endpoint from PowerShell. This reads the image into memory and prints only the detection response, not its Base64 content:

```powershell
$jpegPath = Join-Path $env:TEMP "face-test.jpg"
$imageBytes = [System.IO.File]::ReadAllBytes($jpegPath)
$body = @{
    request_id = [guid]::NewGuid().ToString()
    image_base64 = [Convert]::ToBase64String($imageBytes)
} | ConvertTo-Json -Compress
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/face/detect" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
Remove-Variable imageBytes, body
```

Use a temporary JPEG under 5 MiB. Do not place the image in the repository or commit it. The API does not persist the submitted image; delete the temporary source file after testing if it is no longer needed. A single manual observation is not an accuracy evaluation.

## 3. Liveness challenge

**Status: Implemented — in-memory prototype**

- **Method and path:** `POST /api/liveness/challenge`
- **Purpose:** Issue a cryptographically randomized left/right movement challenge.
- **Request format:** No body.
- **Response:** HTTP `200` with `challenge_id`, `challenge` (`MOVE_LEFT` or `MOVE_RIGHT`), and `expires_in_seconds` (`60`).

Example response:

```json
{
  "challenge_id": "4Uh...random-token...",
  "challenge": "MOVE_LEFT",
  "expires_in_seconds": 60
}
```

The challenge ID is unpredictable, expires after 60 seconds, and can be consumed only once. Challenges are held in process memory; a restart loses them, and multiple Uvicorn workers do not share challenge state. This storage is temporary and unsuitable for multi-worker or production deployment.

## 4. Liveness check

**Status: Implemented — experimental movement heuristic**

- **Method and path:** `POST /api/liveness/check`
- **Purpose:** Check whether an ordered series of submitted JPEG frames shows the requested horizontal face movement.
- **Request format:** JSON.
- **Required fields:**
  - `challenge_id`: ID returned by `/api/liveness/challenge`.
  - `frames`: ordered array of 5–20 Base64-encoded JPEG byte strings. Do not include data-URL prefixes.
- **Limits:** Each decoded frame is at most 5 MiB. Combined Base64 frame data is at most 20 MiB. Only JPEG signatures are accepted; malformed Base64 and non-JPEG data are rejected. Frames are processed in the supplied order.

Example request (abbreviated frame values):

```json
{
  "challenge_id": "4Uh...random-token...",
  "frames": [
    "<frame-1-base64>",
    "<frame-2-base64>",
    "<frame-3-base64>",
    "<frame-4-base64>",
    "<frame-5-base64>"
  ]
}
```

Example response:

```json
{
  "success": true,
  "challenge_id": "4Uh...random-token...",
  "liveness_status": "LIVE",
  "confidence": 0.83,
  "movement_detected": true,
  "valid_frame_count": 5,
  "total_frame_count": 5,
  "processing_time_ms": 78.4,
  "message": "Requested leftward movement met the experimental heuristic; this is not proof of liveness."
}
```

The numeric confidence and processing time above are illustrative. `confidence` is an uncalibrated challenge-evidence score, not a probability that the subject is live. A completed evaluation may return `LIVE`, `UNCERTAIN`, or `SPOOF_SUSPECTED`; `success: true` means evaluation completed, not that liveness was established.

### Movement heuristic

Each frame is processed by the existing Haar Cascade face detector. A frame is a valid observation only when exactly one face is detected. The normalized horizontal face center is calculated and kept in input order. At least `max(5, ceil(60% of submitted frames))` frames must be valid; otherwise the result is `UNCERTAIN`.

The analyzer compares the median center in the first third of valid observations with the median center in the last third:

- Minimum requested directional shift: **0.08 of image width**.
- Directional consistency: at least **60%** of non-noise consecutive movement steps must match the requested direction.
- Step noise below **0.005 of image width** is ignored.
- A sequence whose total horizontal range is below **0.025**, or whose shift clearly meets the threshold in the wrong direction, returns `SPOOF_SUSPECTED` as a failed challenge—not proof that the input was a spoof.

Thresholds are configurable through the `MovementAnalyzer` constructor in the service. They are prototype defaults, not values calibrated against a real evaluation dataset.

Possible errors:

- `404` challenge ID is unknown.
- `410` challenge has expired.
- `409` challenge has already been used.
- `413` per-frame or aggregate Base64 size limit exceeded.
- `422` invalid frame count, malformed Base64, unsupported/corrupted JPEG, or invalid request fields.
- `500` face detector unavailable.

The challenge is consumed when a validly structured check request is processed, including when detector processing subsequently fails.

### Limitations and security

This is an experimental movement heuristic, not a reliable anti-spoof system or production security decision. It does **not** reliably detect printed photos, screen replay, prerecorded videos, or 3D masks. A replay that reproduces the requested movement may pass. Do not use the result alone to grant access. No real-image accuracy claim is made.

## 5. Face registration

**Status: Implemented — consent-gated local enrollment**

- **Method and path:** `POST /api/auth/register`
- **Purpose:** Enroll one face for a caller-supplied user ID. Enrollment does not authenticate the person or assess liveness.
- **Request format:** JSON.
- **Required fields:**
  - `user_id`: non-empty ID, up to 128 characters; surrounding whitespace is removed.
  - `consent`: required boolean; it must be `true`.
  - `image_base64`: Base64-encoded JPEG bytes, without a data-URL prefix.
- **Image limits:** decoded JPEG size is limited to 5 MiB. Empty/malformed Base64, unsupported formats, and corrupt JPEGs are rejected.
- **Face rule:** exactly one face must be detected. No face or multiple faces returns `422`.

Example request:

```json
{
  "user_id": "person-12345",
  "consent": true,
  "image_base64": "<base64-encoded-JPEG-bytes>"
}
```

Success response: HTTP `201`.

```json
{
  "success": true,
  "registered": true,
  "user_id": "person-12345",
  "processing_time_ms": 48.0,
  "message": "Face enrollment completed. Identity was not verified."
}
```

`processing_time_ms` is illustrative. The response never includes the original image or embedding.

Possible errors: `403` consent is false; `409` user ID already enrolled; `413` image exceeds 5 MiB; `415` image is not JPEG; `422` invalid fields, malformed/corrupt JPEG, or face count other than one; `503` recognition model or local database unavailable.

Consent is an explicit API confirmation, not a legal-consent workflow or proof of the user's identity. Callers must explain purpose, storage, and retention before sending a request.

## 6. Face verification

**Status: Implemented — 1:1 SFace comparison**

- **Method and path:** `POST /api/auth/verify`
- **Purpose:** Compare one submitted face with the embedding registered for the supplied user ID. This is identity matching only and does not determine liveness.
- **Request format:** JSON.
- **Required fields:** `user_id` and `image_base64` (Base64 JPEG, without a data-URL prefix).
- **Image limits:** same 5 MiB JPEG and exactly-one-face validation as registration.

Example request:

```json
{
  "user_id": "person-12345",
  "image_base64": "<base64-encoded-JPEG-bytes>"
}
```

Example response:

```json
{
  "success": true,
  "user_id": "person-12345",
  "identity_status": "VERIFIED",
  "similarity": 0.71,
  "threshold": 0.363,
  "processing_time_ms": 45.0,
  "message": "The face matched the enrolled representation."
}
```

`similarity` is cosine similarity for normalized embeddings. It is a diagnostic score, not a probability or calibrated confidence. A completed comparison returns `success: true` and either `VERIFIED` or `NOT_VERIFIED`. This endpoint omits `liveness_status`; an identity match must not be interpreted as `LIVE`.

Possible errors: `404` user ID is not enrolled; `413` image exceeds 5 MiB; `415` image is not JPEG; `422` invalid fields, malformed/corrupt JPEG, or face count other than one; `503` recognition model or local database unavailable.

### Models, threshold, and storage

The recognition stage uses OpenCV Zoo's pretrained **SFace** ONNX model (`face_recognition_sface_2021dec.onnx`) with the **YuNet** face detector (`face_detection_yunet_2023mar.onnx`) to provide the five landmarks required for SFace alignment. Both run locally through OpenCV DNN on CPU; the existing Haar-based `/api/face/detect` endpoint is unchanged. SFace produces a 128-value embedding, which is L2-normalized before comparison and storage.

- Model source: [OpenCV Zoo SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) and [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet).
- Local model files and license notices are in `backend/models/`; the SFace model is approximately 36.9 MiB. SFace is distributed under Apache-2.0 and YuNet under MIT; see [`SFACE-LICENSE.txt`](../backend/models/SFACE-LICENSE.txt) and [`YUNET-LICENSE.txt`](../backend/models/YUNET-LICENSE.txt).
- The default cosine threshold is **0.363**, configurable through `FACE_MATCH_THRESHOLD`. This is the threshold used by OpenCV Zoo's SFace demo; it is an uncalibrated starting point, not an accuracy or security guarantee. Do not lower it to force a match.
- SQLite stores the user ID, a 512-byte little-endian float32 embedding, and server timestamps at `backend/data/face_embeddings.sqlite3` by default. `BIOMETRIC_DB_PATH` can override the file location. The directory is ignored by Git.
- Original images are processed transiently and are not stored. Embeddings are sensitive biometric data; this local SQLite prototype does not encrypt the database or provide authentication/authorization.

No DELETE API is implemented. For this local prototype, stop the backend and remove the database file to delete all enrollments. Add authenticated per-user deletion and retention controls before broader use.

## 7. Campus access decision

**Status: Implemented — prototype policy**

- **Method and path:** `POST /api/access/decision`
- **Purpose:** Consume a server-issued movement challenge, assess its ordered frames, compare the last Haar-valid frame with the enrolled user's SFace representation, evaluate the configured campus schedule, and calculate one access decision.
- **Request format:** JSON; `extra="forbid"` means client-supplied statuses, schedule results, confidence, or decisions are rejected.
- **Required fields:**
  - `user_id`: enrolled user ID.
  - `challenge_id`: ID returned by `POST /api/liveness/challenge`.
  - `frames`: ordered Base64 JPEG strings, 5–20 frames, maximum 5 MiB decoded per frame and 20 MiB combined Base64 payload.
- **Challenge behavior:** Uses the same 60-second, process-local, one-time challenge store as `/api/liveness/check`. Unknown, expired, and consumed IDs return `404`, `410`, and `409`, respectively.
- **Image behavior:** Runs the existing movement analyzer and passes its last valid face frame to the existing SFace verification service. The frame is already in the request sequence; the client does not send an extra identity image. If no valid frame or identity cannot be checked, identity is `NOT_CHECKED` and the decision cannot be `ALLOW`.
- **Identity detail:** `identity_message` explains whether identity was verified, mismatched, unevaluable because no single face was available, or unavailable because the local enrollment/model could not be used.

Example request:

```json
{
  "user_id": "person-12345",
  "challenge_id": "server-issued-challenge-id",
  "frames": [
    "<ordered-base64-JPEG-frame-1>",
    "<ordered-base64-JPEG-frame-2>",
    "<ordered-base64-JPEG-frame-3>",
    "<ordered-base64-JPEG-frame-4>",
    "<ordered-base64-JPEG-frame-5>"
  ]
}
```

Example response (timing is illustrative):

```json
{
  "success": true,
  "decision": "ALLOW",
  "challenge_id": "server-issued-challenge-id",
  "liveness_status": "LIVE",
  "identity_status": "VERIFIED",
  "schedule_status": "WITHIN_SCHEDULE",
  "reason": "Identity and liveness verified; access is permitted by the configured campus schedule.",
  "requires_step_up": false,
  "movement_detected": true,
  "valid_frame_count": 8,
  "total_frame_count": 10,
  "processing_time_ms": 83.2
}
```

`success` means the backend completed the decision evaluation; it does not mean access was granted. A decision is exactly one of `ALLOW`, `STEP_UP_VERIFICATION`, or `DENY`. Only the backend calculates statuses. The response includes no images or embeddings.

### Decision policy

| Liveness | Identity | Schedule | Decision |
| --- | --- | --- | --- |
| `LIVE` | `VERIFIED` | `WITHIN_SCHEDULE` | `ALLOW` |
| `LIVE` | `VERIFIED` | `OUTSIDE_SCHEDULE` | `STEP_UP_VERIFICATION` |
| `LIVE` | `VERIFIED` | `UNAVAILABLE` | `STEP_UP_VERIFICATION` |
| `UNCERTAIN` | `VERIFIED` or `NOT_CHECKED` | Any | `STEP_UP_VERIFICATION` |
| `SPOOF_SUSPECTED` | Any | Any | `DENY` |
| Any, including `UNCERTAIN` | `NOT_VERIFIED` | Any | `DENY` |
| `LIVE` | `NOT_CHECKED` | Any | `DENY` |

A failed liveness assessment or confirmed identity mismatch is denied before schedule evaluation can grant access. A movement challenge classified as uncertain can request step-up only when identity was not positively mismatched.

`STEP_UP_VERIFICATION` sets `requires_step_up: true`; it requests another approved verification method and is **not** an access grant. `DENY` is not a successful unlock. No physical door controller is connected.

### Campus schedule configuration

The default JSON schedule is `backend/config/campus_schedule.json`; set `CAMPUS_SCHEDULE_PATH` to use another file. It is read for each decision, so schedule edits do not require a process restart. The default timezone is `Asia/Kolkata`. Every weekday must have either a `closed: true` entry or an `open`/`close` window in `HH:MM` 24-hour format; closing must follow opening and windows do not cross midnight. Optional ISO dates in `exceptions` override the weekly entry for that local date. Opening is inclusive and closing is exclusive.

The included weekday hours and dated exceptions are **demonstration timings only, not verified PES University or other campus policy**. Replace them with timings approved by the campus. Missing, invalid, unreadable, or timezone-unresolvable configuration produces `schedule_status: UNAVAILABLE`, which can never yield `ALLOW`. `tzdata` supplies IANA timezone data on Windows.

This prototype has no individual student/staff role permissions or per-user schedule rules. Schedule compliance is only one part of a policy and is not authorization by itself.

Possible errors: `404` unknown challenge, `409` used challenge, `410` expired challenge, `413` frame payload over configured limits, `422` invalid request/Base64/JPEG, and `500` unavailable liveness detector. Unknown user, unavailable identity verification, and unavailable schedule produce a structured non-allow decision rather than trusted client status.

## Security and privacy notes

- Obtain explicit, informed consent for registration before processing biometric data.
- Keep liveness and identity evaluation separate; neither result substitutes for the other.
- Do not log raw frames, base64 image payloads, embeddings, or biometric-derived secrets.
- Use HTTPS outside local development; apply authorization, rate limits, payload limits, and retention/deletion controls before production.
- Do not treat confidence as calibrated accuracy or advertise unsupported attack resistance.
- The backend has no caller authentication/authorization, encrypted biometric storage, per-user deletion, individual student/staff role permissions, or production privacy controls. Step-up is not an access grant, and no physical door controller is connected.
