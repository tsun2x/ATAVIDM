# TAVIDM

TAVIDM is a local Flask application for reviewing traffic footage and possible traffic violations. It stores uploaded videos and evidence in the project data folders and uses SQLite for application records.

## Requirements

- Windows, macOS, or Linux
- Python 3.10 or newer
- Git
- Enough free disk space for the video and generated frames/evidence

The project does not pin a GPU-specific PyTorch or CUDA installation in `requirements.txt`. Processing speed and GPU availability depend on the machine and its installed runtime. The first model load may need internet access to obtain the pretrained YOLOv8m weights if no suitable local weights are available.

## Quick start

Run these commands from the directory where you want the project cloned. Replace the password placeholder with a private password of at least 12 characters. Do not commit or share it.

### Windows PowerShell

```powershell
git clone https://github.com/tsun2x/ATAVIDM.git
cd ATAVIDM
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TAVIDM_BOOTSTRAP_ADMIN_PASSWORD = 'replace-with-a-private-password-of-12-or-more-characters'
$env:FLASK_SECRET_KEY = (.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))").Trim()
.\venv\Scripts\python.exe app.py
```

### macOS or Linux

```bash
git clone https://github.com/tsun2x/ATAVIDM.git
cd ATAVIDM
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
export TAVIDM_BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-private-password-of-12-or-more-characters'
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

Open <http://127.0.0.1:5000>. Sign in with username `admin` and the password you set above. The bootstrap password is used when the application creates the initial administrator or upgrades a legacy insecure default account. It does not reset an administrator account that already has a secure password. To change an existing account password, use **Settings → Individual Account Access → Edit**.

For repeat runs, set the same `FLASK_SECRET_KEY` and administrator password in your shell before starting the app, or put them in a private `.env` file in the repository root. The quick-start command generates a key for your local session; keep that value stable across restarts so browser sessions remain valid. `.env.example` contains blank placeholders and is not ready to use as-is. Environment variables take precedence over `.env`. Production mode requires an explicit secret key and enables secure session cookies; `python app.py` starts Flask's development server and is intended for local development.

## First use

1. Sign in as the administrator.
2. Open **Video Upload**, select an MP4 video, and upload it.
3. Open the video annotation page and mark the applicable zones. The supported zone types are `no_parking`, `active_lane`, `pedestrian_crossing`, `truck_ban_zone`, `loading_unloading`, and `restricted_lane`.
4. Save the annotations and start processing.
5. Review generated cases and evidence. Model output is decision support; review cases before treating them as confirmed violations.
6. Open **Reports** to preview or export cases. Reports are restricted to administrators and enforcement officers.
7. Use the application menu to sign out, then stop the local server with `Ctrl+C` in its terminal.

## Accounts and access

- **System Administrator (`admin`)** manages accounts and settings and can use all application areas.
- **Traffic Enforcement Officer (`enforcer`)** can review enforcement cases and generate reports.
- **Guest Viewer (`viewer`)** has limited, read-only access.

The application creates its initial administrator at first startup. Configure `TAVIDM_BOOTSTRAP_ADMIN_PASSWORD` before that startup; there is no shared default password.

## Processing and supported detections

The standard detector uses the YOLOv8m model family with ByteTrack. The configured object map has 15 classes: 10 vehicle classes from `config/training/class_schema.json`, plus person, rider, acceptable helmet, nut-shell helmet, and side mirror. A custom detector must have a class map compatible with the ordered schema. When no recognized custom weights are available, the application can fall back to pretrained YOLOv8m COCO weights; that fallback does not provide TAVIDM-specific classes.

The canonical registry contains 12 violation types. Four have a complete candidate processing path: obstruction, counterflow, truck-ban violation, and motorcycle overloading. The other eight have partial coverage; some depend on motorcycle-detail model evidence. A listed or enabled rule does not mean it is fully implemented, validated, or suitable for automatic enforcement. Missing or uncertain visual evidence must be reviewed rather than treated as proof that a violation occurred.

The optional motorcycle-detail model is configured separately. For its readiness and evidence limits, see [Phase 2 AI training readiness](docs/PHASE_2_AI_TRAINING_READINESS.md) and the [system truth index](docs/SYSTEM_TRUTH_INDEX.md).

The processing queue uses SQLite-backed claims. The default admission limits are three active jobs overall and one per account; the application starts two local worker threads. These are software limits, not a promise of processing speed or hardware capacity. Large workloads and multi-process GPU deployment need separate capacity evaluation.

Report export is capped at 5,000 matching cases per request. Narrow the report filters when a larger result set is reported as exceeding the cap.

## Files and storage

- Original uploads: `dataset/raw/`
- Extracted frames: `dataset/frames/`
- Annotated output: `dataset/annotated/`
- Other evidence and report files: under `dataset/`
- SQLite application database: `database/tavidm.db` by default (overridable with `SQLITE_PATH` or `DATABASE_URL`)

MP4 is the default allowed upload format and the default maximum upload size is 3 GiB. Upload requires at least 500 MiB of additional free disk headroom; processing outputs need further space. Back up the database and data folders together using an application-aware maintenance procedure. Do not delete the database as a troubleshooting step: it contains account and application records.

## Model weights

Place a compatible custom detector in `models/`. The application checks `tavidm_yolov8m.pt`, `best.pt`, and `yolov8m_custom.pt` before using the pretrained `yolov8m.pt` fallback. The model's ordered class map must match the configured TAVIDM schema. Installing weights does not establish model accuracy or validate violation decisions.

## Troubleshooting

- **Administrator sign-in fails on a new database:** confirm that `TAVIDM_BOOTSTRAP_ADMIN_PASSWORD` was set before the first application startup and has at least 12 characters.
- **The bootstrap password did not change an existing account:** use **Settings → Individual Account Access → Edit** while signed in as an administrator.
- **The model cannot load while offline:** provide compatible local weights or allow access to download the pretrained fallback weights.
- **Upload is rejected for space:** free disk space; the configured headroom is in addition to the upload itself, and processing creates more files.
- **A port is already in use:** stop the process using port 5000 or change the port in the local `app.run(..., port=5000)` call while retaining the existing `debug_enabled` setting.
- **A rule has incomplete evidence:** consult the system truth index and review the case manually. Do not infer a violation from a missing detection.

## Further documentation

- [System truth index](docs/SYSTEM_TRUTH_INDEX.md)
- [Phase 2 AI training readiness](docs/PHASE_2_AI_TRAINING_READINESS.md)
- [Codebase explanation](CODEBASE_EXPLAINED.md)

`HOW_TO_RUN.md` is retained for older bookmarks and points to this README. Its previous setup commands are obsolete.
