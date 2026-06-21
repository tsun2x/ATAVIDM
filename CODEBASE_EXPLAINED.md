# CODEBASE EXPLAINED — TAVIDM

**Traffic Violation Detection and Monitoring System**
Line-by-line code walkthrough, architecture overview, and dependency map.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [app.py — The Flask Backend](#2-apppy--the-flask-backend)
3. [requirements.txt — Dependencies](#3-requirementstxt--dependencies)
4. [templates/base.html — Master Layout](#4-templatesbasehtml--master-layout)
5. [templates/dashboard.html](#5-templatesdashboardhtml)
6. [templates/live_monitor.html](#6-templateslive_monitorhtml)
7. [templates/violations.html](#7-templatesviolationshtml)
8. [templates/analytics.html](#8-templatesanalyticshtml)
9. [templates/reports.html](#9-templatesreportshtml)
10. [templates/settings.html](#10-templatessettingshtml)
11. [static/css/style.css](#11-staticcssstylecss)
12. [static/js/main.js](#12-staticjsmainjs)
13. [static/js/dashboard.js](#13-staticjsdashboardjs)
14. [static/js/live_monitor.js](#14-staticjslive_monitorjs)
15. [static/js/violations.js](#15-staticjsviolationsjs)
16. [static/js/analytics.js](#16-staticjsanalyticsjs)
17. [static/js/reports.js](#17-staticjsreportsjs)
18. [static/js/settings.js](#18-staticjssettingsjs)
19. [static/images/ — SVG Assets](#19-staticimages--svg-assets)
20. [Data Flow Diagram](#20-data-flow-diagram)
21. [External CDN Dependencies](#21-external-cdn-dependencies)

---

## 1. System Architecture

```
Browser
  │
  │  HTTP GET /  (or /live-monitor, /violations, etc.)
  ▼
Flask Dev Server (port 5000)
  │
  ├── app.py          ← generates mock data, calls render_template()
  │
  ├── templates/      ← Jinja2 renders HTML server-side
  │     └── base.html ← all pages extend this
  │
  └── static/         ← served as-is by Flask's built-in static file handler
        ├── css/style.css      ← custom styles
        ├── js/*.js            ← page-specific interactivity
        └── images/*.svg       ← placeholder CCTV and evidence images

External CDN (loaded by the browser):
  - Bootstrap 5.3.3 (CSS + JS bundle)
  - Bootstrap Icons 1.11.3
  - Google Fonts — Inter
  - Chart.js 4.4.3 (only on dashboard and analytics pages)
```

The architecture is a classic **server-side rendered (SSR) monolith**. Flask renders full HTML pages; JavaScript only adds interactivity on top. There is no REST API, no AJAX calls, and no client-side routing. Every page navigation is a full HTTP round-trip.

---

## 2. app.py — The Flask Backend

`app.py` is the single Python file that powers the entire application. It handles routing, mock data generation, and template rendering.

### Module-level docstring

```python
"""TAVIDM - Traffic Violation Detection and Monitoring System
Frontend prototype only. All data is mocked for demonstration purposes.
"""
```

This docstring documents the file's intent at the top. It's a signal to any developer reading the file that no real database or AI pipeline is connected.

### Imports

```python
from datetime import datetime, timedelta
import random
```

- `datetime` — used to generate realistic timestamps for mock violations and to inject the current year into every page footer.
- `timedelta` — used to subtract hours/minutes from `datetime.now()` so violations appear spread across the past 72 hours.
- `random` — used to randomly assign violation types, plates, cameras, statuses, confidence scores, and severity levels to mock records.

```python
from flask import Flask, render_template
```

- `Flask` — the class that creates the WSGI application object.
- `render_template` — reads a Jinja2 `.html` file from the `templates/` directory, processes all `{{ }}` expressions and `{% %}` tags, and returns a complete HTML string to send to the browser.

### Application factory

```python
app = Flask(__name__)
app.config["SECRET_KEY"] = "tavidm-prototype-dev-key"
```

- `Flask(__name__)` — creates the app. Passing `__name__` tells Flask where to find the `templates/` and `static/` directories relative to this file.
- `SECRET_KEY` — required by Flask for session signing and CSRF protection in Flask-WTF. Since this prototype uses neither, it is present only as good practice. In production this must be a long, random, secret value stored outside source code.

---

### CAMERAS constant

```python
CAMERAS = [
    {
        "id": "cam-01",
        "name": "Inbound Normal Road",
        "location": "Inbound Lane — Normal Road Sector",
        "status": "online",
        "fps": 30,
        "feed_image": "cctv_inbound.svg",
    },
    ...
]
```

A list of Python dictionaries representing the two CCTV cameras in the system.

- `id` — unique string key used as a data attribute in HTML (`data-camera-id`) so JavaScript can identify which camera was clicked.
- `name` — human-readable label shown in the sidebar camera selector and violation records.
- `location` — descriptive string shown below the feed and in violation detail modals.
- `status` — `"online"` or `"offline"`. Drives the green/red dot in the UI and the FPS display in the live monitor.
- `fps` — frames per second, displayed as a badge on the live feed header.
- `feed_image` — filename of the SVG in `static/images/` used as the camera's placeholder feed image.

### VIOLATION_TYPES, PLATE_PREFIXES, STATUSES

```python
VIOLATION_TYPES = ["Red Light Violation", "Speeding", "Wrong Lane", ...]
PLATE_PREFIXES = ["ABC", "XYZ", "TAV", "PHL", "NCR", "MNL"]
STATUSES = ["Pending", "Verified", "Dismissed", "Escalated"]
```

These three lists are used as the population pools for `random.choice()` inside `generate_violations()`.

- `VIOLATION_TYPES` — also passed to the violations and reports templates to populate the filter `<select>` dropdowns, so the options always match the mock data.
- `PLATE_PREFIXES` — simulates Philippine-style plate numbers (prefix + digits + two letters).
- `STATUSES` — the workflow states a violation record can be in.

### _random_plate() helper

```python
def _random_plate():
    return f"{random.choice(PLATE_PREFIXES)} {random.randint(100, 999)} {chr(random.randint(65, 90))}{chr(random.randint(65, 90))}"
```

- `random.choice(PLATE_PREFIXES)` — picks a random prefix like "NCR".
- `random.randint(100, 999)` — generates a 3-digit number.
- `chr(random.randint(65, 90))` — converts a random integer in the ASCII uppercase range (A=65, Z=90) to a letter. Called twice for two suffix letters.
- Result example: `"NCR 452 BX"`

---

### generate_violations(count=48)

```python
def generate_violations(count=48):
    violations = []
    base_time = datetime.now()
    for i in range(1, count + 1):
        vtype = random.choice(VIOLATION_TYPES)
        cam = random.choice(CAMERAS)
        ts = base_time - timedelta(hours=random.randint(0, 72), minutes=random.randint(0, 59))
        violations.append({
            "id": f"VIO-{2025}{i:04d}",
            ...
        })
    violations.sort(key=lambda v: v["timestamp_iso"], reverse=True)
    return violations
```

This function generates `count` (default 48) mock violation records.

- `base_time = datetime.now()` — anchors timestamps relative to when the server starts.
- `timedelta(hours=..., minutes=...)` — subtracts a random offset so violations span the past 72 hours, making the data feel realistic.
- `"id": f"VIO-{2025}{i:04d}"` — creates IDs like `VIO-20250001`. `{i:04d}` zero-pads the integer to 4 digits.
- `"confidence": round(random.uniform(0.72, 0.99), 2)` — AI confidence score between 72% and 99%, rounded to 2 decimal places.
- `"evidence_image": f"evidence_{(i % 6) + 1}.svg"` — cycles through 6 SVG files (`evidence_1.svg` to `evidence_6.svg`) using modulo arithmetic so every record has an evidence image.
- `.sort(key=lambda v: v["timestamp_iso"], reverse=True)` — sorts the list newest-first using ISO 8601 strings (sortable lexicographically) so the most recent violations appear at the top of the dashboard table.

### DASHBOARD_STATS

```python
DASHBOARD_STATS = {
    "total_today": 127,
    "total_week": 842,
    "active_cameras": sum(1 for c in CAMERAS if c["status"] == "online"),
    "total_cameras": len(CAMERAS),
    "pending_review": sum(1 for v in MOCK_VIOLATIONS if v["status"] == "Pending"),
    "avg_confidence": round(sum(v["confidence"] for v in MOCK_VIOLATIONS[:20]) / 20 * 100, 1),
    "trend_today": 12.4,
    "trend_week": -3.2,
}
```

- `sum(1 for c in CAMERAS if c["status"] == "online")` — counts online cameras using a generator expression. More memory-efficient than building a list first.
- `pending_review` — counts violations in the "Pending" state from the generated mock data, so the number is always consistent with the violations page.
- `avg_confidence` — averages confidence scores of the first 20 violations (newest, since the list is sorted descending), multiplied by 100 to convert to a percentage, rounded to 1 decimal place.
- `trend_today: 12.4` and `trend_week: -3.2` — hardcoded percentage changes displayed with up/down arrow indicators. Positive = trend-up (green), negative = trend-down (red).

### VIOLATION_SUMMARY & _VIOLATION_COUNTS

```python
_VIOLATION_COUNTS = [
    ("Red Light Violation", 342, 8.2, "#ef4444"),
    ...
]
_total_violation_count = sum(v[1] for v in _VIOLATION_COUNTS)
VIOLATION_SUMMARY = [
    {
        "type": t,
        "count": c,
        "change": ch,
        "color": col,
        "share": round(c / _total_violation_count * 100, 1),
    }
    for t, c, ch, col in _VIOLATION_COUNTS
]
```

- The underscore prefix on `_VIOLATION_COUNTS` and `_total_violation_count` is a Python convention for "private to this module" — not imported by other files.
- `"share": round(c / _total_violation_count * 100, 1)` — calculates each type's percentage share of total violations. Used in the analytics table "Share" column.
- `"color"` — a hex color string used both in the summary cards' left-border indicator and as `backgroundColor` in the Chart.js doughnut chart.

---

### ANALYTICS_DATA

```python
ANALYTICS_DATA = {
    "daily_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "daily_values": [89, 112, 95, 127, 143, 78, 65],
    "monthly_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    "monthly_values": [1840, 2105, 1987, 2340, 2567, 2210],
    "breakdown_labels": [s["type"] for s in VIOLATION_SUMMARY],
    "breakdown_values": [s["count"] for s in VIOLATION_SUMMARY],
    "kpi": { ... },
}
```

- `breakdown_labels` and `breakdown_values` are derived from `VIOLATION_SUMMARY` using list comprehensions — this keeps the data in sync instead of duplicating it.
- `kpi` contains four key performance indicator values rendered as cards on the analytics page: total violations, detection rate, average response time, and model accuracy.

### REPORT_HISTORY

```python
REPORT_HISTORY = [
    {"id": "RPT-001", "name": "Weekly Violation Summary", "type": "PDF", "date": "2025-06-20", "size": "2.4 MB", "status": "Ready"},
    ...
]
```

Hardcoded list of pre-existing reports shown in the "Download History" table on the Reports page. The `status` field controls whether the download button is enabled (`"Ready"`) or disabled (`"Expired"`).

### USERS

```python
USERS = [
    {"id": 1, "name": "Admin User", "email": "admin@tavidm.local", "role": "Administrator", "status": "Active", "last_login": "2025-06-21 08:30"},
    ...
]
```

Mock user accounts displayed in the Settings page user management table. The `name[:2]|upper` Jinja2 filter extracts the first two letters to render the user avatar initials (`AU`, `JD`, etc.).

### SYSTEM_SETTINGS

```python
SYSTEM_SETTINGS = {
    "site_name": "TAVIDM",
    "timezone": "Asia/Manila",
    "alert_sound": True,
    "auto_export": False,
    "retention_days": 90,
    "confidence_threshold": 75,
    "speed_threshold": 60,
    "red_light_sensitivity": 80,
    "helmet_detection": True,
    "night_mode_enhance": True,
}
```

Default system configuration values rendered into the Settings page form controls — sliders, text inputs, checkboxes, and selects. The JavaScript `settings.js` reads the slider values directly from the rendered HTML `value` attributes.

### DETECTION_BOXES

```python
DETECTION_BOXES = [
    {"label": "Vehicle", "x": 12, "y": 35, "w": 28, "h": 22, "confidence": 0.94},
    {"label": "Red Light", "x": 68, "y": 8, "w": 8, "h": 12, "confidence": 0.98},
    {"label": "Plate", "x": 18, "y": 52, "w": 12, "h": 5, "confidence": 0.87},
]
```

Initial positions for the AI detection bounding boxes on the live monitor feed. Coordinates (`x`, `y`, `w`, `h`) are percentages of the feed container's width/height, so they scale responsively. The JavaScript `live_monitor.js` re-renders these boxes every 2 seconds with slight random drift to simulate a live detection stream.

---

### Context Processor

```python
@app.context_processor
def inject_globals():
    return {
        "app_name": "TAVIDM",
        "app_full_name": "Traffic Violation Detection and Monitoring System",
        "current_year": datetime.now().year,
        "nav_items": [
            {"endpoint": "dashboard", "label": "Dashboard", "icon": "bi-speedometer2"},
            ...
        ],
    }
```

A **context processor** is a Flask feature that automatically injects variables into every template's rendering context. This means every `.html` template can use `{{ app_name }}`, `{{ current_year }}`, and `{{ nav_items }}` without each route passing them individually.

- `"endpoint"` values match the Python function names of routes (e.g., `def dashboard():`). The template uses `url_for(item.endpoint)` to generate the correct URL, and `request.endpoint == item.endpoint` to apply the `active` CSS class to the current page's nav link.
- `current_year` is evaluated at request time, so the footer copyright year is always accurate.

### Routes

```python
@app.route("/")
def dashboard():
    chart_data = {
        "hourly_labels": [f"{h:02d}:00" for h in range(6, 22, 2)],
        "hourly_values": [8, 15, 22, 18, 31, 28, 19, 12],
        ...
    }
    return render_template("dashboard.html", stats=DASHBOARD_STATS, ...)
```

Each `@app.route()` decorator maps a URL path to a Python function. When a browser requests that path, Flask calls the function and returns its result as the HTTP response body.

- `@app.route("/")` — the root path. Maps to the `dashboard()` function.
- `chart_data` is built locally inside the route rather than at module level because it conceptually belongs to the dashboard page only.
- `[f"{h:02d}:00" for h in range(6, 22, 2)]` — generates `["06:00", "08:00", ..., "20:00"]`: 8 two-hour intervals using `range(6, 22, 2)` (start 6, stop 22, step 2) and `{h:02d}` zero-pad format.

```python
@app.route("/live-monitor")
def live_monitor():
    return render_template("live_monitor.html", cameras=CAMERAS, default_camera=CAMERAS[0], detection_boxes=DETECTION_BOXES)
```

- `CAMERAS[0]` — passes the first camera as the default so the page always loads with a valid feed on first render.

```python
if __name__ == "__main__":
    app.run(debug=True, port=5000)
```

- `if __name__ == "__main__"` — Python idiom that ensures `app.run()` only executes when the script is run directly (`python app.py`), not when it is imported as a module.
- `debug=True` — enables Flask's development mode: auto-reloader and interactive error page.
- `port=5000` — binds the server to TCP port 5000 on localhost.

---

## 3. requirements.txt — Dependencies

```
Flask==3.0.3
Werkzeug==3.0.3
```

- **Pinned versions** (`==`) guarantee reproducible installs. If you share this project with another developer or deploy it, they get exactly the same library versions.
- `Flask==3.0.3` — the micro web framework. Provides routing (`@app.route`), template rendering (`render_template`), and the development server.
- `Werkzeug==3.0.3` — Flask's underlying WSGI toolkit. Handles HTTP request/response objects, URL routing internals, and the development server's request dispatcher. Flask lists it as a direct dependency, but it is pinned here to prevent accidental upgrades.

---

## 4. templates/base.html — Master Layout

Every page extends this file using Jinja2 template inheritance. It defines the complete page shell that never changes between pages.

### `<head>` section

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0">
```
Makes the page responsive — tells the browser to use the device's actual width instead of a virtual 980px viewport.

```html
<title>{% block title %}Dashboard{% endblock %} | {{ app_name }}</title>
```
`{% block title %}` is a **Jinja2 block**. Child templates override it with their own title. If not overridden, it defaults to "Dashboard". `{{ app_name }}` comes from the context processor.

```html
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
```
Loads Bootstrap 5 CSS grid, components, and utilities, plus the Bootstrap Icons icon font — all from jsDelivr CDN. These are present on every page since they are in the base template.

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
```
Loads the Inter font from Google Fonts. `display=swap` tells the browser to show a fallback font while Inter loads, preventing invisible text.

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
```
`url_for('static', filename='...')` generates the correct URL for the file regardless of where Flask is mounted. This is more robust than hardcoding `/static/css/style.css`.

### Sidebar

```html
{% for item in nav_items %}
<li class="nav-item">
    <a class="nav-link {% if request.endpoint == item.endpoint %}active{% endif %}"
       href="{{ url_for(item.endpoint) }}">
```

- `request.endpoint` — Flask injects the `request` object globally into templates. `endpoint` is the name of the currently active route function.
- `{% if request.endpoint == item.endpoint %}active{% endif %}` — conditionally applies the `active` CSS class to highlight the current page in the sidebar.
- `url_for(item.endpoint)` — dynamically generates the URL for each nav item.

### Top Navigation

```html
<button class="btn btn-icon sidebar-toggle d-lg-none" id="sidebarToggle">
```
`d-lg-none` is a Bootstrap utility class that hides this element on large screens (≥992px) and shows it on mobile. The JavaScript `main.js` listens for clicks on this button to toggle the sidebar.

```html
<span class="notification-badge">3</span>
```
A hardcoded badge showing 3 notifications — purely decorative in the prototype.

### Content blocks

```html
<main class="page-content">
    {% block content %}{% endblock %}
</main>
```
The `{% block content %}` is where each child template injects its unique page body.

### Scripts

```html
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script src="{{ url_for('static', filename='js/main.js') }}"></script>
{% block extra_js %}{% endblock %}
```

- Bootstrap's JS bundle includes Popper.js, which powers dropdowns and modals.
- `main.js` is loaded on every page after Bootstrap so it can use `bootstrap.Toast`.
- `{% block extra_js %}` allows child templates to inject page-specific scripts (Chart.js, violations data, etc.) at the end of the body.

---

## 5. templates/dashboard.html

Extends `base.html`. Receives `stats`, `recent_violations`, `violation_summary`, and `chart_data` from the `dashboard()` route.

### Stat Cards

```html
<span class="stat-value">{{ stats.total_today }}</span>
<span class="stat-trend {% if stats.trend_today >= 0 %}trend-up{% else %}trend-down{% endif %}">
    <i class="bi bi-arrow-{% if stats.trend_today >= 0 %}up{% else %}down{% endif %}-short"></i>
    {{ stats.trend_today|abs }}% vs yesterday
</span>
```

- `{{ stats.trend_today|abs }}` — the Jinja2 `abs` filter returns the absolute value, so `-3.2` displays as `3.2%`. The direction (up/down) is conveyed by the color class and icon.
- `trend-up` maps to `color: var(--success)` (green); `trend-down` maps to `color: var(--danger)` (red).

### Charts

```html
<canvas id="hourlyChart"></canvas>
```

An HTML5 `<canvas>` element is a blank drawing surface. Chart.js finds this element by its `id` and renders an SVG-like chart directly onto it using the Canvas 2D API.

```html
{% block extra_js %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script>
    window.TAVIDM_CHART_DATA = {{ chart_data | tojson }};
</script>
<script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
{% endblock %}
```

- Chart.js is loaded only on the dashboard (and analytics) page — not globally — to avoid loading a ~200KB library on pages that don't use it.
- `{{ chart_data | tojson }}` — the Jinja2 `tojson` filter converts the Python `chart_data` dictionary to a JSON string. Jinja2 also escapes it safely so it cannot break out of the `<script>` tag.
- `window.TAVIDM_CHART_DATA` — attaches the data to the global `window` object so `dashboard.js` can access it without needing an AJAX call.

### Violation Summary Cards

```html
{% for item in violation_summary %}
<div class="summary-indicator" style="background-color: {{ item.color }};"></div>
```

The colored left-border indicator on each summary card is set via an inline `style` attribute using the hex color from `VIOLATION_SUMMARY`. This is the only place inline styles are used — necessary because the color values are dynamic data, not static CSS classes.

### Recent Violations Table

```html
<td><code>{{ v.id }}</code></td>
<td><span class="plate-badge">{{ v.plate }}</span></td>
<td>
    <div class="confidence-bar-wrap">
        <div class="confidence-bar" style="width: {{ (v.confidence * 100)|int }}%;"></div>
        <span>{{ (v.confidence * 100)|int }}%</span>
    </div>
</td>
```

- `{{ (v.confidence * 100)|int }}` — multiplies the decimal (e.g. `0.87`) by 100, then the `int` Jinja2 filter truncates to an integer (87). Used both for the percentage label and for the confidence bar's `width` inline style.
- `status-{{ v.status|lower }}` — dynamically generates CSS class names like `status-pending`, `status-verified`, etc. The `lower` filter converts the status to lowercase to match the CSS class naming convention.

---

## 6. templates/live_monitor.html

Receives `cameras`, `default_camera`, and `detection_boxes` from the `live_monitor()` route.

### Detection Boxes (Server-Rendered Initial State)

```html
{% for box in detection_boxes %}
<div class="detection-box"
     data-label="{{ box.label }}"
     style="left: {{ box.x }}%; top: {{ box.y }}%; width: {{ box.w }}%; height: {{ box.h }}%;">
    <span class="detection-label">{{ box.label }} {{ (box.confidence * 100)|int }}%</span>
</div>
{% endfor %}
```

The initial bounding boxes are rendered server-side. Once `live_monitor.js` loads, it replaces the overlay content every 2 seconds with slightly shifted versions to simulate movement.

### Camera Selector Buttons

```html
<button class="camera-item" 
        data-camera-id="{{ cam.id }}"
        data-camera-name="{{ cam.name }}"
        data-camera-feed="{{ cam.feed_image }}">
```

All camera data is embedded as `data-*` HTML attributes on the button element. `live_monitor.js` reads these attributes when a camera is clicked so it doesn't need to make any server requests to switch cameras.

---

## 7. templates/violations.html

Receives `violations`, `violation_types`, `cameras`, and `statuses`.

### Data Bridge to JavaScript

```html
{% block extra_js %}
<script>
    window.TAVIDM_VIOLATIONS = {{ violations | tojson }};
</script>
<script src="{{ url_for('static', filename='js/violations.js') }}"></script>
{% endblock %}
```

The full violations array is serialized into the page as a JavaScript global variable. This is the key pattern that enables client-side filtering, sorting, and pagination without any AJAX calls — all 48 records are available in the browser's memory the moment the page loads.

### Server-Rendered + Client-Replaced Table Rows

The `<tbody>` is initially populated by Jinja2 with all violation rows. When `violations.js` calls `applyFilters()` on load, it re-renders the table from the `window.TAVIDM_VIOLATIONS` array, effectively replacing the server-rendered content with the client-side paginated version. This ensures the page works even if JavaScript is disabled (you'd see all records), while JavaScript users get pagination and filtering.

### Modals

```html
<div class="modal fade" id="detailModal" ...>
    <div class="modal-body" id="detailModalBody"></div>
```

The modal body is intentionally empty in the HTML. `violations.js` populates it dynamically using `innerHTML` when a row's "View Details" button is clicked. This avoids rendering 48 hidden modal bodies in the page HTML.

---

## 8. templates/analytics.html

Receives `analytics` and `violation_summary`.

### KPI Cards

```html
<span class="kpi-value">{{ analytics.kpi.detection_rate }}%</span>
```

Simple value interpolation. The `kpi` sub-dictionary is accessed with dot notation in Jinja2. These four KPI cards are the first thing visible on the analytics page, giving operators a quick high-level read before scrolling to charts.

### Chart Canvas IDs

Three canvases: `dailyChart` (bar), `monthlyChart` (line), `breakdownChart` (doughnut). Each is targeted by `analytics.js` using `document.getElementById()`.

### Analytics Data Bridge

```html
<script>
    window.TAVIDM_ANALYTICS = {{ analytics | tojson }};
</script>
```

Same pattern as violations — the Python dict is serialized to JSON and attached to `window` for `analytics.js` to consume.

---

## 9. templates/reports.html

Receives `report_history` and `violation_types`.

### Report Generator Form

```html
<input type="date" class="form-control" id="dateFrom" value="2025-06-01">
```

HTML5 date inputs render a native date picker in the browser. The value is pre-filled with a hardcoded date. `reports.js` reads these values to display a formatted date range in the preview panel.

### Conditional Download Button

```html
<button class="btn btn-sm btn-outline-primary btn-download-report"
        {% if r.status != 'Ready' %}disabled{% endif %}>
```

Jinja2 conditionally adds the `disabled` HTML attribute to reports with `"Expired"` status. The JavaScript event handler also checks `btn.disabled` before firing to double-guard against interaction.

---

## 10. templates/settings.html

Receives `cameras`, `users`, and `settings`.

### Slider Pre-fill

```html
<input type="range" class="form-range" id="confidenceThreshold"
       min="50" max="99" value="{{ settings.confidence_threshold }}">
<span class="threshold-value" id="confidenceValue">{{ settings.confidence_threshold }}%</span>
```

The slider's `value` is set from the Python `settings` dict. The adjacent `<span>` is also pre-populated with the same value so it displays correctly on page load, before any JavaScript interaction.

### User Avatar Initials

```html
<div class="user-avatar-sm">{{ user.name[:2]|upper }}</div>
```

`user.name[:2]` — Python/Jinja2 slice syntax extracts the first 2 characters. `|upper` converts to uppercase. Result: `"Admin User"` → `"AD"`, `"Juan Dela Cruz"` → `"JU"`.

---

## 11. static/css/style.css

The entire application's custom styling is in this one file. No CSS preprocessors (Sass/Less) are used.

### CSS Custom Properties (Variables)

```css
:root {
    --sidebar-width: 260px;
    --sidebar-bg: #0f172a;
    --primary: #2563eb;
    --body-bg: #f1f5f9;
    --radius: 12px;
    --transition: 0.2s ease;
    ...
}
```

`--variable-name` syntax defines CSS custom properties on the `:root` pseudo-element (equivalent to `<html>`), making them globally available. This is the CSS equivalent of a design token system — change `--primary` once and every element using `var(--primary)` updates automatically. Enables easy theming.

### Layout: Flexbox Sidebar + Main Content

```css
.app-wrapper { display: flex; min-height: 100vh; }
.sidebar { position: fixed; width: var(--sidebar-width); height: 100vh; }
.main-content { flex: 1; margin-left: var(--sidebar-width); }
```

The outer wrapper is a flex row. The sidebar is `position: fixed` — it stays in place while the main content scrolls. `main-content` uses `flex: 1` to fill all remaining horizontal space, and `margin-left` equal to `--sidebar-width` to avoid being hidden behind the fixed sidebar.

### Responsive Breakpoint

```css
@media (max-width: 991.98px) {
    .sidebar { transform: translateX(-100%); }
    .sidebar.show { transform: translateX(0); }
    .main-content { margin-left: 0; }
}
```

On mobile (below Bootstrap's `lg` breakpoint of 992px): the sidebar slides off-screen to the left using `transform: translateX(-100%)`. When JavaScript adds the `show` class, it slides back in. The main content loses its left margin so it fills the full viewport.

### Detection Boxes

```css
.detection-box { position: absolute; border: 2px solid #22c55e; }
.detection-box[data-label="Red Light"] { border-color: #ef4444; }
.detection-box[data-label="Plate"] { border-color: #3b82f6; }
```

Attribute selectors (`[data-label="..."]`) change the border color per detection type: green for vehicles, red for red lights, blue for license plates. This is driven by the `data-label` attribute set in both the Jinja2 template and the `live_monitor.js` re-render function.

### CSS Animation

```css
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
.live-indicator .pulse { animation: pulse 1.5s infinite; }
```

The LIVE badge's red dot fades in and out continuously using a CSS keyframe animation — no JavaScript required. The dot goes from full opacity → half opacity → full opacity over 1.5 seconds, infinitely.

---

## 12. static/js/main.js

Loaded on every page (via `base.html`). Provides shared utilities used across all other scripts.

### IIFE Pattern

```javascript
(function () {
    "use strict";
    // ... all code ...
})();
```

An **Immediately Invoked Function Expression (IIFE)** wraps all code. This creates a private scope — variables declared inside cannot leak to the global `window` object and cannot conflict with Bootstrap or Chart.js. `"use strict"` enables strict mode, which catches common JavaScript errors like undeclared variables.

### Sidebar Toggle

```javascript
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebarOverlay = document.getElementById("sidebarOverlay");

function toggleSidebar() {
    sidebar.classList.toggle("show");
    sidebarOverlay.classList.toggle("show");
}
```

`classList.toggle("show")` adds the class if absent, removes it if present. The CSS media query handles the actual visual transition. The overlay is a semi-transparent dark div that covers the main content — clicking it closes the sidebar.

```javascript
window.addEventListener("resize", function () {
    if (window.innerWidth >= 992) { closeSidebar(); }
});
```

If the user rotates their phone to landscape or resizes a browser window past 992px, the sidebar closes automatically so it doesn't overlay the fixed-position desktop layout.

### Global Toast Notification System

```javascript
window.showToast = function (title, message, type) {
```

`window.showToast` is intentionally placed on `window` (global scope) so every other script file can call it without importing anything. This is the one exception to the IIFE's isolation.

```javascript
const toastId = "toast-" + Date.now();
```

`Date.now()` returns milliseconds since the Unix epoch — a fast, practically unique ID for each toast element. Multiple toasts can exist simultaneously without ID conflicts.

```javascript
const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
toast.show();
toastEl.addEventListener("hidden.bs.toast", function () { toastEl.remove(); });
```

`new bootstrap.Toast()` instantiates Bootstrap's Toast component. `{ delay: 4000 }` auto-dismisses after 4 seconds. The `hidden.bs.toast` event fires after the hide animation completes, at which point the element is removed from the DOM to prevent accumulation.

### formatTimestamp()

```javascript
window.formatTimestamp = function () {
    return now.toLocaleString("en-PH", { ... hour12: false });
};
```

`"en-PH"` is the Philippines locale. `hour12: false` uses 24-hour clock format. The result is used by `live_monitor.js` to display a live timestamp on the CCTV feed.

---

## 13. static/js/dashboard.js

Renders two Chart.js charts on the dashboard page.

### Guard Clause

```javascript
const data = window.TAVIDM_CHART_DATA;
if (!data || typeof Chart === "undefined") return;
```

If the chart data wasn't injected (meaning this script ran on the wrong page) or Chart.js CDN failed to load, the script exits immediately instead of throwing a TypeError.

### chartDefaults Object

```javascript
const chartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
};
```

`responsive: true` makes charts resize with their container. `maintainAspectRatio: false` lets the container's CSS height control the chart height (the `.chart-container` CSS class sets `height: 280px`). `legend: { display: false }` hides the built-in legend to keep the UI clean — the violation type is evident from context.

### Hourly Line Chart

```javascript
new Chart(hourlyCtx, {
    type: "line",
    data: {
        datasets: [{
            borderColor: "#2563eb",
            backgroundColor: "rgba(37, 99, 235, 0.1)",
            fill: true,
            tension: 0.4,
            pointRadius: 4,
        }],
    },
    options: { ...chartDefaults, scales: { y: { beginAtZero: true } } },
});
```

- `fill: true` — fills the area under the line with a semi-transparent blue.
- `tension: 0.4` — Bezier curve smoothing (0 = straight lines, 1 = very curved).
- `...chartDefaults` — JavaScript spread operator merges the shared defaults into the options object.

### Weekly Bar Chart

```javascript
backgroundColor: ["#2563eb", "#3b82f6", "#60a5fa", "#2563eb", "#1d4ed8", "#93c5fd", "#bfdbfe"],
borderRadius: 6,
borderSkipped: false,
```

- An array of 7 colors (one per day) creates a gradient-like effect across the bars.
- `borderRadius: 6` — rounds the top corners of each bar.
- `borderSkipped: false` — applies border radius to all four corners, not just the top.

---

## 14. static/js/live_monitor.js

Simulates a live CCTV monitoring experience using `setInterval` timers.

### Detection Box Animation

```javascript
function simulateDetection() {
    const boxes = MOCK_BOXES.map(function (box) {
        return {
            ...box,
            x: Math.max(5, Math.min(75, box.x + (Math.random() - 0.5) * 4)),
            y: Math.max(5, Math.min(70, box.y + (Math.random() - 0.5) * 3)),
            confidence: Math.min(0.99, Math.max(0.75, box.confidence + (Math.random() - 0.5) * 0.05)),
        };
    });
    renderBoxes(boxes);
}
simulationInterval = setInterval(simulateDetection, 2000);
```

- `(Math.random() - 0.5) * 4` — produces a random delta between -2 and +2. Subtracting 0.5 from `Math.random()` (which is 0–1) centers the range around 0.
- `Math.max(5, Math.min(75, ...))` — clamps the x-position between 5% and 75% so boxes stay within the feed container.
- `setInterval(..., 2000)` — fires every 2 seconds, re-rendering all boxes with slightly new positions. Combined with the CSS `border` color and the label, this creates a convincing "AI tracking" effect.

### Alert Feed

```javascript
function addRandomAlert() {
    const item = document.createElement("div");
    item.innerHTML = '...';
    alertFeed.insertBefore(item, alertFeed.firstChild);
    while (alertFeed.children.length > 8) {
        alertFeed.removeChild(alertFeed.lastChild);
    }
    showToast("Violation Detected", alert.title + " — " + detail, "danger");
}
setInterval(addRandomAlert, 12000);
```

- `insertBefore(item, alertFeed.firstChild)` — prepends the new alert so newest items appear at the top.
- The `while` loop caps the feed at 8 items by removing from the bottom — prevents unbounded DOM growth.
- `showToast()` is the global function from `main.js`, demonstrating cross-script utility sharing.

### Camera Switcher

```javascript
cameraList.addEventListener("click", function (e) {
    const btn = e.target.closest(".camera-item");
```

**Event delegation** — a single click listener on the parent `cameraList` div handles clicks on any camera button. `e.target.closest(".camera-item")` traverses up the DOM from the actual clicked element to find the button, handling clicks on child elements (icon, text) correctly.

---

## 15. static/js/violations.js

The most complex JavaScript file in the project. Implements a complete client-side data grid.

### State Variables

```javascript
let filtered = [...allViolations];
let currentPage = 1;
let pageSize = 10;
let sortField = "timestamp";
let sortDir = "desc";
```

`[...allViolations]` — the spread operator creates a shallow copy of the array so filtering operations modify `filtered` without mutating the original `allViolations` source.

### applyFilters()

```javascript
filtered = allViolations.filter(function (v) {
    const matchQuery = !query || [v.id, v.type, v.plate, v.camera_name, v.location, v.status]
        .some(function (f) { return String(f).toLowerCase().includes(query); });
    return matchQuery && (!type || v.type === type) && (!camera || v.camera_name === camera) && (!status || v.status === status);
});
```

- `Array.filter()` creates a new array with only the elements that pass the callback test.
- `Array.some()` returns `true` if at least one element satisfies the predicate — used to check if the search query matches any of the violation's searchable fields.
- `!type || v.type === type` — if no filter is selected (`type === ""`), `!type` is truthy so the check short-circuits to `true`, including all records. If a filter is selected, it must match exactly.

### applySort()

```javascript
filtered.sort(function (a, b) {
    if (valA < valB) return sortDir === "asc" ? -1 : 1;
    if (valA > valB) return sortDir === "asc" ? 1 : -1;
    return 0;
});
```

JavaScript's `Array.sort()` takes a comparator function. Returning `-1` means `a` comes before `b`, `1` means `b` before `a`, `0` means equal. Toggling `sortDir` between `"asc"` and `"desc"` inverts the comparator.

### renderPagination()

```javascript
if (totalPages > 7 && Math.abs(i - currentPage) > 2 && i !== 1 && i !== totalPages) {
    if (i === 2 || i === totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
    continue;
}
```

This logic implements the "..." ellipsis pattern for long pagination controls: always show page 1 and the last page, show 2 pages around the current page, and replace the gaps with `...`.

### showDetail() and showEvidence()

```javascript
function showDetail(v) {
    const body = document.getElementById("detailModalBody");
    body.innerHTML = '<div class="detail-grid">' + detailField("Violation ID", v.id) + ... + '</div>';
    detailModal.show();
}
```

The modal HTML is built by string concatenation. `detailModal` is a `bootstrap.Modal` instance created at the top of the script. Calling `.show()` triggers Bootstrap's modal animation.

### Row Click Delegation

```javascript
tbody?.addEventListener("click", function (e) {
    const row = e.target.closest("tr");
    let v;
    try { v = JSON.parse(row.dataset.violation); } catch (err) { return; }
    if (e.target.closest(".btn-view-detail")) showDetail(v);
    if (e.target.closest(".btn-view-evidence")) showEvidence(v);
});
```

- `?.` — optional chaining. If `tbody` is null (script running on wrong page), it does nothing instead of throwing.
- `row.dataset.violation` — reads the `data-violation` attribute which contains the JSON-serialized violation object embedded by Jinja2.
- `JSON.parse()` is wrapped in try/catch to handle any edge cases where the data attribute might be malformed.

---

## 16. static/js/analytics.js

Renders three Chart.js charts on the analytics page.

### Color Array

```javascript
const colors = ["#ef4444", "#f97316", "#eab308", "#8b5cf6", "#06b6d4", "#2563eb", "#16a34a"];
```

These 7 colors are shared between the doughnut chart's `backgroundColor` and match the colors defined in `VIOLATION_SUMMARY` in `app.py`. They are intentionally aligned for visual consistency.

### Doughnut Chart

```javascript
new Chart(breakdownCtx, {
    type: "doughnut",
    options: {
        plugins: {
            legend: {
                position: "bottom",
                labels: { padding: 16, usePointStyle: true, pointStyle: "circle" },
            },
        },
    },
});
```

The doughnut chart is the only chart that shows its legend (unlike the bar and line charts which hide it). `usePointStyle: true` + `pointStyle: "circle"` renders circular colored dots instead of the default square legend indicators, matching the design system's aesthetic.

---

## 17. static/js/reports.js

Simulates report generation with visual feedback.

### Date Range Formatter

```javascript
function formatDateRange() {
    const from = new Date(dateFrom.value);
    const to = new Date(dateTo.value);
    const opts = { month: "short", day: "numeric", year: "numeric" };
    previewRange.textContent = from.toLocaleDateString("en-US", opts) + " \u2013 " + to.toLocaleDateString("en-US", opts);
}
```

`new Date(dateFrom.value)` — parses the ISO date string from the `<input type="date">` element into a JavaScript Date object. `toLocaleDateString("en-US", opts)` formats it as "Jun 1, 2025". `\u2013` is the Unicode en-dash character (–) used in date ranges.

### addToHistory()

```javascript
function addToHistory(name, type) {
    const id = "RPT-" + String(Math.floor(Math.random() * 900) + 100);
    const row = document.createElement("tr");
    row.innerHTML = "...";
    tbody.insertBefore(row, tbody.firstChild);
}
```

Creates a new `<tr>` element and prepends it to the history table, simulating the effect of a real report being generated and added to the database. `Math.floor(Math.random() * 900) + 100` generates a 3-digit random number (100–999) for the report ID.

---

## 18. static/js/settings.js

The simplest JavaScript file — handles live slider value display and demo toast feedback.

### Slider Live Update

```javascript
sliders.forEach(function (s) {
    const input = document.getElementById(s.input);
    const display = document.getElementById(s.display);
    input.addEventListener("input", function () {
        display.textContent = this.value + s.suffix;
    });
});
```

The `"input"` event fires on every movement of the range slider (unlike `"change"` which only fires on release). `this.value` is the current slider value. `s.suffix` is either `"%"` or `" km/h"` defined in the sliders config array. This gives instant visual feedback as the user drags the slider.

---

## 19. static/images/ — SVG Assets

All images are SVG files — vector graphics that scale without pixelation and have tiny file sizes compared to raster images.

| File | Used By | Purpose |
|---|---|---|
| `camera_thumb.svg` | `live_monitor.html` | Small thumbnail shown in camera selector list |
| `cctv_inbound.svg` | `live_monitor.html`, `app.py` | Placeholder feed for Cam-01 (inbound lane) |
| `cctv_outbound.svg` | `live_monitor.html`, `app.py` | Placeholder feed for Cam-02 (outbound lane) |
| `cctv_feed.svg` | Available for additional cameras | Generic CCTV feed placeholder |
| `evidence_1.svg` … `evidence_6.svg` | `violations.js` | Placeholder evidence capture images, cycled with modulo in `generate_violations()` |

---

## 20. Data Flow Diagram

```
python app.py
     │
     ├── generate_violations(48)        ← random data created ONCE at startup
     ├── DASHBOARD_STATS computed        ← derived from MOCK_VIOLATIONS
     ├── VIOLATION_SUMMARY computed      ← derived from _VIOLATION_COUNTS
     └── ANALYTICS_DATA assembled        ← references VIOLATION_SUMMARY

Browser: GET /
     │
Flask: dashboard()
     ├── builds chart_data (hourly + weekly labels/values)
     └── render_template("dashboard.html",
             stats=DASHBOARD_STATS,
             recent_violations=MOCK_VIOLATIONS[:8],
             violation_summary=VIOLATION_SUMMARY,
             chart_data=chart_data)
               │
               ▼
     Jinja2 renders HTML:
       - extends base.html (sidebar, navbar, footer)
       - injects stats into stat card HTML
       - loops violation_summary into summary cards
       - loops recent_violations[:8] into table rows
       - serializes chart_data → window.TAVIDM_CHART_DATA via tojson
               │
               ▼
     HTML + CSS sent to browser
               │
               ▼
     Browser executes JS:
       - main.js: sidebar toggle, toast system
       - dashboard.js: reads window.TAVIDM_CHART_DATA → Chart.js renders canvases

Browser: GET /violations
     │
Flask: violations()
     └── render_template("violations.html",
             violations=MOCK_VIOLATIONS,     ← all 48 records
             violation_types=VIOLATION_TYPES,
             cameras=CAMERAS,
             statuses=STATUSES)
               │
               ▼
     HTML: all 48 rows rendered server-side into <tbody>
     JS: window.TAVIDM_VIOLATIONS = [48 objects via tojson]
     violations.js:
       - applyFilters() called on load → renders page 1 (10 rows)
       - user interactions (search/filter/sort/paginate) → re-renders from window.TAVIDM_VIOLATIONS
       - no server round-trips after initial page load
```

---

## 21. External CDN Dependencies

These libraries are loaded from the internet by the browser on every page load. No npm or node_modules involved.

| Library | Version | CDN URL | Pages Used | Role |
|---|---|---|---|---|
| Bootstrap CSS | 5.3.3 | jsDelivr | All | Grid system, form controls, badges, tables, modals, dropdowns, utilities |
| Bootstrap JS Bundle | 5.3.3 | jsDelivr | All | Modal, Toast, Dropdown components (includes Popper.js) |
| Bootstrap Icons | 1.11.3 | jsDelivr | All | Icon font — `bi-*` classes render SVG icons as font glyphs |
| Inter (Google Fonts) | — | fonts.googleapis.com | All | Primary UI typeface: weights 400, 500, 600, 700 |
| Chart.js | 4.4.3 | jsDelivr | Dashboard, Analytics | Canvas-based charting library for line, bar, and doughnut charts |

### Why CDN instead of bundled assets?

For a prototype with no build pipeline, CDN loading:
- requires zero configuration
- leverages browser caching (if the user has visited any other site using the same CDN URLs, the files are already cached)
- keeps the repository small

For production, these should be bundled and self-hosted using a build tool like Vite or webpack to eliminate the external network dependency.

---

*Document generated for TAVIDM v1.0 — BSIT Capstone Project Prototype.*
