# HID Face Liveness & Campus Access Prototype

A local prototype for face enrollment, identity matching, movement challenges, and campus schedule checks. The backend calculates an access decision; **no physical door or lock is connected**.

## What it does

- Captures camera frames in the browser.
- Enrolls a user from multiple reference photos, storing face templates rather than the original photos.
- Supports automatic 1:N identity search and optional 1:1 verification using an enrolled user ID.
- Issues a one-time `MOVE_LEFT` or `MOVE_RIGHT` challenge and checks face movement across frames.
- Applies the configured campus schedule.
- Returns an access decision and reason from the backend.
- Provides authenticated enrollment management and a safe offline fallback that records a step-up event; offline mode does not grant access.

## Current security status

The included YuNet and SFace models are for face detection and face recognition. **They are not presentation-attack detection (PAD) models.** The current movement check does not prove that a person is live and cannot reliably detect printed photos, screen replays, video replays, or 3D masks.

Until a suitable PAD model is integrated and evaluated, uncertain liveness must remain `STEP_UP_VERIFICATION`. Do not change the movement threshold or label movement as `LIVE` just to obtain `ALLOW`. A previous video-replay attempt returned `ALLOW`; that result demonstrates why movement alone is not sufficient.

PAD performance is not measured for this repository. No detection rate, false-accept rate, or false-reject rate should be claimed for any spoof type unless it has been tested using documented samples and conditions.

## Technology stack

- **Frontend:** React, TypeScript, Vite
- **Backend:** Python, FastAPI
- **Computer vision:** OpenCV, YuNet face detection, SFace face embeddings
- **Local storage:** SQLite for face templates and offline decision events
- **Schedule:** JSON configuration with an explicit timezone

## Repository layout

```text
backend/
  app/
    api/routes/       FastAPI routes for access, enrollment, admin, and liveness
    schemas/          Request and response models
    services/         Face matching, movement analysis, schedule, storage
  config/
    campus_schedule.json
  data/               Local SQLite databases (not for Git)
  models/             YuNet and SFace model files and licenses
  tests/
frontend/
  src/
    App.tsx
    components/BatchEnrollment.tsx
    services/api.ts
    frameCapture.ts
    challengeDisplay.ts
    stitch.css
docs/
  API_CONTRACT.md
  ARCHITECTURE.md
```

## Requirements

- Windows, macOS, or Linux
- Python version supported by `backend/requirements.txt`
- Node.js and npm
- A webcam or a camera exposed to the browser
- A modern browser with camera permission enabled

On a phone, camera access normally requires HTTPS. `localhost` is suitable for local development on the same computer. A phone opening the development server through a computer’s network address may need HTTPS.

## Run locally on Windows

Open two terminals in VS Code.

### 1. Configure and start the backend

```powershell
cd "C:\Users\SONU\Downloads\HID\hid-liveness-system\backend"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set the admin secret for this terminal session. Replace the example with a long random value and do not commit it:

```powershell
$env:ADMIN_SECRET = "replace-with-a-long-random-secret"
```

Optional environment variables:

```powershell
$env:FACE_MATCH_THRESHOLD = "0.363"
$env:ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
```

Start FastAPI:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API health check is available at:

```text
http://127.0.0.1:8000/api/health
```

The interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

### 2. Start the frontend

In a second terminal:

```powershell
cd "C:\Users\SONU\Downloads\HID\hid-liveness-system\frontend"
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

The frontend uses `http://127.0.0.1:8000` as the default backend URL. To use another backend, configure `VITE_API_BASE_URL` before starting Vite.

## Enrollment and identity verification

1. Start the backend and frontend.
2. Open the enrollment section.
3. Enter a stable user ID, for example `sonu-demo`.
4. Provide consent and enroll at least two usable reference photos.
5. Keep all reference photos for one person under the same user ID.
6. Start camera verification and follow the screen-side movement instruction.
7. If automatic identification reports `AMBIGUOUS`, enter the intended enrolled user ID in the optional ID field and retry.

The entered ID is only a claimed identity. The backend still compares the captured face with the templates for that user. A wrong ID or a non-matching face must not verify. Automatic 1:N matching does not guess between multiple matching profiles.

Enrollment management requires the configured admin secret. The admin interface can list enrollment IDs and template counts or delete an enrollment. Deleting an enrollment is permanent for that local database; verify the ID before deleting it.

## Campus schedule

Edit `backend/config/campus_schedule.json` to configure the access window. The timezone is explicit; the example uses `Asia/Kolkata`.

Example:

```json
{
  "timezone": "Asia/Kolkata",
  "weekly": {
    "monday": { "open": "08:00", "close": "20:00" },
    "tuesday": { "open": "08:00", "close": "20:00" },
    "wednesday": { "open": "08:00", "close": "20:00" },
    "thursday": { "open": "08:00", "close": "20:00" },
    "friday": { "open": "08:00", "close": "20:00" },
    "saturday": { "open": "09:00", "close": "16:00" },
    "sunday": { "closed": true }
  },
  "exceptions": {}
}
```

The closing time is exclusive. For example, `close: "16:00"` means an attempt at 16:00 is outside the window. Configure demo hours deliberately; do not bypass the schedule in code.

## Decision meanings

- **ALLOW:** The configured policy permits access. The current project does not have a tested PAD model, so do not treat a movement match as sufficient evidence to enable this outcome.
- **STEP_UP_VERIFICATION:** Evidence is uncertain, multiple profiles match, schedule information is unavailable/outside the allowed time, or additional verification is required. This is not an access grant.
- **DENY:** The identity does not match or the requested movement challenge failed. A failed movement challenge is not, by itself, proof that the person is a spoof.

The current movement analyzer can report `UNCERTAIN` even when it observes the requested movement. This is intentional while no tested PAD model is configured.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Backend health |
| `POST` | `/api/liveness/challenge` | Create a one-time movement challenge |
| `POST` | `/api/access/decision` | Evaluate frames, identity, schedule, and policy |
| `POST` | `/api/auth/register` | Enroll a single reference image |
| `POST` | `/api/auth/register/batch` | Enroll multiple reference images for one user |
| `POST` | `/api/auth/verify` | Verify a face against a specified enrolled user |
| `GET` | `/api/admin/enrollments` | List IDs and template counts; requires admin authentication |
| `DELETE` | `/api/admin/enrollments` | Delete an enrollment; requires admin authentication |
| `POST` | `/api/access/offline-sync` | Sync queued step-up events; does not grant access |

For exact request fields and response shapes, see [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

## Data and privacy

- Face enrollment stores embedding templates in a local SQLite database; it is not intended to store the uploaded reference photos.
- The default face database is under `backend/data/face_embeddings.sqlite3`.
- Offline events use a separate local SQLite database.
- Do not commit `.env` files, SQLite databases, captured frames, enrollment photos, or admin secrets.
- Admin credentials must remain on the backend and must not be embedded in frontend source code.
- The project is a prototype, not a production access-control system.

## Timing and evaluation

The UI reports backend processing, browser API round-trip, and capture duration separately. These timings are different measurements:

- **Backend processing:** time spent evaluating a submitted request.
- **API round-trip:** browser send-to-response time.
- **Capture duration:** challenge creation/countdown through the final captured frame.

Some recorded examples had backend/API times below two seconds, while one capture took about 3.86 seconds. These are individual observations, not a performance guarantee or a controlled benchmark.

For a spoof evaluation, record the camera/device, lighting, sample type, number of attempts, outcome, and decision time for each category:

- Real face
- Printed photo
- Photo shown on a screen
- Video replay
- 3D or printed mask

Do not report detection rates or false-accept/false-reject rates from one or two attempts. The mask and replay categories remain unvalidated until a suitable PAD model and representative test samples are used.

## Focused verification

Run the two relevant identity/access tests:

```powershell
cd "C:\Users\SONU\Downloads\HID\hid-liveness-system\backend"
.\.venv\Scripts\python.exe -m pytest `
  tests/test_face_auth.py::test_claimed_identity_must_match_and_be_the_strongest_enrolled_profile `
  tests/test_access_decision.py::test_uncertain_liveness_with_verified_identity_steps_up_and_reuses_last_valid_frame -q
```

Build the frontend:

```powershell
cd "C:\Users\SONU\Downloads\HID\hid-liveness-system\frontend"
npm run build
```

## Known limitations

- No tested PAD model is configured.
- Printed-photo, screen-replay, video-replay, and mask detection performance has not been established.
- Movement alone cannot establish liveness.
- The system has no physical door control.
- A `STEP_UP_VERIFICATION` result is not approval; use an authorized secondary process.
- Results from a small number of manual attempts are not accuracy statistics.
