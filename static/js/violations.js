/**
 * TAVIDM - Violations Page
 * Search, filter, sort, pagination, modals
 */

(function () {
    "use strict";

    const allViolations = window.TAVIDM_VIOLATIONS || [];
    let filtered = [...allViolations];
    let currentPage = 1;
    let pageSize = 10;
    let sortField = "timestamp";
    let sortDir = "desc";
    let selectedViolation = null;

    const searchInput = document.getElementById("searchInput");
    const filterType = document.getElementById("filterType");
    const filterCamera = document.getElementById("filterCamera");
    const filterStatus = document.getElementById("filterStatus");
    const pageSizeSelect = document.getElementById("pageSize");
    const tbody = document.getElementById("violationsBody");
    const pagination = document.getElementById("pagination");
    const paginationInfo = document.getElementById("paginationInfo");
    const resultCount = document.getElementById("resultCount");

    const detailModal = new bootstrap.Modal(document.getElementById("detailModal"));
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));

    function applyFilters() {
        const query = (searchInput?.value || "").toLowerCase().trim();
        const type = filterType?.value || "";
        const camera = filterCamera?.value || "";
        const status = filterStatus?.value || "";

        filtered = allViolations.filter(function (v) {
            const matchQuery = !query || [
                v.id, v.type, v.plate, v.camera_name, v.location, v.status
            ].some(function (f) { return String(f).toLowerCase().includes(query); });

            return matchQuery &&
                (!type || v.type === type) &&
                (!camera || v.camera_name === camera) &&
                (!status || v.status === status);
        });

        applySort();
        currentPage = 1;
        render();
    }

    function applySort() {
        filtered.sort(function (a, b) {
            let valA, valB;
            switch (sortField) {
                case "confidence":
                    valA = a.confidence; valB = b.confidence; break;
                case "timestamp":
                    valA = a.timestamp_iso; valB = b.timestamp_iso; break;
                case "camera":
                    valA = a.camera_name; valB = b.camera_name; break;
                default:
                    valA = a[sortField] || ""; valB = b[sortField] || "";
            }
            if (valA < valB) return sortDir === "asc" ? -1 : 1;
            if (valA > valB) return sortDir === "asc" ? 1 : -1;
            return 0;
        });
    }

    function getConfidenceClass(c) {
        if (c >= 0.9) return "high";
        if (c >= 0.8) return "medium";
        return "low";
    }

    function render() {
        const start = (currentPage - 1) * pageSize;
        const end = start + pageSize;
        const page = filtered.slice(start, end);

        if (resultCount) resultCount.textContent = filtered.length;

        if (tbody) {
            tbody.innerHTML = page.map(function (v) {
                return (
                    "<tr data-violation='" + JSON.stringify(v).replace(/'/g, "&#39;") + "'>" +
                    "<td><code>" + v.id + "</code></td>" +
                    "<td>" + v.type + "</td>" +
                    "<td><span class=\"plate-badge\">" + v.plate + "</span></td>" +
                    "<td>" + v.camera_name + "</td>" +
                    "<td>" + v.timestamp + "</td>" +
                    "<td><span class=\"confidence-badge confidence-" + getConfidenceClass(v.confidence) + "\">" +
                    Math.round(v.confidence * 100) + "%</span></td>" +
                    "<td><span class=\"badge status-badge status-" + v.status.toLowerCase() + "\">" + v.status + "</span></td>" +
                    "<td><div class=\"btn-group btn-group-sm\">" +
                    "<button class=\"btn btn-outline-primary btn-view-detail\" title=\"View Details\"><i class=\"bi bi-eye\"></i></button>" +
                    "<button class=\"btn btn-outline-secondary btn-view-evidence\" title=\"View Evidence\"><i class=\"bi bi-image\"></i></button>" +
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
        let html = "";

        html += '<li class="page-item ' + (currentPage === 1 ? "disabled" : "") + '">' +
            '<a class="page-link" href="#" data-page="' + (currentPage - 1) + '">&laquo;</a></li>';

        for (let i = 1; i <= totalPages; i++) {
            if (totalPages > 7 && Math.abs(i - currentPage) > 2 && i !== 1 && i !== totalPages) {
                if (i === 2 || i === totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
                continue;
            }
            html += '<li class="page-item ' + (i === currentPage ? "active" : "") + '">' +
                '<a class="page-link" href="#" data-page="' + i + '">' + i + "</a></li>";
        }

        html += '<li class="page-item ' + (currentPage === totalPages ? "disabled" : "") + '">' +
            '<a class="page-link" href="#" data-page="' + (currentPage + 1) + '">&raquo;</a></li>';

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
            detailField("Type", v.type) +
            detailField("Plate Number", '<span class="plate-badge">' + v.plate + "</span>") +
            detailField("Severity", v.severity) +
            detailField("Camera", v.camera_name) +
            detailField("Location", v.location) +
            detailField("Timestamp", v.timestamp) +
            detailField("Confidence", Math.round(v.confidence * 100) + "%") +
            detailField("Status", '<span class="badge status-badge status-' + v.status.toLowerCase() + '">' + v.status + "</span>") +
            detailField("Notes", v.notes, true) +
            "</div>";

        detailModal.show();
    }

    function detailField(label, value, full) {
        return '<div class="detail-item"' + (full ? ' style="grid-column:1/-1"' : "") + ">" +
            "<label>" + label + "</label><span>" + value + "</span></div>";
    }

    function showEvidence(v) {
        selectedViolation = v;
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;

        body.innerHTML =
            '<img src="/static/images/' + v.evidence_image + '" alt="Evidence for ' + v.id + '" class="evidence-preview">' +
            '<p class="mt-3 text-muted">' + v.id + " · " + v.type + " · " + v.plate + " · " + v.timestamp + "</p>";

        evidenceModal.show();
    }

    // Event listeners
    [searchInput, filterType, filterCamera, filterStatus].forEach(function (el) {
        if (el) el.addEventListener("input", applyFilters);
        if (el && el.tagName === "SELECT") el.addEventListener("change", applyFilters);
    });

    document.getElementById("resetFilters")?.addEventListener("click", function () {
        if (searchInput) searchInput.value = "";
        if (filterType) filterType.value = "";
        if (filterCamera) filterCamera.value = "";
        if (filterStatus) filterStatus.value = "";
        applyFilters();
    });

    pageSizeSelect?.addEventListener("change", function () {
        pageSize = parseInt(this.value, 10);
        currentPage = 1;
        render();
    });

    document.querySelectorAll(".sortable").forEach(function (th) {
        th.addEventListener("click", function () {
            const field = this.dataset.sort;
            if (sortField === field) {
                sortDir = sortDir === "asc" ? "desc" : "asc";
            } else {
                sortField = field;
                sortDir = "asc";
            }
            document.querySelectorAll(".sortable").forEach(function (el) {
                el.classList.remove("sorted-asc", "sorted-desc");
            });
            this.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
            applySort();
            render();
        });
    });

    pagination?.addEventListener("click", function (e) {
        e.preventDefault();
        const link = e.target.closest("[data-page]");
        if (!link) return;
        const page = parseInt(link.dataset.page, 10);
        const totalPages = Math.ceil(filtered.length / pageSize);
        if (page >= 1 && page <= totalPages) {
            currentPage = page;
            render();
        }
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
        if (selectedViolation) {
            detailModal.hide();
            setTimeout(function () { showEvidence(selectedViolation); }, 300);
        }
    });

    document.getElementById("btnDownloadEvidence")?.addEventListener("click", function () {
        showToast("Download", "Evidence image download started (demo).", "success");
    });

    applyFilters();
})();
