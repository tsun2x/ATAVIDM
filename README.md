# Hermosa Connect — v0.1 Prototype

**Status:** Prototype / Proof-of-Concept  
**Purpose:** Interactive UI/UX demonstration for Hermosa Connect Reverse Pitch 2026  
**Data:** Simulated / Mock — NO real CCTV, CV, or traffic analysis  

---

## ⚠️ Important Notice

This is a **prototype** that demonstrates the **Hermosa Connect** user experience.

All data is **simulated** or **pre-computed mock data**. No real:

- CCTV feeds
- RTSP streams
- Computer vision (YOLO, ByteTrack)
- Congestion detection algorithms
- Traffic-light integration
- Production hotspot analysis

is implemented or used.

---

## What This Prototype Demonstrates

| Feature | Status |
|---------|--------|
| Interactive Leaflet map | ✅ Implemented |
| Camera/intersection markers | ✅ Implemented |
| Color-coded traffic states | ✅ Simulated |
| Drill-down panel | ✅ Implemented |
| Congestion event lifecycle | ✅ Simulated |
| Daily history view | ✅ Mock data |
| Hotspot visualization | ✅ Pre-computed mock |
| Camera management UI | ✅ Mock (add/edit only) |
| Simulation engine | ✅ Scenario-based |
| Demo feed placeholder | ✅ Static placeholder |

---

## How to Run

### Prerequisites

- Python 3.8+
- Flask 3.0+

### Installation

```bash
cd "C:\Users\AMD\Desktop\side quest\tavidm-side-quest"
pip install -r requirements.txt
```

### Run

```bash
cd "C:\Users\AMD\Desktop\side quest\tavidm-side-quest"
python -m app.main
```

Or:

```bash
python app/main.py
```

### Access

Open your browser to: [http://localhost:5001](http://localhost:5001)

---

## Project Structure

```
tavidm-side-quest/
├── app/
│   ├── main.py              # Flask application with routes
│   ├── templates/
│   │   ├── base.html        # Base template with header/footer
│   │   ├── dashboard.html   # Main dashboard with map + simulation controls
│   │   ├── history.html     # Daily history view
│   │   └── config.html      # Camera management view
│   └── static/
│       ├── css/
│       │   └── style.css    # Map marker styles
│       └── js/
│           ├── mock_data.js     # Data loading utilities
│           ├── simulator.js     # Simulation engine
│           ├── map_ui.js        # Leaflet map + markers
│           ├── drill_down.js    # Drill-down panel interactions
│           ├── history_ui.js    # History view + timeline chart
│           └── config_ui.js     # Camera management form
├── data/
│   ├── mock_cameras.json       # Demo camera data
│   ├── mock_clusters.json      # Demo intersection clusters
│   ├── mock_hotspots.json      # Pre-computed demo hotspots
│   ├── mock_events.json        # Demo congestion events
│   └── scenarios/
│       ├── rush_hour.json      # Morning rush hour scenario
│       ├── accident_event.json # Simulated accident spread
│       └── clear_day.json      # Normal traffic day
├── requirements.txt
└── README.md
```

---

## Simulation Scenarios

The prototype includes three predefined scenarios:

1. **Rush Hour** — Typical morning peak with congestion that builds and clears
2. **Accident Event** — Localized incident causing congestion to spread across cameras
3. **Clear Day** — Normal traffic with only brief state changes

Use the simulation controls in the dashboard header to play, pause, reset, and switch scenarios.

---

## Demo Locations

The prototype uses **illustrative locations in Zamboanga City**. These are:

- **Demo data only** — not verified against actual city CCTV infrastructure
- **Not real camera placements** — used for UI demonstration purposes

| Camera ID | Name | Direction |
|-----------|------|-----------|
| CAM-001 | R.T. Lim Blvd @ City Hall | Eastbound |
| CAM-002 | R.T. Lim Blvd @ City Hall | Westbound |
| CAM-003 | Veterans Ave @ Plaza Pershing | Eastbound |
| CAM-004 | Veterans Ave @ Plaza Pershing | Westbound |
| CAM-005 | Mayor Ramon Cañelares Ave | Northbound |

---

## What Is NOT Implemented (Future Work)

| Component | Future Implementation |
|-----------|----------------------|
| Real CCTV/RTSP ingestion | Connect to Zamboanga City traffic management RTSP streams |
| Computer vision detection | YOLO-based vehicle detection using TAVIDM's engine |
| Object tracking | ByteTrack integration |
| Congestion detection algorithm | State machine + signal analysis (see Design Review HC-DR-001) |
| Traffic-light integration | Correlate with signal phase data |
| Production hotspot analysis | Recurrence analysis over historical events |
| Authentication | City traffic management portal integration |
| User roles | Operator vs. admin permissions |
| Production deployment | Docker containerization |

---

## Integration Points (Future)

The prototype is structured to enable future integration:

```typescript
// Future: Replace mock data with real API calls
const cameras = await fetch('/api/cameras');  // Currently returns mock JSON

// Future: Replace simulation with real CV processing
window.Simulator.on('stateChange', updateMap);  // Currently simulated

// Future: WebSocket for live updates
// ws.onmessage = (event) => updateCameraStates(JSON.parse(event.data));
```

---

## Acceptance Criteria Verification

| AC # | Criterion | Status |
|------|-----------|--------|
| AC-01 | Map loads with camera and cluster markers | ✅ Verified |
| AC-02 | Markers are color-coded by simulated traffic state | ✅ Verified |
| AC-03 | Clicking a camera opens the drill-down panel | ✅ Verified |
| AC-04 | Clicking a cluster shows its associated cameras and states | ✅ Verified |
| AC-05 | Hovering a marker shows state + duration | ✅ Verified (via Leaflet popup) |
| AC-06 | Drill-down shows location, cameras, states, aggregated mock status | ✅ Verified |
| AC-07 | Panel shows duration, severity, start time for congestion | ✅ Verified |
| AC-08 | "View Demo Feed" displays a static demo placeholder | ✅ Verified |
| AC-09 | "View History" opens daily history | ✅ Verified |
| AC-10 | Simulation has Play/Pause/Reset | ✅ Verified |
| AC-11 | Simulation changes marker states | ✅ Verified |
| AC-12 | At least 3 scenario presets exist | ✅ Verified (Rush Hour, Accident, Clear Day) |
| AC-13 | Simulation time changes according to simulation speed | ✅ Verified |
| AC-14 | History shows a timeline | ✅ Verified (Chart.js bar chart) |
| AC-15 | History lists events with duration/severity | ✅ Verified |
| AC-16 | Clicking an event highlights the corresponding location | ✅ Verified |
| AC-17 | Hotspot panel shows ranked demo hotspots | ✅ Verified |
| AC-18 | Hotspots are represented on the map | ✅ Verified |
| AC-19 | Clicking a hotspot highlights it and shows its information | ✅ Verified |
| AC-20 | Camera Management shows demo cameras | ✅ Verified |
| AC-21 | Add/Edit Camera supports name, location, direction | ✅ Verified |
| AC-22 | Test Connection shows a simulated result | ✅ Verified |
| AC-23 | All mock information visibly labeled as DEMO DATA | ✅ Verified |

---

## License

This is a prototype for the Hermosa Connect Reverse Pitch 2026. All mock data is illustrative.
