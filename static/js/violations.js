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
                return (
                    "<tr data-violation='" + JSON.stringify(v).replace(/'/g, "&#39;") + "'>" +
                    "<td><code>" + v.id + "</code></td>" +
                    '<td><span class="vtype-badge vtype-' + v.type_slug + '">' + v.type + "</span></td>" +
                    '<td class="small">' + v.video_name + "</td>" +
                    "<td>" + v.timestamp + "</td>" +
                    '<td><span class="confidence-badge confidence-' + getConfidenceClass(v.confidence) + '">' +
                    Math.round(v.confidence * 100) + "%</span></td>" +
                    '<td><span class="badge status-badge status-' + v.status + '">' + v.status + "</span></td>" +
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
        body.innerHTML =
            '<div class="detail-grid">' +
            detailField("Violation ID", v.id) +
            detailField("Type", '<span class="vtype-badge vtype-' + v.type_slug + '">' + v.type + "</span>") +
            detailField("Track ID", "#" + v.track_id) +
            detailField("Video Source", v.video_name) +
            detailField("Condition", v.condition) +
            detailField("Frame", v.frame_number) +
            detailField("Timestamp", v.timestamp) +
            detailField("Confidence", Math.round(v.confidence * 100) + "%") +
            detailField("Status", '<span class="badge status-badge status-' + v.status + '">' + v.status + "</span>") +
            detailField("Reason Log", v.reason_log, true) +
            "</div>";
        detailModal.show();
    }

    function detailField(label, value, full) {
        return '<div class="detail-item"' + (full ? ' style="grid-column:1/-1"' : "") + "><label>" + label + "</label><span>" + value + "</span></div>";
    }

    function showEvidence(v) {
        selectedViolation = v;
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;
        const image = v.evidence_url
            ? '<img src="' + v.evidence_url + '" alt="Evidence" class="evidence-preview">'
            : '<div class="text-muted py-5"><i class="bi bi-image fs-1 d-block mb-2"></i>No evidence snapshot available.</div>';
        body.innerHTML =
            image +
            "<p class=\"mt-3 text-muted\">" + v.id + " · " + v.type + " · Track #" + v.track_id + " · " + v.timestamp + "</p>";
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
