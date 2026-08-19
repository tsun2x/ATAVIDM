# First-Time Setup — TAVIDM

**Traffic Violation Detection and Monitoring System**

Use this guide after cloning the repository from GitHub for the first time on a new machine.

Repository: [https://github.com/tsun2x/ATAVIDM](https://github.com/tsun2x/ATAVIDM)

---

## What you are setting up

TAVIDM is a **Flask web application** that detects traffic violations in recorded MP4 videos and RTSP live streams. The current branch includes:

- **YOLOv8m + ByteTrack** detection pipeline (Ultralytics) with a rule-based violation engine (ROI, dwell-time, direction, object counting, and time-based rules)
- MP4 video upload, zone annotation, and reusable zone templates
- RTSP live camera streams with on-frame detection overlays
- Manual review queue (all detections are validated by an operator before confirmation)
- Analytics dashboards and real PDF/Excel report generation
- Authentication with three roles: System Administrator, Traffic Enforcement Officer, Guest Viewer

Custom-trained YOLOv8m weights (with helmet/rider classes) are loaded from `models/` when present; otherwise the pretrained COCO YOLOv8m checkpoint is downloaded automatically on first processing run. Helmet-based rules stay inactive until custom weights are provided.

---

## Requirements

| Requirement | Version | Check |
|-------------|---------|-------|
| Python | 3.10 or newer (3.9+ may work) | `python --version` |
| pip | Recent | `pip --version` |
| Git | Any | `git --version` |
| Internet | For CDN assets | Bootstrap, Chart.js, fonts load from CDN in the browser |

**Recommended:** 4 GB+ RAM, Windows/macOS/Linux.

---

## 1. Clone the repository

```bash
git clone https://github.com/tsun2x/ATAVIDM.git
cd ATAVIDM
```

If you use a specific branch (for example Phase 1 work):

```bash
git checkout cursor/phase-1-foundation
```

---

## 2. Create a virtual environment

Isolates project dependencies from your system Python.

### Windows (PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

When active, your prompt shows `(venv)`.

---

## 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This installs Flask, OpenCV, NumPy, and other packages listed in `requirements.txt`. The first install may take several minutes (OpenCV and related packages are large).

**Verify Flask:**

```bash
python -c "import flask; print(flask.__version__)"
```

**Verify OpenCV (needed for frame extraction on upload):**

```bash
python -c "import cv2; print(cv2.__version__)"
```

---

## 4. Optional environment configuration

Copy the example env file:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Edit `.env` if needed:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FLASK_SECRET_KEY` | (change in production) | Flask session signing |
| `DB_BACKEND` | `sqlite` | Active database backend |
| `DATABASE_URL` | `database/tavidm.db` | Backend connection target (sqlite path or future DSN) |
| `SQLITE_PATH` | `database/tavidm.db` | Legacy sqlite alias (optional) |
| `UPLOAD_FOLDER` | `dataset/raw` | Uploaded MP4 storage |
| `MAX_UPLOAD_MB` | `500` | Max single upload size in MB (env-driven; UI shows it on the upload panel) |
| `UPLOAD_DISK_HEADROOM_MB` | `500` | Free-disk headroom (MB) required *in addition to* the file size before an upload is accepted |

The app runs without a `.env` file — defaults are used. `.env` is gitignored.

### Recorded-footage uploads (large files)

Uploaded MP4s are saved **in full** to `dataset/raw`, so multi-GB footage is
not "free". Two guards protect the host:

- **Size limit** — `MAX_UPLOAD_MB` (default `500`) enforces the hard cap and
  returns a clear `413`/`400` when exceeded. Raise it only after confirming
  there is enough free disk for the file plus headroom.
- **Disk-space pre-check** — before saving, the upload path is checked with
  `shutil.disk_usage`. The request must leave at least `UPLOAD_DISK_HEADROOM_MB`
  (default `500` MB) free; otherwise it is rejected with a clear error instead
  of filling the volume mid-write.

### Sequential processing queue

Only **one** video is processed at a time (a single YOLOv8m + ByteTrack
inference job — the RTX 3050 6 GB cannot run two). Each `POST
/api/videos/<id>/process` request:

1. Opens a **pre-processing confirmation modal** listing the 12 canonical
   violation types as toggles (defaults = current global enabled set from
   Settings). The submitted list is the **run's frozen snapshot** — later
   changes to global Settings do not affect an in-flight run.
2. On submit, validates the list via `validate_enabled_violations()` and
   persists it in the `processing_runs` table (`enabled_violations_json`).
3. Enqueues the job. A second video is **auto-queued** (response
   `queued: true`) and runs after the first; requesting the *same* video again
   returns `409`.
4. On restart, any video left in `processing` is reset to `ready` and its
   `processing_runs` row marked `failed` (orphan recovery).

---

## 5. Folder structure (created automatically)

On first run, the app creates what it needs:

```
tavidm/
├── database/tavidm.db      ← SQLite (created on first run)
├── dataset/raw/            ← Uploaded MP4 files
├── dataset/frames/         ← Extracted first frames for zone editor
└── venv/                   ← Your virtual environment (not in git)
```

You do **not** need to run a separate database script. Starting the app calls `db.init_db()` and applies schema migrations automatically.

---

## 6. Run the application

From the project root (where `app.py` is located), with the virtual environment active:

```bash
python app.py
```

Expected output:

```
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
```

Open a browser:

**http://localhost:5000**

---

## 7. First-time walkthrough

### A. Sign in

Open **http://localhost:5000** — you are redirected to the login page.

Default bootstrap account (created on first run — change the password afterwards in Settings):

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin123` | System Administrator |

### B. Explore the UI

| URL | Page |
|-----|------|
| `/` | Dashboard (live statistics from the database) |
| `/live-monitor` | RTSP cameras, video upload + processing |
| `/violations` | Confirmed/dismissed violation records |
| `/analytics` | Trends, breakdowns, vehicle classifications |
| `/reports` | PDF / Excel report generation (enforcer/admin) |
| `/settings` | Rule parameters, cameras, templates, users (admin) |
| `/review-queue` | Manual validation of detections (enforcer/admin) |

### C. Upload and process your first video

1. Go to **Live Monitor**.
2. Drag and drop an **MP4** file (or click Browse).
3. Choose a traffic condition (morning / peak / nighttime).
4. Click **Upload**.
5. The **Zone Annotation** wizard opens with the first frame extracted from the video.
6. Choose **Use Existing Zone Template** or **Create New Zone Annotation**.
7. Draw at least one zone polygon (3+ points each). Available zones:
   - No Parking Zone
   - Active Lane
   - Pedestrian Crossing
   - Truck Ban Zone
   - No Loading/Unloading Zone
   - Restricted Lane
8. Click **Save Annotation**, then choose **Use for This Video Only** or **Save as New Template**.
9. Select the video in the **Uploaded Videos** list and click **Process Video**.
   The pipeline (YOLOv8m → ByteTrack → rule engine) runs in the background and
   queues detected violations for manual review.
10. Open the **Review Queue** to confirm or dismiss each detection.

### D. Manage templates, cameras, and users

Go to **Settings** (admin) to manage zone templates, RTSP cameras, rule parameters, and user accounts.

---

## 8. Stopping the server

In the terminal running the app:

```
Ctrl + C
```

Deactivate the virtual environment:

```bash
deactivate
```

---

## 9. Troubleshooting

### `ModuleNotFoundError: No module named 'flask'`

Virtual environment is not active or dependencies were not installed:

```bash
.\venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r requirements.txt
```

### `ModuleNotFoundError: No module named 'cv2'`

OpenCV is missing:

```bash
pip install opencv-python
```

### Port 5000 already in use

Edit the bottom of `app.py`:

```python
app.run(debug=True, port=5001)
```

Or stop the other process using port 5000.

### Upload fails or frame extraction error

- Use **MP4** only.
- Check file size (default max 500 MB).
- Ensure `dataset/raw/` and `dataset/frames/` are writable.
- Confirm OpenCV imports: `python -c "import cv2"`.

### Database issues after pulling new code

Restart the app — migrations run on startup. If the database is corrupted, delete `database/tavidm.db` and restart (this removes uploaded videos and annotations).

### Charts or styles look broken

Bootstrap and Chart.js load from CDN. You need internet in the browser on first load.

### PowerShell: `&&` not valid

Use separate commands or semicolons:

```powershell
cd ATAVIDM; .\venv\Scripts\Activate.ps1; python app.py
```

---

## 10. Project status

| Feature | Status |
|---------|--------|
| Web UI | Working |
| Video upload + SQLite | Working |
| Zone annotations + templates | Working |
| First-frame extraction | Working |
| YOLOv8m + ByteTrack detection | Working (COCO fallback; custom weights loaded from `models/`) |
| Rule-based violation engine | Working (10 rules; helmet rules need custom weights) |
| RTSP live streams | Working |
| Manual review queue | Working |
| Analytics + PDF/Excel reports | Working |
| Authentication + roles | Working (bcrypt, admin/enforcer/viewer) |

For more technical detail, see `CODEBASE_EXPLAINED.md` and `HOW_TO_RUN.md` (some sections in older docs may be outdated).

---

## Quick reference

```bash
git clone https://github.com/tsun2x/ATAVIDM.git
cd ATAVIDM
python -m venv venv
.\venv\Scripts\Activate.ps1          # Windows
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```
