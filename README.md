# HID Face Liveness Detection & Authentication System

A hackathon prototype with separate face-detection, movement-based liveness, 1:1 identity-verification, and campus-schedule access-decision steps. It does not control a physical door.

## Problem

Face matching alone cannot establish that the camera is observing a physically present person: a printed photo, a screen replay, or other presentation attack may be presented to the camera. This prototype keeps liveness and identity as separate checks, then combines their server-calculated results with a configurable schedule.

## Current implementation status

The repository currently contains a FastAPI backend and a basic React demo:

- FastAPI application initialization and environment-backed settings
- Local-development CORS configuration
- `GET /api/health`
- `POST /api/face/detect` using OpenCV's built-in Haar Cascade as a lightweight baseline
- `POST /api/liveness/challenge` and `POST /api/liveness/check` using a basic randomized movement heuristic
- `POST /api/auth/register` and `POST /api/auth/verify` using OpenCV Zoo YuNet and SFace models with local SQLite embedding storage
- `POST /api/auth/register/batch` for consent- and admin-authenticated enrollment, storing one normalized 128-dimensional template per accepted distinct photo
- Authenticated `GET` and `DELETE /api/admin/enrollments` for user IDs and template counts; embeddings are never exposed
- `POST /api/access/decision` combining a consumed liveness challenge, SFace identity comparison, and campus schedule
- Pytest coverage for health, face detection, challenge expiry/reuse, and movement outcomes
- A React enrollment panel and camera-preview screen; access attempts automatically search enrolled templates and capture 10 ordered JPEG frames

The liveness result is an experimental movement heuristic, not reliable spoof detection. It does not reliably detect printed photos, screen replay, prerecorded video, or 3D masks; replayed movement may pass. No tested presentation-attack detection model is configured, so the backend fails closed to step-up or deny and cannot return `ALLOW`. SFace matching is an uncalibrated prototype, not production-grade identity security. The access endpoint is a demonstration policy, not campus-approved policy or a physical unlock. The included hours are examples, not verified PES University timings. No individual student/staff permissions are implemented. The frontend can enroll a consenting user from multiple reference photos and the backend automatically searches stored templates during a live challenge; it never calculates identity or access decisions in the browser.

## Technology stack

- **Backend:** Python, FastAPI, Uvicorn, Pydantic, pytest
- **Frontend:** React, TypeScript, and Vite
- **Computer vision:** OpenCV headless Haar Cascade for baseline detection, plus OpenCV Zoo YuNet and SFace ONNX models for face alignment and embedding comparison
- **Database:** local SQLite stores normalized face embeddings only; source images are not stored
- **Schedule:** editable JSON configuration with IANA timezone evaluation
- **Deployment (future):** AWS is outside the current implementation

## Project structure

```text
hid-liveness-system/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # Health, face detection, liveness, and face-auth routes
│   │   ├── core/             # Application settings
│   │   ├── schemas/          # API request/response models
│   │   ├── services/         # Detection, liveness, SFace, and SQLite services
│   │   └── main.py           # FastAPI app, middleware, and route registration
│   ├── models/               # Local OpenCV Zoo ONNX weights and license notices
│   ├── config/               # Demonstration campus access schedule
│   ├── tests/                # Backend unit, integration, and model smoke tests
│   └── requirements.txt
├── docs/
│   ├── ARCHITECTURE.md       # Current and planned system responsibilities
│   └── API_CONTRACT.md       # Implemented routes and remaining planned API
├── frontend/                 # Consent-based enrollment and camera challenge demo
└── README.md
```

## Backend setup (Windows PowerShell)

Python 3.14.7 was used to validate the current backend. From the project root:

```powershell
Set-Location .\backend
.\.venv\Scripts\Activate.ps1
```

If the existing environment is not present, create a virtual environment and install the pinned dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run backend tests:

```powershell
python -m pytest -q
```

Configure the required admin secret in the same PowerShell session before running the API. Generate a high-entropy secret, keep it out of source control, and enter the same value in the frontend's **ADMIN SECRET** field:

```powershell
$env:ADMIN_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(32))")
Write-Output $env:ADMIN_SECRET
```

Copy the printed value into the frontend's **ADMIN SECRET** field. Keep it private and out of source control. `ADMIN_SECRET` has no insecure default; without it, enrollment and admin list/delete operations return `503`.

Start the API:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The health endpoint is at [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). Interactive Swagger documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Stop Uvicorn with `Ctrl+C`.

The default local CORS origins include Vite's `localhost` and `127.0.0.1` on ports 5173 and 5174, covering Vite's first automatic fallback when 5173 is occupied. Configure `ALLOWED_ORIGINS` as a comma-separated environment variable if the frontend uses a different local origin.

To manually test face detection with a laptop webcam still or a separately captured mobile JPEG, follow the temporary-file procedure in the [API contract](docs/API_CONTRACT.md#manual-real-image-testing). No camera capture is automated, and the repository contains no real-face test image.

For liveness, first call `POST /api/liveness/challenge`, then submit 5–20 ordered JPEG Base64 strings from the same capture session to `POST /api/liveness/check` before the 60-second expiry. Challenge storage is process-local and temporary; do not rely on it with multiple workers or as a production security boundary.

For local batch enrollment, use the frontend's **Reference enrollment** section with the configured admin secret, an authorized user ID, and at least two clear, distinct photos from a consenting person. Confirm consent before submitting. The browser accepts JPEG, PNG, and WebP files up to 5 MiB each, converts them to JPEG, and sends them only to the configured backend. The backend requires exactly one face per accepted image, applies basic image-quality checks, deduplicates exact and near-duplicate photos, and stores one normalized embedding per distinct accepted photo. It does not store the original photos. Submitted browser image data is cleared when each request completes. Enrollment success is shown only when the backend reports `registered: true`. The admin panel can list enrolled user IDs and template counts or delete a user's templates. These routes require `Authorization: Bearer <ADMIN_SECRET>`; embeddings are not returned. Swagger requests must include this header. The existing single-image `POST /api/auth/register` route is also admin-protected; `POST /api/auth/verify` remains a 1:1 identity-only comparison, not an access decision.

For access verification, the visitor does not need to type a user ID. The camera demo submits only the ordered frames and challenge ID to `POST /api/access/decision`; the backend compares the selected live frame with precomputed per-photo templates. A unique threshold-qualified match returns the backend-provided user ID, multiple plausible matches request step-up, and no match is denied. The backend—not the browser—calculates liveness, identity, schedule status, and one decision. A tested presentation-attack detection model is not available, so even `LIVE` + `VERIFIED` + `WITHIN_SCHEDULE` cannot return `ALLOW`; movement evidence is `UNCERTAIN` and fails closed to step-up. Step-up requests are not access grants. Enrollment photos alone cannot call the access endpoint or grant access.

The enrollment panel also offers **Capture live photo**. If the access camera is already active, its stream is reused; otherwise the enrollment panel opens its own camera stream and stops only that stream when closed. Captured photos stay in browser memory and pass through the same JPEG preparation, size, count, consent, and backend validation rules as uploaded photos. Access capture can run while enrollment uploads; if capture finishes first, access submission waits for enrollment confirmation, and cancelling while waiting submits no decision. The result view shows the measured challenge-to-final-frame duration, browser API round-trip, and backend processing time separately. No consenting real-participant benchmark has been run on a named machine/network, so the 1.5-second post-capture API target is **not measured or confirmed**. Displayed timings are per-attempt measurements, not a performance guarantee.

When the backend is unavailable, the frontend offers **Record offline step-up**. This local path never evaluates biometrics or grants access; it instructs the visitor to contact authorized campus staff for an in-person check. The browser queues at most 50 events for up to 24 hours, containing only a random event ID, UTC timestamp, fixed step-up decision, and backend-unavailable reason. It stores no user ID, face image, or embedding. Events sync automatically when the backend reconnects through `POST /api/access/offline-sync`; the backend accepts only step-up events, deduplicates them, and retains them for up to 7 days. This is an audit trail, not an offline authorization or a staff-authentication mechanism.

The recognition models are checked into `backend/models/` with their license notices; no extra model download is needed at runtime. The default database is `backend/data/face_embeddings.sqlite3` (ignored by Git). The `face_templates` table stores normalized 128-dimensional float32 embeddings as 512-byte little-endian BLOBs, per user and per distinct accepted photo, plus a SHA-256 reference-photo digest. Original photos are not stored. `BIOMETRIC_DB_PATH` changes the database path and `FACE_MATCH_THRESHOLD` configures the cosine cutoff. The default cutoff `0.363` is an uncalibrated OpenCV Zoo demo threshold, not a probability or accuracy claim. Batch enrollment uses basic size, brightness, and blur checks; these are not calibrated quality or identity guarantees. On first open, existing `face_embeddings` rows are migrated without deleting them: each aggregate becomes one `face_templates` row with no photo hash. New batch enrollments replace that user's template set and keep the old table synchronized for legacy schema compatibility; current verification reads from `face_templates`.

The demonstration access schedule is `backend/config/campus_schedule.json`; edit it to use campus-approved hours or set `CAMPUS_SCHEDULE_PATH` to another JSON file. It is read for each decision. The timezone defaults to `Asia/Kolkata`; the sample opening hours and holiday exceptions are not verified PES University policy. Missing or invalid schedule data cannot produce `ALLOW`. `tzdata` is included for IANA timezone support on Windows.

## Frontend setup (Windows PowerShell)

In a second terminal from the project root:

```powershell
Set-Location .\frontend
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The frontend checks `GET /api/health` and uses `http://127.0.0.1:8000` by default. To use another backend address, copy `.env.example` to `.env.local`, set `VITE_API_BASE_URL`, and ensure the backend `ALLOWED_ORIGINS` includes the frontend's origin.

The browser asks for camera permission only after **Start camera** is clicked. On phones, camera access requires HTTPS except when the page is opened on `localhost` on that same device. The front-camera preference is requested where available. Captured frames are kept in browser memory and submitted to the backend for the active check; the frontend does not save them to disk. Stop the camera when finished.

Frontend checks:

```powershell
npm run test
npm run build
```

## Documentation

- [System architecture](docs/ARCHITECTURE.md)
- [Frontend/backend API contract](docs/API_CONTRACT.md)

## Biometric data, consent, and limitations

Enrollment requires explicit consent and the configured admin secret, detects one face per image, and stores only normalized SFace template vectors, user IDs, hashes, and timestamps. The original JPEG is not stored. Explicit API consent does not establish legal compliance or prove a user's identity. Embeddings are sensitive biometric data: this prototype's SQLite database is local and unencrypted. Authenticated admin list/delete endpoints are available, but retention policy and role-specific authorization are not implemented.

Identity verification and liveness are independent. `VERIFIED` does not mean `LIVE`. This prototype has no individual student/staff role permissions, authenticated kiosk identity, or physical door-controller connection. A step-up result requests another approved verification method; it is not an access grant. Do not rely on this prototype for security-sensitive decisions or expose it to untrusted clients.
#   H I D _ b u i l d  
 