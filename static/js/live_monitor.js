/**
 * TAVIDM - Live Monitor: RTSP camera streams + recorded video processing
 */

(function () {
    "use strict";

    const feedImage = document.getElementById("feedImage");
    const feedPlaceholder = document.getElementById("feedPlaceholder");
    const feedStatus = document.getElementById("feedStatus");
    const liveIndicator = document.getElementById("liveIndicator");
    const activeSourceName = document.getElementById("activeSourceName");
    const sourceMeta = document.getElementById("sourceMeta");
    const btnProcess = document.getElementById("btnProcessVideo");
    const btnStopStream = document.getElementById("btnStopStream");
    const cameraList = document.getElementById("cameraList");
    const videoList = document.getElementById("videoList");
    const alertFeed = document.getElementById("alertFeed");
    const alertCount = document.getElementById("alertCount");

    let activeCamera = null;
    let activeVideo = null;
    let statusPoll = null;

    function clearActive() {
        document.querySelectorAll(".camera-item.active").forEach(function (el) {
            el.classList.remove("active");
        });
        if (statusPoll) { clearInterval(statusPoll); statusPoll = null; }
        activeCamera = null;
        activeVideo = null;
        btnProcess?.classList.add("d-none");
        btnStopStream?.classList.add("d-none");
        liveIndicator?.classList.add("d-none");
    }

    function showFeed(src) {
        if (!feedImage) return;
        feedImage.src = src;
        feedImage.classList.remove("d-none");
        feedPlaceholder?.classList.add("d-none");
    }

    // --- Live cameras ----------------------------------------------------------

    function selectCamera(camera, btn) {
        clearActive();
        activeCamera = camera;
        btn.classList.add("active");
        activeSourceName.textContent = camera.name;
        sourceMeta.textContent = camera.location || "RTSP stream";
        feedStatus.textContent = "Connecting to stream…";
        liveIndicator?.classList.remove("d-none");
        btnStopStream?.classList.remove("d-none");
        showFeed(camera.stream_url + "?t=" + Date.now());

        statusPoll = setInterval(function () {
            fetch("/api/cameras/" + camera.id + "/stream-status")
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    if (!activeCamera || activeCamera.id !== camera.id) return;
                    feedStatus.textContent = "Stream: " + payload.status +
                        (payload.error ? " — " + payload.error : "");
                });
        }, 5000);
    }

    cameraList?.addEventListener("click", function (e) {
        const btn = e.target.closest(".camera-item");
        if (!btn || !btn.dataset.camera) return;
        let camera;
        try { camera = JSON.parse(btn.dataset.camera); } catch (err) { return; }
        selectCamera(camera, btn);
    });

    btnStopStream?.addEventListener("click", function () {
        if (!activeCamera) return;
        fetch("/api/cameras/" + activeCamera.id + "/stop", { method: "POST" })
            .then(function () {
                feedStatus.textContent = "Stream stopped.";
                feedImage.classList.add("d-none");
                feedPlaceholder?.classList.remove("d-none");
                clearActive();
            });
    });

    // --- Uploaded videos --------------------------------------------------------

    function describeVideo(video) {
        if (video.processing) return "Processing with YOLOv8m + ByteTrack…";
        switch (video.status) {
            case "processed": return "Processed — violations sent to the review queue.";
            case "ready": return "Annotated and ready for processing.";
            case "annotating": return "Awaiting zone annotation.";
            default: return "Uploaded.";
        }
    }

    function selectVideo(video, btn) {
        clearActive();
        activeVideo = video;
        btn.classList.add("active");
        activeSourceName.textContent = video.filename;
        sourceMeta.textContent = video.condition.charAt(0).toUpperCase() + video.condition.slice(1) +
            " · " + video.status;
        feedStatus.textContent = describeVideo(video);
        showFeed(video.frame_url);

        if (video.has_annotation && !video.processed && !video.processing) {
            btnProcess?.classList.remove("d-none");
        }
        if (video.processing) pollProcessing(video.db_id);
    }

    videoList?.addEventListener("click", function (e) {
        const btn = e.target.closest(".camera-item");
        if (!btn || !btn.dataset.video) return;
        let video;
        try { video = JSON.parse(btn.dataset.video); } catch (err) { return; }
        selectVideo(video, btn);
    });

    function pollProcessing(videoId) {
        if (statusPoll) clearInterval(statusPoll);
        statusPoll = setInterval(function () {
            fetch("/api/videos/" + videoId + "/process-status")
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    if (!payload.success) return;
                    if (payload.job_state === "done" || payload.video_status === "processed") {
                        clearInterval(statusPoll);
                        statusPoll = null;
                        feedStatus.textContent = "Processing complete — check the review queue.";
                        showToast("Processing Complete", "Detected violations were queued for review.", "success");
                        refreshAlerts();
                    } else if (payload.job_state === "error") {
                        clearInterval(statusPoll);
                        statusPoll = null;
                        feedStatus.textContent = "Processing failed: " + (payload.job_error || "unknown error");
                        showToast("Processing Failed", payload.job_error || "Unknown error.", "danger");
                    } else {
                        feedStatus.textContent = "Processing with YOLOv8m + ByteTrack…";
                    }
                });
        }, 3000);
    }

    const processConfirmModal = document.getElementById("processConfirmModal");
    const pcViolationList = document.getElementById("pcViolationList");
    let pendingProcessVideo = null;

    function templateNameFor(video) {
        if (!video || !video.template_id) return "—";
        const tpls = window.TAVIDM_ZONE_TEMPLATES || [];
        const tpl = tpls.find(function (t) { return t.id === video.template_id; });
        return tpl ? tpl.template_name : "—";
    }

    function humanFileSize(bytes) {
        if (!bytes) return "—";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function buildProcessChecklist(video) {
        const catalog = window.TAVIDM_VIOLATION_CATALOG || [];
        const enabled = new Set(window.TAVIDM_ENABLED_VIOLATIONS || []);
        if (!pcViolationList) return;
        if (!catalog.length) {
            pcViolationList.innerHTML = '<div class="text-muted small">No violation types available.</div>';
            return;
        }
        pcViolationList.innerHTML = catalog.map(function (v) {
            const checked = enabled.has(v.name);
            const badge = !v.toggleable
                ? '<span class="badge bg-secondary-subtle text-secondary ms-2">planned</span>'
                : (v.implemented
                    ? '<span class="badge bg-success-subtle text-success ms-2">implemented</span>'
                    : '<span class="badge bg-warning-subtle text-warning ms-2">partial</span>');
            const disabled = v.toggleable ? "" : " disabled";
            const help = v.toggleable ? "" : ' <small class="text-muted">(planned — cannot be enabled yet)</small>';
            return (
                '<div class="form-check py-1">' +
                '<input class="form-check-input pc-violation-toggle" type="checkbox" ' +
                'id="pcv-' + v.name.replace(/[^a-z0-9]/gi, "_") + '" value="' + v.name + '"' +
                (checked ? " checked" : "") + disabled + ">" +
                '<label class="form-check-label ms-1" for="pcv-' + v.name.replace(/[^a-z0-9]/gi, "_") + '">' +
                v.name + badge + help + "</label>" +
                "</div>"
            );
        }).join("");
    }

    function openProcessConfirm(video) {
        if (!video) return;
        pendingProcessVideo = video;
        const fn = document.getElementById("pcFilename");
        const cond = document.getElementById("pcCondition");
        const fs = document.getElementById("pcFilesize");
        const tpl = document.getElementById("pcTemplate");
        if (fn) fn.textContent = video.filename || "—";
        if (cond) cond.textContent = (video.condition || "peak").charAt(0).toUpperCase() + (video.condition || "peak").slice(1);
        if (fs) fs.textContent = humanFileSize(video.file_size_bytes);
        if (tpl) tpl.textContent = templateNameFor(video);
        buildProcessChecklist(video);
        const modal = processConfirmModal ? bootstrap.Modal.getOrCreateInstance(processConfirmModal) : null;
        if (modal) modal.show();
    }

    function collectEnabledViolations() {
        return Array.from(document.querySelectorAll(".pc-violation-toggle:checked"))
            .map(function (el) { return el.value; });
    }

    document.getElementById("btnConfirmProcess")?.addEventListener("click", function () {
        if (!pendingProcessVideo) return;
        const videoId = pendingProcessVideo.db_id;
        const enabled = collectEnabledViolations();
        const modal = processConfirmModal ? bootstrap.Modal.getOrCreateInstance(processConfirmModal) : null;
        if (modal) modal.hide();
        btnProcess?.classList.add("d-none");
        fetch("/api/videos/" + videoId + "/process", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled_violations: enabled }),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    feedStatus.textContent = payload.queued
                        ? (payload.message || "Processing queued.")
                        : "Processing with YOLOv8m + ByteTrack…";
                    showToast("Processing Started", activeVideo.filename + (payload.queued ? " (queued)" : ""), "info");
                    pollProcessing(videoId);
                } else {
                    showToast("Error", payload.error || "Could not start processing.", "danger");
                    btnProcess?.classList.remove("d-none");
                }
            });
    });

    btnProcess?.addEventListener("click", function () {
        if (!activeVideo) return;
        openProcessConfirm(activeVideo);
    });

    // --- Violation alerts (pending review queue items) ---------------------------

    function alertIcon(type) {
        if (/counterflow/i.test(type)) return "bi-arrow-left-right";
        if (/truck/i.test(type)) return "bi-truck";
        if (/parking|stopping/i.test(type)) return "bi-p-square";
        if (/helmet/i.test(type)) return "bi-shield-x";
        if (/overload/i.test(type)) return "bi-people";
        if (/crossing/i.test(type)) return "bi-person-walking";
        return "bi-exclamation-triangle";
    }

    function refreshAlerts() {
        fetch("/api/review-queue?status=pending")
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (!payload.success || !alertFeed) return;
                if (alertCount) alertCount.textContent = payload.total;
                const items = payload.items.slice(0, 8);
                if (!items.length) {
                    alertFeed.innerHTML = '<div class="text-center text-muted small py-4">No pending detections.</div>';
                    return;
                }
                alertFeed.innerHTML = items.map(function (item) {
                    const level = item.confidence >= 0.95 ? "critical" : "warning";
                    return (
                        '<div class="alert-feed-item alert-' + level + '">' +
                        '<div class="alert-icon"><i class="bi ' + alertIcon(item.violation_type) + '"></i></div>' +
                        '<div class="alert-content"><strong>' + item.violation_type + "</strong>" +
                        "<span>Track #" + item.track_id + " · " + item.video_name + "</span>" +
                        "<small>Frame " + item.frame_number + " · " + Math.round(item.confidence * 100) + "% confidence</small></div>" +
                        "</div>"
                    );
                }).join("");
            });
    }

    refreshAlerts();
    setInterval(refreshAlerts, 10000);
})();
