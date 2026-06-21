/**
 * TAVIDM - Live Monitor Simulation
 */

(function () {
    "use strict";

    const feedTimestamp = document.getElementById("feedTimestamp");
    const alertFeed = document.getElementById("alertFeed");
    const detectionOverlay = document.getElementById("detectionOverlay");
    const activeDetections = document.getElementById("activeDetections");
    const cameraList = document.getElementById("cameraList");

    const MOCK_ALERTS = [
        { type: "critical", icon: "bi-exclamation-octagon", title: "Red Light Violation", detail: "NCR 321 PQ detected", confidence: 97 },
        { type: "warning", icon: "bi-speedometer2", title: "Speeding", detail: "MNL 654 RS · 68 km/h", confidence: 89 },
        { type: "info", icon: "bi-sign-stop", title: "Wrong Lane", detail: "XYZ 987 TU detected", confidence: 84 },
        { type: "critical", icon: "bi-exclamation-octagon", title: "No Helmet", detail: "Motorcycle · TAV 112 VW", confidence: 92 },
        { type: "warning", icon: "bi-parking", title: "Illegal Parking", detail: "PHL 445 XY detected", confidence: 86 },
    ];

    const MOCK_BOXES = [
        { label: "Vehicle", x: 15, y: 40, w: 25, h: 20, confidence: 0.93 },
        { label: "Red Light", x: 70, y: 10, w: 7, h: 10, confidence: 0.96 },
        { label: "Plate", x: 20, y: 55, w: 10, h: 4, confidence: 0.88 },
        { label: "Vehicle", x: 55, y: 30, w: 18, h: 15, confidence: 0.91 },
        { label: "Plate", x: 58, y: 42, w: 8, h: 3, confidence: 0.85 },
    ];

    const PLATES = ["ABC 123 AB", "TAV 456 CD", "NCR 789 EF", "PHL 234 GH", "MNL 567 IJ"];

    let alertIndex = 0;
    let simulationInterval = null;

    // Update timestamp
    function updateTimestamp() {
        if (feedTimestamp) {
            feedTimestamp.textContent = formatTimestamp();
        }
    }

    updateTimestamp();
    setInterval(updateTimestamp, 1000);

    // Render detection boxes
    function renderBoxes(boxes) {
        if (!detectionOverlay) return;
        detectionOverlay.innerHTML = boxes.map(function (box) {
            return (
                '<div class="detection-box" data-label="' + box.label + '" ' +
                'style="left:' + box.x + '%;top:' + box.y + '%;width:' + box.w + '%;height:' + box.h + '%;">' +
                '<span class="detection-label">' + box.label + " " + Math.round(box.confidence * 100) + "%</span>" +
                "</div>"
            );
        }).join("");
    }

    function renderDetectionList(boxes) {
        if (!activeDetections) return;
        activeDetections.innerHTML = boxes.map(function (box) {
            const level = box.confidence >= 0.9 ? "high" : box.confidence >= 0.8 ? "medium" : "low";
            return (
                '<div class="detection-item">' +
                '<span class="detection-type">' + box.label + "</span>" +
                '<span class="confidence-badge confidence-' + level + '">' + Math.round(box.confidence * 100) + "%</span>" +
                "</div>"
            );
        }).join("");
    }

    // Simulate moving boxes
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
        renderDetectionList(boxes);
    }

    simulationInterval = setInterval(simulateDetection, 2000);

    // Add random alert
    function addRandomAlert() {
        if (!alertFeed) return;
        const alert = MOCK_ALERTS[alertIndex % MOCK_ALERTS.length];
        alertIndex++;

        const plate = PLATES[Math.floor(Math.random() * PLATES.length)];
        const detail = alert.detail.includes("detected")
            ? plate + " detected"
            : alert.detail;

        const item = document.createElement("div");
        item.className = "alert-feed-item alert-" + alert.type;
        item.innerHTML =
            '<div class="alert-icon"><i class="bi ' + alert.icon + '"></i></div>' +
            '<div class="alert-content">' +
            "<strong>" + alert.title + "</strong>" +
            "<span>" + detail + "</span>" +
            "<small>Just now · " + alert.confidence + "% confidence</small>" +
            "</div>";

        alertFeed.insertBefore(item, alertFeed.firstChild);

        while (alertFeed.children.length > 8) {
            alertFeed.removeChild(alertFeed.lastChild);
        }

        showToast("Violation Detected", alert.title + " — " + detail, alert.type === "critical" ? "danger" : "warning");
    }

    setInterval(addRandomAlert, 12000);

    // Camera selector
    if (cameraList) {
        cameraList.addEventListener("click", function (e) {
            const btn = e.target.closest(".camera-item");
            if (!btn) return;

            cameraList.querySelectorAll(".camera-item").forEach(function (el) {
                el.classList.remove("active");
            });
            btn.classList.add("active");

            const name = btn.dataset.cameraName;
            const location = btn.dataset.cameraLocation;
            const status = btn.dataset.cameraStatus;
            const fps = btn.dataset.cameraFps;
            const feed = btn.dataset.cameraFeed;

            document.getElementById("activeCameraName").textContent = name;
            document.getElementById("feedLocation").textContent = location;
            document.getElementById("feedFps").textContent = (status === "online" ? fps : "0") + " FPS";

            const feedImage = document.getElementById("feedImage");
            if (feedImage && feed) {
                feedImage.src = "/static/images/" + feed;
            }

            const statusEl = document.getElementById("feedStatus");
            if (status === "online") {
                statusEl.textContent = "Online";
                statusEl.className = "badge bg-success-subtle text-success";
            } else {
                statusEl.textContent = "Offline";
                statusEl.className = "badge bg-danger-subtle text-danger";
            }

            showToast("Camera Switched", "Now viewing: " + name, "info");
        });
    }

    // Control buttons
    document.getElementById("btnSnapshot")?.addEventListener("click", function () {
        showToast("Snapshot", "Frame captured and saved (demo).", "success");
    });

    document.getElementById("btnRecord")?.addEventListener("click", function () {
        showToast("Recording", "Recording started — 30s clip (demo).", "danger");
    });

    document.getElementById("btnFullscreen")?.addEventListener("click", function () {
        const feed = document.getElementById("cctvFeed");
        if (feed.requestFullscreen) {
            feed.requestFullscreen();
        }
    });

    document.getElementById("clearAlerts")?.addEventListener("click", function () {
        if (alertFeed) alertFeed.innerHTML = "";
        showToast("Alerts Cleared", "All violation alerts have been cleared.", "info");
    });
})();
