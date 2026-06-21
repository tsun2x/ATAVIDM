# HOW TO RUN — TAVIDM

**Traffic Violation Detection and Monitoring System**
Frontend Prototype · Flask · Python 3

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

> **Note:** This is a **frontend prototype only**. There is no database, no authentication backend, and no real AI pipeline. All data is mocked in Python at startup.

---

## 2. Project Structure

```
tavidm/
├── app.py                   ← Flask application entry point (all routes + mock data)
├── requirements.txt         ← Python package dependencies
│
├── templates/               ← Jinja2 HTML templates
│   ├── base.html            ← Master layout (sidebar, navbar, footer)
│   ├── dashboard.html       ← Main dashboard with stat cards and charts
│   ├── live_monitor.html    ← CCTV feed simulation with detection overlays
│   ├── violations.html      ← Paginated & filterable violations table
│   ├── analytics.html       ← KPI cards, bar/line/doughnut charts
│   ├── reports.html         ← Report generator and download history
│   └── settings.html        ← Camera config, user management, system preferences
│
└── static/
    ├── css/
    │   └── style.css        ← All custom styles (no Sass, plain CSS with variables)
    ├── js/
    │   ├── main.js          ← Shared utilities: sidebar toggle, toast notifications
    │   ├── dashboard.js     ← Chart.js: hourly line chart + weekly bar chart
    │   ├── live_monitor.js  ← Simulated CCTV: moving boxes, alert feed, camera switch
    │   ├── violations.js    ← Client-side search, filter, sort, pagination, modals
    │   ├── analytics.js     ← Chart.js: daily bar, monthly line, violation doughnut
    │   ├── reports.js       ← Report generation simulation, download history
    │   └── settings.js      ← Slider live-update, save/edit toast feedback
    └── images/
        ├── camera_thumb.svg
        ├── cctv_feed.svg
        ├── cctv_inbound.svg
        ├── cctv_outbound.svg
        └── evidence_1.svg … evidence_6.svg
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

This installs exactly two packages:

| Package | Version | Purpose |
|---|---|---|
| `Flask` | 3.0.3 | Web framework — routing, template rendering, dev server |
| `Werkzeug` | 3.0.3 | WSGI utilities — Flask's internal dependency for request/response handling |

> Flask automatically installs Werkzeug as a dependency, but pinning it in `requirements.txt` ensures version consistency across environments.

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

| URL | Page | Description |
|---|---|---|
| `http://localhost:5000/` | Dashboard | Overview: stat cards, hourly & weekly charts, recent violations table |
| `http://localhost:5000/live-monitor` | Live Monitor | Simulated CCTV feed with animated AI detection bounding boxes |
| `http://localhost:5000/violations` | Violations | Full paginated table with search, filter by type/camera/status, sort, detail modals |
| `http://localhost:5000/analytics` | Analytics | KPI cards, daily/monthly charts, doughnut breakdown, top-violations table |
| `http://localhost:5000/reports` | Reports | Report generator form (PDF/Excel demo), download history table |
| `http://localhost:5000/settings` | Settings | Camera list, detection threshold sliders, user management, system preferences |

---

## 6. Configuration & Customization

All configuration lives inside **`app.py`** as Python constants near the top of the file.

### Change the port

```python
# At the bottom of app.py
if __name__ == "__main__":
    app.run(debug=True, port=5000)  # Change 5000 to any available port
```

### Adjust mock data volume

```python
MOCK_VIOLATIONS = generate_violations(count=48)  # Increase or decrease this number
```

### Change the secret key

```python
app.config["SECRET_KEY"] = "tavidm-prototype-dev-key"
# Replace with a long random string in production
```

### Add cameras

Edit the `CAMERAS` list in `app.py`:

```python
CAMERAS = [
    {
        "id": "cam-03",
        "name": "Highway Overpass",
        "location": "North Sector — Highway",
        "status": "online",
        "fps": 60,
        "feed_image": "cctv_feed.svg",   # must exist in static/images/
    },
    ...
]
```

### Add violation types

```python
VIOLATION_TYPES = [
    "Red Light Violation",
    "Speeding",
    ...
    "Your New Type",   # Add here
]
```

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
- **No database required.** All data is generated in memory at server startup using `random` from the Python standard library.
- **No `.env` file required.** There are no secrets to configure for the prototype.
- **No external API calls.** All CDN assets (Bootstrap, Chart.js, Google Fonts) are the only external network requests, made by the browser.
