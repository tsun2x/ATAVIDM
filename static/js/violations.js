/**
 * TAVIDM - Violations Page
 */

(function () {
    "use strict";

    const allViolations = window.TAVIDM_VIOLATIONS || [];
    let filtered = [...allViolations];
    let currentPage = 1;
    let pageSize = 8;
    let sortField = "timestamp";
    let sortDir = "desc";
    let selectedViolation = null;

    const searchInput = document.getElementById("searchInput");
    const filterType = document.getElementById("filterType");
    const filterVideo = document.getElementById("filterVideo");
    const filterStatus = document.getElementById("filterStatus");
    const filterDate = document.getElementById("filterDate");
    const sortSelect = document.getElementById("sortSelect");
    const pageSizeSelect = document.getElementById("pageSize");
    const tbody = document.getElementById("violationsBody");
    const pagination = document.getElementById("pagination");
    const paginationInfo = document.getElementById("paginationInfo");
    const resultCount = document.getElementById("resultCount");

    const detailModal = new bootstrap.Modal(document.getElementById("detailModal"));
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));

    function getConfidenceClass(c) {
        if (c >= 0.85) return "high";
        if (c >= 0.65) return "medium";
        return "low";
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function plateBadge(v) {
        const status = v.plate_status || "not_attempted";
        if ((status === "recognized" || status === "verified_readable") && v.plate_text) {
            return '<span class="badge bg-success-subtle text-success" title="Verified / recognized plate">' +
                escapeHtml(v.plate_text) + "</span>";
        }
        if (status === "candidate_awaiting_verification") {
            return '<span class="badge bg-info-subtle text-info" title="OCR candidate awaiting review">Candidate</span>';
        }
        if (status === "unreadable" || status === "unclear") {
            return '<span class="badge bg-warning-subtle text-warning" title="Plate unclear / unreadable">Unclear</span>';
        }
        if (status === "not_visible") {
            return '<span class="badge bg-secondary-subtle text-secondary" title="Plate not visible">Not visible</span>';
        }
        return '<span class="badge bg-secondary-subtle text-secondary" title="No plate recognition attempted">N/A</span>';
    }

    function evidenceImage(url, caption) {
        if (!url) {
            return '<div class="text-muted py-5"><i class="bi bi-image fs-1 d-block mb-2"></i>' +
                (caption || "No evidence snapshot available.") + "</div>";
        }
        return '<img src="' + url + '" alt="' + (caption || "Evidence") +
            '" class="evidence-preview img-fluid rounded">';
    }

    function applyFilters() {
        const query = (searchInput?.value || "").toLowerCase().trim();
        const type = filterType?.value || "";
        const video = filterVideo?.value || "";
        const status = filterStatus?.value || "";
        const date = filterDate?.value || "";

        filtered = allViolations.filter(function (v) {
            const matchQuery = !query || [v.id, v.type, v.video_name, v.status, v.track_id]
                .some(function (f) { return String(f).toLowerCase().includes(query); });
            const matchDate = !date || v.timestamp.startsWith(date);
            return matchQuery && matchDate &&
                (!type || v.type === type) &&
                (!video || v.video_name === video) &&
                (!status || v.status === status);
        });

        applySort();
        currentPage = 1;
        render();
    }

    function applySort() {
        const sortVal = sortSelect?.value || "newest";
        if (sortVal === "newest") { sortField = "timestamp"; sortDir = "desc"; }
        else if (sortVal === "oldest") { sortField = "timestamp"; sortDir = "asc"; }
        else if (sortVal === "confidence_desc") { sortField = "confidence"; sortDir = "desc"; }
        else if (sortVal === "confidence_asc") { sortField = "confidence"; sortDir = "asc"; }
        else if (sortVal === "type") { sortField = "type"; sortDir = "asc"; }

        filtered.sort(function (a, b) {
            let valA, valB;
            switch (sortField) {
                case "confidence": valA = a.confidence; valB = b.confidence; break;
                case "timestamp": valA = a.timestamp_iso; valB = b.timestamp_iso; break;
                case "video": valA = a.video_name; valB = b.video_name; break;
                default: valA = a[sortField] || ""; valB = b[sortField] || "";
            }
            if (valA < valB) return sortDir === "asc" ? -1 : 1;
            if (valA > valB) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
    }

    function render() {
        const start = (currentPage - 1) * pageSize;
        const page = filtered.slice(start, start + pageSize);
        if (resultCount) resultCount.textContent = filtered.length;

        if (tbody) {
            tbody.innerHTML = page.map(function (v) {
                const plate = plateBadge(v);
                return (
                    "<tr data-violation='" + JSON.stringify(v).replace(/'/g, "&#39;") + "'>" +
                    "<td><code>" + v.id + "</code></td>" +
                    '<td><span class="vtype-badge vtype-' + v.type_slug + '">' + v.type + "</span></td>" +
                    '<td class="small">' + v.video_name + "</td>" +
                    "<td>" + v.timestamp + "</td>" +
                    '<td><span class="confidence-badge confidence-' + getConfidenceClass(v.confidence) + '">' +
                    Math.round(v.confidence * 100) + "%</span></td>" +
                    '<td><span class="badge status-badge status-' + v.status + '">' + v.status + "</span></td>" +
                    '<td class="small">' + (v.vehicle_class && v.vehicle_class !== "—" ? escapeHtml(v.vehicle_class) : "—") + "</td>" +
                    "<td>" + plate + "</td>" +
                    '<td><div class="btn-group btn-group-sm">' +
                    '<button class="btn btn-outline-danger btn-view-detail"><i class="bi bi-eye"></i></button>' +
                    '<button class="btn btn-outline-secondary btn-view-evidence"><i class="bi bi-image"></i></button>' +
                    "</div></td></tr>"
                );
            }).join("");
        }
        renderPagination();
        updatePaginationInfo();
    }

    function renderPagination() {
        if (!pagination) return;
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        let html = '<li class="page-item ' + (currentPage === 1 ? "disabled" : "") + '"><a class="page-link" href="#" data-page="' + (currentPage - 1) + '">&laquo;</a></li>';
        for (let i = 1; i <= totalPages; i++) {
            if (totalPages > 7 && Math.abs(i - currentPage) > 2 && i !== 1 && i !== totalPages) {
                if (i === 2 || i === totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
                continue;
            }
            html += '<li class="page-item ' + (i === currentPage ? "active" : "") + '"><a class="page-link" href="#" data-page="' + i + '">' + i + "</a></li>";
        }
        html += '<li class="page-item ' + (currentPage === totalPages ? "disabled" : "") + '"><a class="page-link" href="#" data-page="' + (currentPage + 1) + '">&raquo;</a></li>';
        pagination.innerHTML = html;
    }

    function updatePaginationInfo() {
        if (!paginationInfo) return;
        const start = filtered.length ? (currentPage - 1) * pageSize + 1 : 0;
        const end = Math.min(currentPage * pageSize, filtered.length);
        paginationInfo.textContent = "Showing " + start + "\u2013" + end + " of " + filtered.length;
    }

    function showDetail(v) {
        selectedViolation = v;
        const body = document.getElementById("detailModalBody");
        if (!body) return;
        const legalBadge = v.flag_only
            ? '<span class="badge bg-warning text-dark">flag_only (review material)</span>'
            : escapeHtml(v.legal_status || "—");
        const official = v.verified_official_category
            ? escapeHtml(v.verified_official_category) + ' <span class="badge bg-success">verified</span>'
            : (v.proposed_official_category
                ? escapeHtml(v.proposed_official_category) + ' <span class="badge bg-secondary">proposed</span>'
                : "—");
        body.innerHTML =
            '<div class="detail-grid">' +
            detailField("Violation ID", v.id) +
            detailField("Canonical Type", '<span class="vtype-badge vtype-' + v.type_slug + '">' + escapeHtml(v.type) + "</span>") +
            detailField("Official Category", official) +
            detailField("Legal Status", legalBadge) +
            detailField("Contributing Behaviors", escapeHtml((v.contributing_behaviors || []).join("; ") || v.type)) +
            detailField("Track ID", "#" + v.track_id) +
            detailField("Video Source", escapeHtml(v.video_name)) +
            detailField("Condition", escapeHtml(v.condition)) +
            detailField("Vehicle Class", v.vehicle_class && v.vehicle_class !== "—" ? escapeHtml(v.vehicle_class) : "—") +
            detailField("Plate", plateBadge(v)) +
            detailField("Event Time", escapeHtml(v.event_time || "Not confirmed (timestamp OCR unavailable)")) +
            detailField("Case Confirmed", v.case_confirmed ? "Yes" : "No") +
            detailField("Notice Printed", v.notice_printed ? "Yes (attested)" : "No") +
            detailField("Frame", v.frame_number) +
            detailField("Detected At", escapeHtml(v.timestamp)) +
            detailField("Confidence", Math.round(v.confidence * 100) + "%") +
            detailField("Status", '<span class="badge status-badge status-' + v.status + '">' + v.status + "</span>") +
            detailField("Reason Log", escapeHtml(v.reason_log || ""), true) +
            "</div>" +
            '<hr><div class="d-flex flex-wrap gap-2" id="caseActionBar">' +
            '<button type="button" class="btn btn-sm btn-outline-primary" id="btnVerifyPlate">Verify Plate</button>' +
            '<button type="button" class="btn btn-sm btn-outline-primary" id="btnConfirmEventTime">Confirm Event Time</button>' +
            '<button type="button" class="btn btn-sm btn-success" id="btnConfirmCase">Confirm Case</button>' +
            '<button type="button" class="btn btn-sm btn-outline-secondary" id="btnPrintable">Printable Record</button>' +
            '<button type="button" class="btn btn-sm btn-warning" id="btnAttestPrint">Attest Notice Printed</button>' +
            "</div>" +
            '<p class="small text-muted mt-2 mb-0">Preview/PDF/download do not establish Notice Printed. Flag-only and unverified mappings remain review material.</p>';
        detailModal.show();
        wireCaseActions(v);
    }

    function wireCaseActions(v) {
        const id = v.db_id;
        document.getElementById("btnVerifyPlate")?.addEventListener("click", function () {
            const text = window.prompt("Accepted plate text (all characters must be visibly readable). Leave blank to mark unclear.");
            const payload = text
                ? { plate_status: "verified_readable", accepted_plate_text: text }
                : { plate_status: "unclear" };
            postJson("/api/cases/" + id + "/plate", payload);
        });
        document.getElementById("btnConfirmEventTime")?.addEventListener("click", function () {
            const raw = window.prompt(
                "Event time (ISO-8601 with explicit offset, e.g. 2026-01-15T10:30:00+08:00).\nAutomatic timestamp OCR is unavailable."
            );
            if (!raw) return;
            postJson("/api/cases/" + id + "/event-time", { event_time: raw });
        });
        document.getElementById("btnConfirmCase")?.addEventListener("click", function () {
            postJson("/api/cases/" + id + "/confirm-case", {});
        });
        document.getElementById("btnPrintable")?.addEventListener("click", function () {
            fetch("/api/cases/" + id + "/printable")
                .then(function (r) { return r.json(); })
                .then(function (payload) {
                    if (!payload.success) {
                        showToast("Error", payload.error || "Failed", "danger");
                        return;
                    }
                    const kind = payload.document_kind === "official_notice_draft"
                        ? "Official notice draft (not yet attested as printed)"
                        : "Review material only — not an official citation";
                    showToast("Printable Record", kind, "info");
                });
        });
        document.getElementById("btnAttestPrint")?.addEventListener("click", function () {
            if (!window.confirm("Attest that printing of this notice actually occurred?")) return;
            postJson("/api/cases/" + id + "/notice-printed", {});
        });
    }

    function postJson(url, body) {
        fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
        })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (res.j.success) {
                    showToast("Saved", "Case update recorded.", "success");
                } else {
                    showToast("Error", res.j.error || "Action failed.", "danger");
                }
            })
            .catch(function () {
                showToast("Error", "Request failed.", "danger");
            });
    }

    function detailField(label, value, full) {
        return '<div class="detail-item"' + (full ? ' style="grid-column:1/-1"' : "") + "><label>" + label + "</label><span>" + value + "</span></div>";
    }

    function showEvidence(v) {
        selectedViolation = v;
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;
        const sceneImg = evidenceImage(v.evidence_url, "No scene evidence snapshot available.");
        const vehicleImg = evidenceImage(v.vehicle_evidence_url, "No vehicle crop captured for this detection.");
        body.innerHTML =
            '<ul class="nav nav-tabs mb-3" role="tablist">' +
            '<li class="nav-item" role="presentation"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#paneScene" type="button" role="tab">Scene</button></li>' +
            '<li class="nav-item" role="presentation"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#paneVehicle" type="button" role="tab">Vehicle</button></li>' +
            "</ul>" +
            '<div class="tab-content">' +
            '<div class="tab-pane fade show active" id="paneScene" role="tabpanel">' + sceneImg +
            "<p class=\"mt-2 text-muted small\">" + v.id + " · " + v.type + " · Track #" + v.track_id + " · " + v.timestamp + "</p></div>" +
            '<div class="tab-pane fade" id="paneVehicle" role="tabpanel">' + vehicleImg +
            "<p class=\"mt-2 text-muted small\">Vehicle/object crop. Plate recognition is not performed in this build.</p></div>" +
            "</div>";
        evidenceModal.show();
    }

    [searchInput, filterType, filterVideo, filterStatus, filterDate].forEach(function (el) {
        if (!el) return;
        el.addEventListener("input", applyFilters);
        if (el.tagName === "SELECT") el.addEventListener("change", applyFilters);
    });

    sortSelect?.addEventListener("change", function () { applySort(); render(); });

    document.getElementById("resetFilters")?.addEventListener("click", function () {
        [searchInput, filterType, filterVideo, filterStatus, filterDate].forEach(function (el) { if (el) el.value = ""; });
        applyFilters();
    });

    pageSizeSelect?.addEventListener("change", function () {
        pageSize = parseInt(this.value, 10);
        currentPage = 1;
        render();
    });

    pagination?.addEventListener("click", function (e) {
        e.preventDefault();
        const link = e.target.closest("[data-page]");
        if (!link) return;
        const page = parseInt(link.dataset.page, 10);
        const totalPages = Math.ceil(filtered.length / pageSize);
        if (page >= 1 && page <= totalPages) { currentPage = page; render(); }
    });

    tbody?.addEventListener("click", function (e) {
        const row = e.target.closest("tr");
        if (!row) return;
        let v;
        try { v = JSON.parse(row.dataset.violation); } catch (err) { return; }
        if (e.target.closest(".btn-view-detail")) showDetail(v);
        if (e.target.closest(".btn-view-evidence")) showEvidence(v);
    });

    document.getElementById("btnViewEvidenceFromDetail")?.addEventListener("click", function () {
        if (selectedViolation) { detailModal.hide(); setTimeout(function () { showEvidence(selectedViolation); }, 300); }
    });

    // Prefill search from ?q= (global navbar search redirects here).
    const urlQuery = new URLSearchParams(window.location.search).get("q");
    if (urlQuery && searchInput) searchInput.value = urlQuery;

    applyFilters();
})();
