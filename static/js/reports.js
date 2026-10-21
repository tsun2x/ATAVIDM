/**
 * TAVIDM - Reports Page (real PDF/Excel generation)
 */

(function () {
    "use strict";

    const historyBody = document.getElementById("historyBody");
    const previewState = document.getElementById("reportPreviewState");
    const previewMessage = document.getElementById("reportPreviewMessage");
    const previewResults = document.getElementById("reportPreviewResults");
    let previewPage = 1;
    let previewController = null;
    let previewRequestId = 0;
    let videoOptionsRequestId = 0;
    let videoOptionsLoading = false;
    let videoOptionsReady = true;

    function collectFilters() {
        return {
            title: document.getElementById("reportName")?.value.trim() || null,
            date_from: document.getElementById("dateFrom")?.value || null,
            date_to: document.getElementById("dateTo")?.value || null,
            violation_type: document.getElementById("reportViolationType")?.value || null,
            video_id: document.getElementById("reportVideo")?.value || null,
            status: document.getElementById("reportStatus")?.value || null,
            uploader_id: document.getElementById("reportUploader")?.value || null,
        };
    }

    function formatBadge(type) {
        return type === "PDF"
            ? '<span class="badge bg-danger-subtle text-danger">PDF</span>'
            : '<span class="badge bg-success-subtle text-success">Excel</span>';
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function previewFilters() {
        const filters = collectFilters();
        return {
            video_id: filters.video_id,
            uploader_id: filters.uploader_id,
            date_from: filters.date_from,
            date_to: filters.date_to,
            violation_type: filters.violation_type,
            status: filters.status,
        };
    }

    function setPreviewMessage(message, state) {
        if (previewMessage) previewMessage.textContent = message;
        if (previewResults) previewResults.hidden = true;
        if (previewState) previewState.textContent = state;
        const total = document.getElementById("reportPreviewTotal");
        if (total) total.textContent = "—";
        const categories = document.getElementById("reportPreviewCategories");
        const cases = document.getElementById("reportPreviewCases");
        const pageLabel = document.getElementById("reportPreviewPage");
        const previous = document.getElementById("reportPreviewPrevious");
        const next = document.getElementById("reportPreviewNext");
        if (categories) categories.innerHTML = "";
        if (cases) cases.innerHTML = "";
        if (pageLabel) pageLabel.textContent = "";
        if (previous) previous.disabled = true;
        if (next) next.disabled = true;
    }

    function invalidatePreview(message, state) {
        previewRequestId += 1;
        if (previewController) previewController.abort();
        previewController = null;
        previewPage = 1;
        if (message) setPreviewMessage(message, state);
    }

    function setGenerateButtonsDisabled(disabled) {
        ["btnGeneratePdf", "btnGenerateExcel"].forEach(function (id) {
            const button = document.getElementById(id);
            if (button) button.disabled = disabled;
        });
    }

    function resultStateLabel(state) {
        const labels = {
            processing: "Processing",
            completed_with_cases: "Completed · cases available",
            completed_empty: "Completed · no matching cases",
            previous_results_available: "Latest attempt failed · previous cases available",
            failed_no_results: "Latest attempt failed · no cases available",
            legacy_results_available: "Legacy results · run unknown",
            legacy_processed_no_run: "Processed · run provenance unavailable",
            not_processed: "Not processed",
        };
        return labels[state] || "Processing state unavailable";
    }

    function renderPreview(payload) {
        const categories = document.getElementById("reportPreviewCategories");
        const cases = document.getElementById("reportPreviewCases");
        const total = document.getElementById("reportPreviewTotal");
        const pageLabel = document.getElementById("reportPreviewPage");
        const previous = document.getElementById("reportPreviewPrevious");
        const next = document.getElementById("reportPreviewNext");
        const page = payload.pagination;
        const groups = new Map();
        (payload.items || []).forEach(function (item) {
            const name = item.violation_type || "Unknown type";
            if (!groups.has(name)) groups.set(name, []);
            groups.get(name).push(item);
        });
        categories.innerHTML = (payload.category_counts || []).map(function (item) {
            return '<span class="badge text-bg-light border">' + escapeHtml(item.violation_type) +
                ": " + escapeHtml(item.count) + "</span>";
        }).join("") || '<span class="text-muted small">No matching cases</span>';
        cases.innerHTML = Array.from(groups.entries()).map(function (entry) {
            const name = entry[0];
            const rows = entry[1].map(function (item) {
                const run = item.processing_run_id == null
                    ? "Legacy / unknown"
                    : "#" + item.processing_run_id + (item.run_status ? " · " + item.run_status : "");
                return "<tr>" +
                    "<td>" + escapeHtml("VIO-" + String(item.id).padStart(6, "0")) + "</td>" +
                    "<td>" + escapeHtml(item.detected_at || "—") + "</td>" +
                    "<td>" + escapeHtml(item.status || "—") + "</td>" +
                    "<td>" + escapeHtml(run) + "</td>" +
                    "<td>" + escapeHtml(item.observation_count || 1) + "</td>" +
                    "<td>" + escapeHtml(item.official_category || "—") + "</td>" +
                    "<td>" + escapeHtml(item.legal_status || "—") + "</td>" +
                    "<td>" + escapeHtml(item.case_outcome || "—") + "</td>" +
                    "</tr>";
            }).join("");
            return '<tr class="table-light"><th colspan="8">' + escapeHtml(name) +
                " · " + escapeHtml((payload.category_counts || []).find(function (x) {
                    return x.violation_type === name;
                })?.count || entry[1].length) + "</th></tr>" + rows;
        }).join("");
        total.textContent = String(payload.total_cases);
        pageLabel.textContent = page.pages
            ? "Page " + page.page + " of " + page.pages + " · all persisted cases, including historical runs"
            : "No matching cases · all persisted runs checked";
        previous.disabled = page.page <= 1;
        next.disabled = page.page >= page.pages;
        previewState.textContent = resultStateLabel(payload.result_state);
        previewMessage.textContent = payload.total_cases > 0
            ? "Cases are grouped by their canonical type. Legal category and status are shown separately."
            : "No cases match these filters. Processing state is shown separately from the case count.";
        previewResults.hidden = false;
    }

    function loadPreview(page) {
        const requestId = ++previewRequestId;
        if (previewController) previewController.abort();
        previewController = null;
        if (videoOptionsLoading || !videoOptionsReady) return Promise.resolve();
        const filters = previewFilters();
        if (!filters.video_id) {
            const videoSelect = document.getElementById("reportVideo");
            const noVideos = videoSelect && videoSelect.dataset && videoSelect.dataset.empty === "true";
            setPreviewMessage(noVideos ? "No videos are available for this account." :
                "Choose a video in the report generator to browse its cases.", noVideos ? "No videos" : "Choose a video");
            return Promise.resolve();
        }
        previewPage = page || 1;
        previewController = new AbortController();
        const controller = previewController;
        const query = new URLSearchParams();
        Object.keys(filters).forEach(function (key) {
            if (filters[key]) query.set(key, filters[key]);
        });
        query.set("page", String(previewPage));
        query.set("per_page", "25");
        setPreviewMessage("Loading cases…", "Loading");
        return fetch("/api/reports/preview?" + query.toString(), { signal: controller.signal })
            .then(function (response) {
                return response.json().then(function (payload) {
                    if (requestId !== previewRequestId) return;
                    if (!response.ok || !payload.success) throw new Error(payload.error || "Could not load cases.");
                    renderPreview(payload);
                });
            })
            .catch(function (error) {
                if (requestId !== previewRequestId) return;
                if (error.name === "AbortError") return;
                setPreviewMessage(error.message || "Could not load cases. Please try again.", "Unavailable");
            });
    }

    function refreshVideoOptions() {
        const requestId = ++videoOptionsRequestId;
        const videoSelect = document.getElementById("reportVideo");
        if (!videoSelect) return Promise.resolve();
        videoOptionsLoading = true;
        videoOptionsReady = false;
        videoSelect.disabled = true;
        videoSelect.dataset.empty = "false";
        videoSelect.value = "";
        videoSelect.innerHTML = '<option value="">Loading videos…</option>';
        setGenerateButtonsDisabled(true);
        invalidatePreview("Loading videos for the selected account…", "Loading");
        const filters = collectFilters();
        const url = filters.uploader_id
            ? "/api/videos?uploader_id=" + encodeURIComponent(filters.uploader_id)
            : "/api/videos";
        return fetch(url).then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok) throw new Error(payload.error || "Could not load videos.");
                return payload;
            });
        }).then(function (payload) {
            if (requestId !== videoOptionsRequestId) return;
            if (!payload.success) throw new Error(payload.error || "Could not load videos.");
            const videos = payload.videos || [];
            videoSelect.innerHTML = '<option value="">All Sources</option>' +
                videos.map(function (video) {
                    return '<option value="' + escapeHtml(video.db_id) + '">' +
                        escapeHtml(video.filename) + " · " + escapeHtml(video.status || "uploaded") +
                        "</option>";
                }).join("");
            videoSelect.value = "";
            videoSelect.dataset.empty = videos.length ? "false" : "true";
            videoSelect.disabled = false;
            videoOptionsLoading = false;
            videoOptionsReady = true;
            setGenerateButtonsDisabled(false);
            return loadPreview(1);
        }).catch(function (error) {
            if (requestId !== videoOptionsRequestId) return;
            videoOptionsLoading = false;
            videoOptionsReady = false;
            videoSelect.disabled = true;
            videoSelect.innerHTML = '<option value="">Videos unavailable</option>';
            videoSelect.dataset.empty = "false";
            setPreviewMessage(error.message || "Could not load videos.", "Unavailable");
        });
    }

    function renderHistory(reports) {
        if (!historyBody) return;
        if (!reports.length) {
            historyBody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">No reports generated yet.</td></tr>';
            return;
        }
        historyBody.innerHTML = reports.map(function (r) {
            return (
                "<tr>" +
                "<td>" + escapeHtml(r.name) + "</td>" +
                '<td class="small">' + escapeHtml(r.date) + "</td>" +
                '<td class="small text-muted">' + escapeHtml(r.filters) + "</td>" +
                "<td>" + formatBadge(r.type) + "</td>" +
                '<td class="small">' + escapeHtml(r.generated_by) + "</td>" +
                '<td><a class="btn btn-sm btn-outline-danger" href="' + escapeHtml(r.download_url) + '" title="Download"><i class="bi bi-download"></i></a></td>' +
                "</tr>"
            );
        }).join("");
    }

    function refreshHistory() {
        const visibility = document.getElementById("reportHistoryVisibility")?.value || "all";
        return fetch("/api/reports?visibility=" + encodeURIComponent(visibility))
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) renderHistory(payload.reports);
            });
    }

    function generateReport(format) {
        if (videoOptionsLoading || !videoOptionsReady) return;
        const body = collectFilters();
        body.format = format;
        showToast("Generating " + format.toUpperCase(), "Building the report from the violations database…", "info");

        fetch("/api/reports", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    showToast("Report Ready", payload.report.name + " (" + payload.report.type + ") is ready.", "success");
                    refreshHistory();
                    window.location.href = payload.report.download_url;
                } else {
                    showToast("Error", payload.error || "Report generation failed.", "danger");
                }
            })
            .catch(function () {
                showToast("Error", "Report generation failed. Please try again.", "danger");
            });
    }

    document.getElementById("btnGeneratePdf")?.addEventListener("click", function () {
        generateReport("pdf");
    });

    document.getElementById("btnGenerateExcel")?.addEventListener("click", function () {
        generateReport("excel");
    });

    document.getElementById("btnRefreshHistory")?.addEventListener("click", function () {
        refreshHistory().then(function () {
            showToast("Refreshed", "Report history updated.", "info");
        });
    });
    document.getElementById("reportHistoryVisibility")?.addEventListener("change", refreshHistory);
    document.getElementById("reportVideo")?.addEventListener("change", function () { loadPreview(1); });
    ["dateFrom", "dateTo", "reportViolationType", "reportStatus"].forEach(function (id) {
        document.getElementById(id)?.addEventListener("change", function () { loadPreview(1); });
    });
    document.getElementById("reportUploader")?.addEventListener("change", function () {
        refreshVideoOptions().catch(function (error) {
            setPreviewMessage(error.message || "Could not load videos.", "Unavailable");
        });
    });
    document.getElementById("reportPreviewPrevious")?.addEventListener("click", function () {
        loadPreview(Math.max(1, previewPage - 1));
    });
    document.getElementById("reportPreviewNext")?.addEventListener("click", function () {
        loadPreview(previewPage + 1);
    });
})();
