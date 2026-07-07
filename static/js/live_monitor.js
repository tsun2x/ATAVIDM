/**
 * TAVIDM - Video Monitor Simulation (pre-recorded MP4, not live CCTV)
 */

(function () {
    "use strict";

    const feedTimestamp = document.getElementById("feedTimestamp");
    const alertFeed = document.getElementById("alertFeed");
    const detectionOverlay = document.getElementById("detectionOverlay");
    const activeDetections = document.getElementById("activeDetections");
    const videoList = document.getElementById("videoList");
    const progressBar = document.getElementById("progressBar");
    const progressLabel = document.getElementById("progressLabel");
    const detectionTimeline = document.getElementById("detectionTimeline");
    const btnPlay = document.getElementById("btnPlay");

    const MOCK_ALERTS = [
        { type: "critical", icon: "bi-arrow-left-right", title: "Counterflowing", detail: "Track #7 · car", confidence: 88 },
        { type: "warning", icon: "bi-truck", title: "Truck Ban", detail: "Track #12 · truck", confidence: 72 },
        { type: "warning", icon: "bi-parking", title: "Illegal Parking", detail: "Track #3 · jeepney", confidence: 86 },
        { type: "critical", icon: "bi-exclamation-triangle", title: "Reckless Driving", detail: "Track #15 · car → Review Queue", confidence: 68 },
        { type: "info", icon: "bi-sign-stop", title: "Obstruction", detail: "Track #9 · truck", confidence: 84 },
    ];

    const MOCK_BOXES = [
        { label: "car", track_id: 3, x: 15, y: 40, w: 25, h: 20, confidence: 0.91 },
        { label: "jeepney", track_id: 7, x: 48, y: 28, w: 22, h: 18, confidence: 0.87 },
        { label: "motorcycle", track_id: 12, x: 68, y: 42, w: 10, h: 14, confidence: 0.84 },
    ];

    let alertIndex = 0;
    let isPlaying = false;
    let progress = 0;
    let playInterval = null;
    let totalSeconds = 754;

    function formatTime(sec) {
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return m + ":" + String(s).padStart(2, "0");
    }

    function updateTimestamp() {
        const elapsed = Math.floor(progress * totalSeconds);
        if (feedTimestamp) feedTimestamp.textContent = formatTime(elapsed);
        if (progressBar) progressBar.style.width = (progress * 100) + "%";
        if (progressLabel) {
            const dur = document.getElementById("videoDuration")?.textContent || "12:34";
            progressLabel.textContent = formatTime(elapsed) + " / " + dur;
        }
    }

    function buildTimeline() {
        if (!detectionTimeline) return;
        detectionTimeline.innerHTML = "";
        for (let i = 0; i < 40; i++) {
            const marker = document.createElement("div");
            marker.className = "timeline-marker";
            if (Math.random() > 0.85) marker.classList.add("has-violation");
            if (i === Math.floor(progress * 40)) marker.classList.add("active");
            detectionTimeline.appendChild(marker);
        }
    }

    function renderBoxes(boxes) {
        if (!detectionOverlay) return;
        detectionOverlay.innerHTML = boxes.map(function (box) {
            return (
                '<div class="detection-box" data-label="' + box.label + '" ' +
                'style="left:' + box.x + '%;top:' + box.y + '%;width:' + box.w + '%;height:' + box.h + '%;">' +
                '<span class="detection-label">' + box.label + " #" + box.track_id + " " + Math.round(box.confidence * 100) + "%</span>" +
                "</div>"
            );
        }).join("");
    }

    function renderDetectionList(boxes) {
        if (!activeDetections) return;
        activeDetections.innerHTML = boxes.map(function (box) {
            const level = box.confidence >= 0.85 ? "high" : box.confidence >= 0.65 ? "medium" : "low";
            return (
                '<div class="detection-item">' +
                '<span class="detection-type">' + box.label + ' <small class="text-muted">#' + box.track_id + "</small></span>" +
                '<span class="confidence-badge confidence-' + level + '">' + Math.round(box.confidence * 100) + "%</span>" +
                "</div>"
            );
        }).join("");
    }

    function simulateDetection() {
        const boxes = MOCK_BOXES.map(function (box) {
            return {
                ...box,
                x: Math.max(5, Math.min(75, box.x + (Math.random() - 0.5) * 4)),
                y: Math.max(5, Math.min(70, box.y + (Math.random() - 0.5) * 3)),
                confidence: Math.min(0.99, Math.max(0.65, box.confidence + (Math.random() - 0.5) * 0.05)),
            };
        });
        renderBoxes(boxes);
        renderDetectionList(boxes);
        buildTimeline();
    }

    function togglePlay() {
        isPlaying = !isPlaying;
        if (btnPlay) btnPlay.innerHTML = isPlaying ? '<i class="bi bi-pause-fill"></i>' : '<i class="bi bi-play-fill"></i>';
        if (isPlaying) {
            playInterval = setInterval(function () {
                progress = Math.min(1, progress + 0.005);
                updateTimestamp();
                if (progress >= 1) togglePlay();
            }, 200);
        } else if (playInterval) {
            clearInterval(playInterval);
            playInterval = null;
        }
    }

    btnPlay?.addEventListener("click", togglePlay);

    document.getElementById("btnStepBack")?.addEventListener("click", function () {
        progress = Math.max(0, progress - 0.02);
        updateTimestamp();
        buildTimeline();
    });

    document.getElementById("btnStepForward")?.addEventListener("click", function () {
        progress = Math.min(1, progress + 0.02);
        updateTimestamp();
        buildTimeline();
    });

    document.getElementById("btnFullscreen")?.addEventListener("click", function () {
        const feed = document.getElementById("videoFeed");
        if (feed?.requestFullscreen) feed.requestFullscreen();
    });

    setInterval(simulateDetection, 2000);
    buildTimeline();
    updateTimestamp();

    function addRandomAlert() {
        if (!alertFeed) return;
        const alert = MOCK_ALERTS[alertIndex % MOCK_ALERTS.length];
        alertIndex++;
        const item = document.createElement("div");
        item.className = "alert-feed-item alert-" + alert.type;
        item.innerHTML =
            '<div class="alert-icon"><i class="bi ' + alert.icon + '"></i></div>' +
            '<div class="alert-content"><strong>' + alert.title + "</strong>" +
            "<span>" + alert.detail + "</span>" +
            "<small>Just now · " + alert.confidence + "% confidence</small></div>";
        alertFeed.insertBefore(item, alertFeed.firstChild);
        while (alertFeed.children.length > 8) alertFeed.removeChild(alertFeed.lastChild);
        showToast("Violation Detected", alert.title + " — " + alert.detail, alert.type === "critical" ? "danger" : "warning");
    }

    setInterval(addRandomAlert, 14000);

    if (videoList) {
        videoList.addEventListener("click", function (e) {
            const btn = e.target.closest(".camera-item");
            if (!btn) return;
            videoList.querySelectorAll(".camera-item").forEach(function (el) { el.classList.remove("active"); });
            btn.classList.add("active");
            document.getElementById("activeVideoName").textContent = btn.dataset.videoName;
            document.getElementById("videoDuration").textContent = btn.dataset.videoDuration;
            document.getElementById("videoCondition").textContent = btn.dataset.videoCondition.charAt(0).toUpperCase() + btn.dataset.videoCondition.slice(1);
            const feedImage = document.getElementById("feedImage");
            if (feedImage && btn.dataset.videoThumbnail) {
                feedImage.src = "/static/images/" + btn.dataset.videoThumbnail;
            }
            progress = 0;
            updateTimestamp();
            showToast("Video Loaded", btn.dataset.videoName, "info");
        });
    }

    document.getElementById("clearAlerts")?.addEventListener("click", function () {
        if (alertFeed) alertFeed.innerHTML = "";
        showToast("Alerts Cleared", "Violation alerts cleared.", "info");
    });
})();
