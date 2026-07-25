# HOW TO RUN — TAVIDM

**Traffic Violation Detection and Monitoring System**
Flask · Python 3 · YOLOv8m + ByteTrack · SQLite

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Project Structure](#2-project-structure)
3. [Installation](#3-installation)
4. [Running the Application](#4-running-the-application)
5. [Accessing the Pages](#5-accessing-the-pages)
6. [Configuration & Customization](#6-configuration--customization)
7. [Stopping the Server](#7-stopping-the-server)
8. [Troubleshooting](#8-troubleshooting)
9. [Environment Notes](#9-environment-notes)

---

## 1. Prerequisites

Before running TAVIDM you need the following installed on your machine:

| Requirement | Minimum Version | How to Check |
|---|---|---|
| Python | 3.9+ | `python --version` |
| pip | 21+ | `pip --version` |
| Git (optional) | Any | `git --version` |

> **Note:** The system uses a real detection pipeline (OpenCV → YOLOv8m → ByteTrack → rule engine), SQLite storage, and bcrypt-based authentication. Sign in with the bootstrap account `admin` / `admin123` on first run, then change the password.

---

## 2. Project Structure

```
tavidm/
├── app.py                   ← Flask application entry point (routes + APIs)
├── config.py                ← Paths, upload limits, environment configuration
├── requirements.txt         ← Python package dependencies
│
├── core/                    ← Processing modules
│   ├── detection_config.py  ← YOLOv8m model config, classes, violation registry
│   ├── detector.py          ← YOLOv8m inference + ByteTrack (Ultralytics)
│   ├── tracker.py           ← Track motion history (trajectory/direction/dwell)
│   ├── zone_config.py       ← Zone types and validation
│   ├── violation_engine.py  ← Rule-based violation detection
│   ├── video_processor.py   ← End-to-end pipeline orchestrator
│   ├── live_stream.py       ← RTSP live stream workers (MJPEG)
│   ├── evidence.py          ← Annotated evidence snapshots
│   ├── analytics.py         ← Dashboard/analytics aggregations
│   ├── reports.py           ← PDF (fpdf2) / Excel (openpyxl) reports
│   └── auth.py              ← bcrypt auth + role-based access
│
├── database/                ← SQLite schema, adapter, and db facade
├── templates/               ← Jinja2 HTML templates (login, dashboard, …)
├── static/                  ← CSS, JS, evidence snapshots, generated reports
├── dataset/raw/             ← Uploaded MP4 files
└── models/                  ← Custom YOLOv8m weights (optional)
```

---

## 3. Installation

### Step 1 — Clone or download the project

```bash
git clone https://github.com/tsun2x/ATAVIDM.git
cd ATAVIDM
```

Or if you already have the files, simply navigate to the project root:

```bash
cd tavidm
```

### Step 2 — Create a virtual environment (recommended)

A virtual environment keeps the project dependencies isolated from your system Python.

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You will see `(venv)` prepended to your terminal prompt when the environment is active.

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

Key packages installed:

| Package | Purpose |
|---|---|
| `Flask` / `Werkzeug` | Web framework, routing, dev server |
| `ultralytics` | YOLOv8m inference and ByteTrack tracking |
| `opencv-python` | Video decoding, frame extraction, drawing |
| `numpy` | Array math for the pipeline |
| `bcrypt` | Password hashing |
| `fpdf2` / `openpyxl` | PDF and Excel report generation |

> The first install may take several minutes (Ultralytics pulls PyTorch). On the first video processing run, the pretrained `yolov8m.pt` checkpoint (~50 MB) is downloaded automatically unless custom weights exist in `models/`.

---

## 4. Running the Application

With your virtual environment **active** and from the project root directory, run:

```bash
python app.py
```

You should see output similar to:

```
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
 * Restarting with watchdog (windowsapi)
 * Debugger is active!
 * Debugger PIN: 123-456-789
```

The application is now live at **http://127.0.0.1:5000** (also accessible as http://localhost:5000).

### What `debug=True` gives you

- **Auto-reload** — the server restarts automatically whenever you save `app.py` or any Python file.
- **Interactive debugger** — if an exception occurs, Flask shows an in-browser error page with a full traceback.
- **No need to restart manually** during development.

> Do **not** use `debug=True` in any production deployment. For production, use a WSGI server like Gunicorn or uWSGI behind Nginx.

---

## 5. Accessing the Pages

Once the server is running, open a browser and navigate to:

You will be redirected to `/login` first — sign in with `admin` / `admin123` (bootstrap account).

| URL | Page | Description |
|---|---|---|
| `http://localhost:5000/` | Dashboard | Stat cards, hourly chart, recent violations (live database data) |
| `http://localhost:5000/live-monitor` | Live Monitor | RTSP camera feeds, video upload, processing controls |
| `http://localhost:5000/violations` | Violations | Paginated table with search/filter, evidence snapshots, detail modals |
| `http://localhost:5000/review-queue` | Review Queue | Manual validation of detections (enforcer/admin) |
| `http://localhost:5000/analytics` | Analytics | KPI cards, daily/monthly charts, vehicle class breakdowns |
| `http://localhost:5000/reports` | Reports | Real PDF/Excel report generation and download history (enforcer/admin) |
| `http://localhost:5000/settings` | Settings | Rule parameters, cameras, zone templates, user management (admin) |

---

## 6. Configuration & Customization

File paths and upload limits live in **`config.py`** (overridable via `.env`). Detection and rule parameters are managed at runtime from the **Settings** page and stored in the `system_settings` database table.

### Change the port

```python
# At the bottom of app.py
if __name__ == "__main__":
    app.run(debug=True, port=5000)  # Change 5000 to any available port
```

### Change the secret key

Set `FLASK_SECRET_KEY` in `.env` (see `.env.example`). Use a long random string in production.

### Add cameras

Go to **Settings → Cameras** (admin) and add a camera with its RTSP URL. Streams appear on the Live Monitor page.

### Adjust rule parameters

Go to **Settings → Detection Parameters** (admin): confidence threshold, dwell-time thresholds, truck ban hours, and review confidence bands.

### Custom model weights

Place custom-trained YOLOv8m weights (with helmet/rider classes) at `models/tavidm_yolov8m.pt` (or any `.pt` in `models/`). Without them, the pretrained COCO checkpoint is used and helmet-based rules stay inactive. The supported violation types are defined in `core/detection_config.py`.

---

## 7. Stopping the Server

In the terminal where the server is running, press:

```
Ctrl + C
```

Then deactivate the virtual environment:

```bash
deactivate
```

---

## 8. Troubleshooting

### `ModuleNotFoundError: No module named 'flask'`

You either forgot to activate the virtual environment, or skipped the install step:

```bash
venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### `Address already in use` / `OSError: [Errno 98]`

Port 5000 is in use by another process. Either kill that process or change the port:

```python
app.run(debug=True, port=5001)
```

On macOS 12+, AirPlay Receiver uses port 5000 by default — disable it in System Preferences → Sharing, or just use a different port.

### `TemplateNotFound`

Make sure you are running `python app.py` from the project **root** directory (where `app.py` lives), not from inside `templates/` or `static/`.

### Charts not rendering

Charts rely on **Chart.js 4.4.3** loaded from jsDelivr CDN. If you are offline, the charts will be blank. You can download Chart.js locally and update the `<script>` tag in `dashboard.html` and `analytics.html` to point to the local file.

### Page styles look broken

The app loads **Bootstrap 5.3.3** and **Bootstrap Icons 1.11.3** from CDN. These also require internet access. If you need to run offline, download both from [getbootstrap.com](https://getbootstrap.com) and [icons.getbootstrap.com](https://icons.getbootstrap.com) and place them in `static/`.

---

## 9. Environment Notes

- **OS:** Developed and tested on Windows. Runs identically on macOS and Linux.
- **Browser support:** Modern Chromium-based browsers (Chrome, Edge), Firefox, Safari. IE is not supported.
- **Database:** SQLite (`database/tavidm.db`), created and migrated automatically on startup. PostgreSQL/Supabase is planned for production deployment.
- **`.env` file:** optional — defaults work for development. Set `FLASK_SECRET_KEY` for anything beyond local testing.
- **Network:** CDN assets (Bootstrap, Chart.js) load in the browser; the server downloads the pretrained YOLOv8m checkpoint once on first processing run.
