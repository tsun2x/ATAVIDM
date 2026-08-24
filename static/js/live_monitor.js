/**
 * TAVIDM - Live Monitor: RTSP cameras + uploaded video processing UX
 */

(function () {
    "use strict";

    const feedImage = document.getElementById("feedImage");
    const sourceVideo = document.getElementById("sourceVideo");
    const annotatedVideo = document.getElementById("annotatedVideo");
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
    const mediaModeBar = document.getElementById("mediaModeBar");
    const processProgressPanel = document.getElementById("processProgressPanel");
    const videoActionsDropdown = document.getElementById("videoActionsDropdown");
    const btnProcessSelected = document.getElementById("btnProcessSelected");

    let activeCamera = null;
    let activeVideo = null;
    let statusPoll = null;
    let mediaMode = "reference";
    let watchingLive = false;
    let pendingDeleteVideo = null;

    function hideAllMedia() {
        feedImage?.classList.add("d-none");
        sourceVideo?.classList.add("d-none");
        annotatedVideo?.classList.add("d-none");
        if (sourceVideo) { sourceVideo.pause(); sourceVideo.removeAttribute("src"); sourceVideo.load(); }
        if (annotatedVideo) { annotatedVideo.pause(); annotatedVideo.removeAttribute("src"); annotatedVideo.load(); }
        feedPlaceholder?.classList.remove("d-none");
    }

    function clearActive() {
        document.querySelectorAll(".camera-item.active").forEach(function (el) {
            el.classList.remove("active");
        });
        if (statusPoll) { clearInterval(statusPoll); statusPoll = null; }
        activeCamera = null;
        activeVideo = null;
        watchingLive = false;
        btnProcess?.classList.add("d-none");
        btnStopStream?.classList.add("d-none");
        liveIndicator?.classList.add("d-none");
        mediaModeBar?.classList.add("d-none");
        processProgressPanel?.classList.add("d-none");
        videoActionsDropdown?.classList.add("d-none");
        hideAllMedia();
    }

    function showImage(src) {
        if (!feedImage) return;
        sourceVideo?.classList.add("d-none");
        annotatedVideo?.classList.add("d-none");
        feedImage.src = src;
        feedImage.classList.remove("d-none");
        feedPlaceholder?.classList.add("d-none");
    }

    function showSourceVideo(url) {
        if (!sourceVideo) return;
        feedImage?.classList.add("d-none");
        annotatedVideo?.classList.add("d-none");
        sourceVideo.src = url;
        sourceVideo.classList.remove("d-none");
        feedPlaceholder?.classList.add("d-none");
    }

    function showAnnotatedVideo(url) {
        if (!annotatedVideo) return;
        feedImage?.classList.add("d-none");
        sourceVideo?.classList.add("d-none");
        annotatedVideo.src = url;
        annotatedVideo.classList.remove("d-none");
        feedPlaceholder?.classList.add("d-none");
    }

    function setMediaMode(mode) {
        mediaMode = mode;
        document.querySelectorAll(".media-mode-btn").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.mode === mode);
        });
        if (!activeVideo) return;
        if (mode === "source") {
            showSourceVideo(activeVideo.source_video_url || ("/api/videos/" + activeVideo.db_id + "/media"));
            feedStatus.textContent = "Source Video";
        } else if (mode === "reference") {
            showImage(activeVideo.frame_url);
            feedStatus.textContent = "Reference Frame (zone annotation)";
        } else if (mode === "live_preview") {
            watchingLive = true;
            showImage("/api/videos/" + activeVideo.db_id + "/process-preview?t=" + Date.now());
            feedStatus.textContent = "Live Processing Preview (sampled annotated frames)";
        } else if (mode === "annotated" && activeVideo.annotated_video_url) {
            showAnnotatedVideo(activeVideo.annotated_video_url);
            feedStatus.textContent = "Annotated Result";
        }
    }

    mediaModeBar?.addEventListener("click", function (e) {
        const btn = e.target.closest(".media-mode-btn");
        if (!btn || btn.classList.contains("d-none")) return;
        setMediaMode(btn.dataset.mode);
    });

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
        showImage(camera.stream_url + "?t=" + Date.now());

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
                clearActive();
            });
    });

    // --- Uploaded videos --------------------------------------------------------

    function describeVideo(video) {
        if (video.processing) return "Processing — see Live Processing Preview / progress.";
        switch (video.status) {
            case "processed": return "Processed — review candidates separately from raw detections.";
            case "ready": return "Annotated and ready for processing.";
            case "annotating": return "Awaiting zone annotation.";
            default: return "Uploaded.";
        }
    }

    function updateActionMenu(video) {
        if (!videoActionsDropdown) return;
        videoActionsDropdown.classList.remove("d-none");
        const menu = document.getElementById("videoActionsMenu");
        if (!menu) return;
        menu.querySelectorAll("[data-action]").forEach(function (item) {
            const action = item.dataset.action;
            let show = true;
            if (action === "watch_live") show = !!video.processing;
            if (action === "preview_annotated" || action === "download_annotated") {
                show = !!video.annotated_video_ready;
            }
            if (action === "delete_video") {
                show = (window.TAVIDM_USER_ROLE || "") === "admin";
            }
            item.classList.toggle("d-none", !show);
        });
        document.getElementById("btnModeLivePreview")?.classList.toggle("d-none", !video.processing);
        document.getElementById("btnModeAnnotated")?.classList.toggle("d-none", !video.annotated_video_ready);
    }

    function selectVideo(video, btn) {
        clearActive();
        activeVideo = video;
        btn.classList.add("active");
        activeSourceName.textContent = video.filename;
        sourceMeta.textContent = (video.condition || "peak").charAt(0).toUpperCase() + (video.condition || "peak").slice(1) +
            " · " + video.status;
        feedStatus.textContent = describeVideo(video);
        mediaModeBar?.classList.remove("d-none");
        updateActionMenu(video);
        setMediaMode(video.processing ? "live_preview" : (video.annotated_video_ready ? "annotated" : "source"));

        const canProcess = video.has_annotation && (video.status === "ready" || video.status === "processed") && !video.processing;
        if (canProcess) btnProcess?.classList.remove("d-none");
        if (video.processing) {
            processProgressPanel?.classList.remove("d-none");
            pollProcessing(video.db_id);
        }
    }

    videoList?.addEventListener("click", function (e) {
        if (e.target.closest(".bulk-check-wrap")) return;
        const btn = e.target.closest(".camera-item");
        if (!btn || !btn.dataset.video) return;
        let video;
        try { video = JSON.parse(btn.dataset.video); } catch (err) { return; }
        selectVideo(video, btn);
    });

    function formatSec(sec) {
        if (sec == null || sec === "") return "—";
        const n = Number(sec);
        if (!isFinite(n)) return "—";
        if (n < 60) return n.toFixed(1) + "s";
        const m = Math.floor(n / 60);
        const s = Math.round(n % 60);
        return m + "m " + s + "s";
    }

    function updateProgressUI(payload) {
        if (!processProgressPanel) return;
        processProgressPanel.classList.remove("d-none");
        const stage = payload.stage || payload.job_state || "—";
        const pct = Math.max(0, Math.min(100, Number(payload.progress_percent || 0)));
        document.getElementById("ppStage").textContent = String(stage).replace(/_/g, " ");
        document.getElementById("ppPercent").textContent = pct.toFixed(1) + "%";
        const bar = document.getElementById("ppBar");
        if (bar) {
            bar.style.width = pct + "%";
            bar.setAttribute("aria-valuenow", String(Math.round(pct)));
        }
        const frames = (payload.frames_processed != null ? payload.frames_processed : "—") +
            " / " + (payload.total_frames != null ? payload.total_frames : "—");
        document.getElementById("ppFrames").textContent = frames;
        document.getElementById("ppElapsed").textContent = formatSec(payload.elapsed_sec);
        document.getElementById("ppFps").textContent = payload.processing_fps != null ? Number(payload.processing_fps).toFixed(2) : "—";
        document.getElementById("ppEta").textContent = payload.eta_sec != null ? formatSec(payload.eta_sec) : "—";
        document.getElementById("ppDetections").textContent = payload.detection_records != null ? payload.detection_records : "—";
        document.getElementById("ppCandidates").textContent = payload.violation_candidates != null ? payload.violation_candidates : "—";
        const classes = payload.class_counts || {};
        const classText = Object.keys(classes).length
            ? Object.keys(classes).map(function (k) { return k + ": " + classes[k]; }).join(", ")
            : "—";
        document.getElementById("ppClasses").textContent = classText;
        if (payload.queue_position != null && payload.queue_position > 0) {
            document.getElementById("ppStage").textContent = "queued (position " + payload.queue_position + ")";
        }
    }

    function showCompletionSummary(payload) {
        const box = document.getElementById("ppCompletionSummary");
        if (!box) return;
        const classes = payload.class_counts || {};
        const classLines = Object.keys(classes).map(function (k) {
            return "<li>" + k + ": " + classes[k] + " detection records</li>";
        }).join("");
        box.classList.remove("d-none");
        box.innerHTML =
            "<div class='small'>" +
            "<div><strong>Model:</strong> " + (payload.model_identifier || "YOLOv8m") + "</div>" +
            "<div><strong>Duration:</strong> " + formatSec(payload.elapsed_sec) +
            " · Source: " + formatSec(payload.source_duration_sec) + "</div>" +
            "<div><strong>Detection records:</strong> " + (payload.detection_records || 0) +
            (payload.unique_tracks != null ? " · Unique tracks: " + payload.unique_tracks : "") + "</div>" +
            "<div><strong>Violation candidates:</strong> " + (payload.violation_candidates || 0) +
            ' · <a href="/review-queue">Open Review Queue</a></div>' +
            (classLines ? "<ul class='mb-1'>" + classLines + "</ul>" : "") +
            (payload.annotated_video_url
                ? '<div class="mt-1"><button type="button" class="btn btn-sm btn-outline-secondary me-1" id="btnPreviewAnnotatedDone">Preview Annotated</button>' +
                  '<a class="btn btn-sm btn-outline-danger" href="' + payload.annotated_video_url + '?download=1">Download Annotated</a></div>'
                : "") +
            "</div>";
        document.getElementById("btnPreviewAnnotatedDone")?.addEventListener("click", function () {
            if (activeVideo) {
                activeVideo.annotated_video_ready = true;
                activeVideo.annotated_video_url = payload.annotated_video_url;
                updateActionMenu(activeVideo);
                setMediaMode("annotated");
            }
        });
    }

    function pollProcessing(videoId) {
        if (statusPoll) clearInterval(statusPoll);
        const tick = function () {
            fetch("/api/videos/" + videoId + "/process-status")
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    if (!payload.success) return;
                    updateProgressUI(payload);
                    if (watchingLive && mediaMode === "live_preview" && feedImage && !feedImage.src.includes("process-preview")) {
                        showImage("/api/videos/" + videoId + "/process-preview?t=" + Date.now());
                    }
                    if (payload.job_state === "done" || payload.stage === "completed" || payload.video_status === "processed") {
                        clearInterval(statusPoll);
                        statusPoll = null;
                        feedStatus.textContent = "Processing complete.";
                        if (activeVideo && activeVideo.db_id === videoId) {
                            activeVideo.processing = false;
                            activeVideo.processed = true;
                            activeVideo.status = "processed";
                            activeVideo.annotated_video_ready = !!payload.annotated_video_ready;
                            activeVideo.annotated_video_url = payload.annotated_video_url;
                            updateActionMenu(activeVideo);
                            btnProcess?.classList.remove("d-none");
                        }
                        showCompletionSummary(payload);
                        showToast("Processing Complete", "Detection records persisted; candidates sent to review when rules fired.", "success");
                        refreshAlerts();
                    } else if (payload.job_state === "error" || payload.stage === "failed") {
                        clearInterval(statusPoll);
                        statusPoll = null;
                        feedStatus.textContent = "Processing failed: " + (payload.error || payload.job_error || "unknown error");
                        showToast("Processing Failed", payload.error || payload.job_error || "Unknown error.", "danger");
                        if (activeVideo && activeVideo.db_id === videoId) {
                            activeVideo.processing = false;
                            btnProcess?.classList.remove("d-none");
                        }
                    }
                });
        };
        tick();
        statusPoll = setInterval(tick, 1500);
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

    function buildProcessChecklist() {
        const catalog = window.TAVIDM_VIOLATION_CATALOG || [];
        const enabled = new Set(window.TAVIDM_ENABLED_VIOLATIONS || []);
        const Sw = window.TavidmViolationSwitch;
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
            let reason = "";
            if (!v.toggleable) reason = "Planned — cannot be enabled yet.";
            else if (!v.implemented) reason = "Partial / model-dependent capability.";
            return Sw.render({
                name: v.name,
                id: "pcv-" + v.name.replace(/[^a-z0-9]/gi, "_"),
                checked: checked && v.toggleable,
                disabled: !v.toggleable,
                reason: reason,
                badgeHtml: badge,
                inputClass: "pc-violation-toggle",
            });
        }).join("");
        pcViolationList.querySelectorAll("input[role='switch']").forEach(Sw.sync);
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
        buildProcessChecklist();
        const modal = processConfirmModal ? bootstrap.Modal.getOrCreateInstance(processConfirmModal) : null;
        if (modal) modal.show();
    }

    function collectEnabledViolations() {
        return Array.from(document.querySelectorAll(".pc-violation-toggle:checked"))
            .map(function (el) { return el.value; });
    }

    function selectedViewerMode() {
        const el = document.querySelector('input[name="pcViewerMode"]:checked');
        return el ? el.value : "background";
    }

    document.getElementById("btnConfirmProcess")?.addEventListener("click", function () {
        if (!pendingProcessVideo) return;
        const videoId = pendingProcessVideo.db_id;
        const enabled = collectEnabledViolations();
        const viewerMode = selectedViewerMode();
        const modal = processConfirmModal ? bootstrap.Modal.getOrCreateInstance(processConfirmModal) : null;
        if (modal) modal.hide();
        btnProcess?.classList.add("d-none");
        fetch("/api/videos/" + videoId + "/process", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled_violations: enabled, viewer_mode: viewerMode }),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    if (activeVideo) {
                        activeVideo.processing = true;
                        updateActionMenu(activeVideo);
                    }
                    watchingLive = viewerMode === "watch_live";
                    if (watchingLive) setMediaMode("live_preview");
                    processProgressPanel?.classList.remove("d-none");
                    feedStatus.textContent = payload.queued
                        ? (payload.message || "Processing queued.")
                        : "Processing with YOLOv8m + ByteTrack…";
                    showToast("Processing Started", (activeVideo && activeVideo.filename) + (payload.queued ? " (queued)" : ""), "info");
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

    // Actions menu
    document.getElementById("videoActionsMenu")?.addEventListener("click", function (e) {
        const item = e.target.closest("[data-action]");
        if (!item || !activeVideo) return;
        const action = item.dataset.action;
        if (action === "preview_source") setMediaMode("source");
        else if (action === "watch_live") { watchingLive = true; setMediaMode("live_preview"); }
        else if (action === "preview_annotated") setMediaMode("annotated");
        else if (action === "download_annotated" && activeVideo.annotated_video_url) {
            window.location.href = activeVideo.annotated_video_url + "?download=1";
        } else if (action === "history") openHistory(activeVideo.db_id);
        else if (action === "reprocess") openProcessConfirm(activeVideo);
        else if (action === "remove_results") {
            if (!confirm("Remove processing results for " + activeVideo.filename + "? Source and annotation are kept.")) return;
            fetch("/api/videos/" + activeVideo.db_id + "/remove-results", { method: "POST" })
                .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                .then(function (res) {
                    if (!res.ok) {
                        showToast("Blocked", res.j.error || "Could not remove results.", "warning");
                        return;
                    }
                    activeVideo.processed = false;
                    activeVideo.status = "ready";
                    activeVideo.annotated_video_ready = false;
                    activeVideo.annotated_video_url = null;
                    updateActionMenu(activeVideo);
                    processProgressPanel?.classList.add("d-none");
                    setMediaMode("source");
                    showToast("Results Removed", "Video is ready to process again.", "success");
                });
        } else if (action === "delete_video") {
            pendingDeleteVideo = activeVideo;
            document.getElementById("deleteVideoFilename").textContent = activeVideo.filename;
            document.getElementById("deleteVideoConfirmInput").value = "";
            bootstrap.Modal.getOrCreateInstance(document.getElementById("deleteVideoModal")).show();
        }
    });

    document.getElementById("btnConfirmDeleteVideo")?.addEventListener("click", function () {
        if (!pendingDeleteVideo) return;
        const typed = document.getElementById("deleteVideoConfirmInput").value;
        fetch("/api/videos/" + pendingDeleteVideo.db_id, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm_filename: typed }),
        })
            .then(function (r) { return r.json().then(function (j) { return { status: r.status, j: j }; }); })
            .then(function (res) {
                if (res.status === 409 || !res.j.success) {
                    showToast("Cannot Delete", res.j.error || "Delete blocked.", "warning");
                    return;
                }
                bootstrap.Modal.getOrCreateInstance(document.getElementById("deleteVideoModal")).hide();
                showToast("Deleted", pendingDeleteVideo.filename + " removed.", "success");
                const row = videoList?.querySelector('[data-video*="\\"db_id\\": ' + pendingDeleteVideo.db_id + '"]')
                    || videoList?.querySelector('input.bulk-video-check[value="' + pendingDeleteVideo.db_id + '"]')?.closest(".camera-item-row");
                row?.remove();
                clearActive();
            });
    });

    function openHistory(videoId) {
        fetch("/api/videos/" + videoId + "/history")
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (!payload.success) {
                    showToast("Error", payload.error || "Could not load history.", "danger");
                    return;
                }
                const v = payload.video || {};
                const runs = payload.processing_runs || [];
                const events = payload.history_events || [];
                const body = document.getElementById("videoHistoryBody");
                const esc = function (s) {
                    const d = document.createElement("div");
                    d.textContent = s == null ? "" : String(s);
                    return d.innerHTML;
                };
                const recorded = payload.recording_time_known ? esc(payload.recorded_at) : "Unknown";
                const dur = (v.duration != null ? esc(v.duration) : null)
                    || (payload.source_duration_sec != null ? esc(payload.source_duration_sec) + "s" : "—");
                body.innerHTML =
                    "<dl class='row small mb-3'>" +
                    "<dt class='col-sm-4'>Filename</dt><dd class='col-sm-8'>" + esc(v.filename || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Uploaded by</dt><dd class='col-sm-8'>" + esc((payload.uploader && payload.uploader.username) || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Upload time</dt><dd class='col-sm-8'>" + esc(v.created_at || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Recording time</dt><dd class='col-sm-8'>" + recorded + "</dd>" +
                    "<dt class='col-sm-4'>File size</dt><dd class='col-sm-8'>" + esc(humanFileSize(v.file_size_bytes)) + "</dd>" +
                    "<dt class='col-sm-4'>Duration</dt><dd class='col-sm-8'>" + dur + "</dd>" +
                    "<dt class='col-sm-4'>Status</dt><dd class='col-sm-8'>" + esc(v.status || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Condition</dt><dd class='col-sm-8'>" + esc(v.condition || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Template</dt><dd class='col-sm-8'>" + esc((payload.template && payload.template.template_name) || "—") + "</dd>" +
                    "<dt class='col-sm-4'>Current detections</dt><dd class='col-sm-8'>" +
                    esc((payload.detection_summary && payload.detection_summary.detection_records) || 0) +
                    " · candidates " + esc((payload.detection_summary && payload.detection_summary.violation_candidates) || 0) +
                    "</dd>" +
                    "</dl>" +
                    "<h6>Processing runs</h6>" +
                    (runs.length ? "<ul class='small'>" + runs.map(function (r) {
                        const rules = Array.isArray(r.enabled_violations)
                            ? r.enabled_violations.join(", ")
                            : "—";
                        return "<li>Run #" + esc(r.id) +
                            (r.is_current_result ? " <strong>(current result)</strong>" : "") +
                            (r.is_latest_attempt && !r.is_current_result
                                ? " <em>(latest attempt)</em>"
                                : "") +
                            " · " + esc(r.status) +
                            " · detections=" + esc(r.detection_records != null ? r.detection_records : "—") +
                            " · mode=" + esc(r.viewer_mode || "background") +
                            " · model=" + esc(r.model_identifier || "—") +
                            " · queued=" + esc(r.queued_at || "—") +
                            " · start=" + esc(r.started_at || "—") +
                            " · finish=" + esc(r.finished_at || "—") +
                            " · detections=" + esc(r.detection_records || 0) +
                            " · candidates=" + esc(r.violation_candidates || 0) +
                            " · rules=[" + esc(rules) + "]" +
                            (r.error_message ? " · error=" + esc(r.error_message) : "") +
                            "</li>";
                    }).join("") + "</ul>" : "<p class='small text-muted'>No runs yet.</p>") +
                    "<h6>Events</h6>" +
                    (events.length ? "<ul class='small'>" + events.map(function (ev) {
                        let detail = "";
                        try {
                            const d = typeof ev.detail_json === "string" ? JSON.parse(ev.detail_json) : (ev.detail || {});
                            if (d && d.message) detail = " — " + esc(d.message);
                        } catch (err) { /* ignore */ }
                        return "<li>" + esc(ev.created_at) + " · " + esc(ev.event_type) + detail + "</li>";
                    }).join("") + "</ul>" : "<p class='small text-muted'>No events.</p>");
                bootstrap.Modal.getOrCreateInstance(document.getElementById("videoHistoryModal")).show();
            });
    }

    // Bulk processing
    function eligibleBulkChecks() {
        return Array.from(videoList?.querySelectorAll(".bulk-video-check") || []).filter(function (cb) {
            return !cb.disabled && cb.getAttribute("data-ready") === "1";
        });
    }

    function syncSelectAllState() {
        const selectAll = document.getElementById("bulkSelectAll");
        if (!selectAll) return;
        const eligible = eligibleBulkChecks();
        const checked = eligible.filter(function (cb) { return cb.checked; });
        selectAll.checked = eligible.length > 0 && checked.length === eligible.length;
        selectAll.indeterminate = checked.length > 0 && checked.length < eligible.length;
    }

    function refreshBulkButton() {
        const checked = eligibleBulkChecks().filter(function (cb) { return cb.checked; });
        btnProcessSelected?.classList.toggle("d-none", checked.length === 0);
        syncSelectAllState();
    }

    document.getElementById("bulkSelectAll")?.addEventListener("change", function (e) {
        const on = !!e.target.checked;
        eligibleBulkChecks().forEach(function (cb) { cb.checked = on; });
        e.target.indeterminate = false;
        refreshBulkButton();
    });

    videoList?.addEventListener("change", function (e) {
        if (e.target.classList.contains("bulk-video-check")) {
            refreshBulkButton();
        }
    });
    btnProcessSelected?.addEventListener("click", function () {
        const ids = eligibleBulkChecks().filter(function (cb) { return cb.checked; }).map(function (cb) {
            return Number(cb.value);
        });
        if (!ids.length) return;
        btnProcessSelected.disabled = true;
        fetch("/api/videos/process-bulk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video_ids: ids }),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                btnProcessSelected.disabled = false;
                if (!payload.success) {
                    showToast("Bulk Failed", payload.error || "Could not queue videos.", "danger");
                    return;
                }
                const msg = "Accepted " + (payload.accepted || []).length +
                    ", rejected " + (payload.rejected || []).length +
                    ", already queued " + (payload.already_queued || []).length;
                showToast("Bulk Processing", msg, "info");
                (payload.accepted || []).forEach(function (a) {
                    const label = videoList.querySelector('.video-status-label[data-db-id="' + a.video_id + '"]');
                    if (label) label.textContent = "Queued · pos " + (a.queue_position || "?");
                    const cb = videoList.querySelector('.bulk-video-check[value="' + a.video_id + '"]');
                    if (cb) {
                        cb.checked = false;
                        cb.disabled = true;
                        cb.setAttribute("data-ready", "0");
                    }
                });
                refreshBulkButton();
            })
            .catch(function () {
                btnProcessSelected.disabled = false;
                showToast("Bulk Failed", "Network error.", "danger");
            });
    });

    // --- Violation alerts -------------------------------------------------------

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
                    alertFeed.innerHTML = '<div class="text-center text-muted small py-4">No pending violation candidates.</div>';
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
