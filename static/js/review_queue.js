/**
 * TAVIDM - Review Queue (manual violation validation)
 */

function matchesReviewFilter(item, filterName) {
    const confidence = Number(item && item.confidence);
    if (filterName === "low") return confidence < 0.80;
    if (filterName === "careful") return confidence >= 0.80 && confidence < 0.95;
    return true;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = { matchesReviewFilter: matchesReviewFilter };
}

(function () {
    "use strict";

    const tbody = document.getElementById("reviewBody");
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));
    const pendingCount = document.getElementById("pendingCount");
    const filterStatus = document.getElementById("reviewFilterStatus");
    const filterButtons = Array.from(document.querySelectorAll?.("[data-review-filter]") || []);
    const escapeHtml = window.TAVIDMMainUI?.escapeHtml || function (value) {
        return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    };

    function showEvidence(item) {
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;
        const sceneImg = item.evidence_url
            ? '<img src="' + escapeHtml(item.evidence_url) + '" alt="Evidence" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-5"><i class="bi bi-image fs-1 d-block mb-2"></i>No evidence snapshot available.</div>';
        const vehicleImg = item.vehicle_evidence_url
            ? '<img src="' + escapeHtml(item.vehicle_evidence_url) + '" alt="Vehicle" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-4"><i class="bi bi-car-front fs-1 d-block mb-2"></i>No vehicle crop captured.</div>';
        const plateStatus = item.plate_status || "not_attempted";
        const plateLine = plateStatus === "recognized" && item.plate_text
            ? "Plate: " + item.plate_text
            : (plateStatus === "unreadable" ? "Plate: unreadable" : "Plate: not recognized (no ALPR in this build)");
        body.innerHTML =
            '<ul class="nav nav-tabs mb-3" role="tablist">' +
            '<li class="nav-item" role="presentation"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#rqScene" type="button" role="tab">Scene</button></li>' +
            '<li class="nav-item" role="presentation"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#rqVehicle" type="button" role="tab">Vehicle</button></li>' +
            "</ul>" +
            '<div class="tab-content">' +
            '<div class="tab-pane fade show active" id="rqScene" role="tabpanel">' + sceneImg + "</div>" +
            '<div class="tab-pane fade" id="rqVehicle" role="tabpanel">' + vehicleImg + "</div>" +
            "</div>" +
            "<p class=\"mt-3 text-muted\">" + escapeHtml(item.display_id) + " · " + escapeHtml(item.violation_type) + " · Track #" + escapeHtml(item.track_id) + "</p>" +
            "<p class=\"small text-muted\">" + escapeHtml(item.reason_log) + "</p>" +
            "<p class=\"small text-muted\">" + escapeHtml(plateLine) + "</p>" +
            "<p class=\"small\">Legal status: <strong>" + escapeHtml(item.legal_status || "—") + "</strong>" +
            (item.flag_only ? " · <span class=\"badge bg-warning text-dark\">flag_only review material</span>" : "") + "</p>" +
            "<p class=\"small text-muted\">Proposed official: " + escapeHtml(item.proposed_official_category || "—") +
            (item.verified_official_category ? " · Verified: " + escapeHtml(item.verified_official_category) : " · Verified: none") + "</p>" +
            "<p class=\"small text-muted\">Timestamp OCR: unavailable — confirm event time after case materialization.</p>";
        evidenceModal.show();
    }

    function updateCount() {
        if (!pendingCount || !tbody) return;
        const total = Math.max(0, Number(pendingCount.dataset.total || 0) - 1);
        pendingCount.dataset.total = String(total);
        pendingCount.textContent = total + " Pending";
        updateVisibleCount();
    }

    function setRowBusy(row, busy, loadingLabel) {
        row.setAttribute("aria-busy", String(busy));
        row.querySelectorAll("button").forEach(function (button) {
            button.disabled = busy;
            if (busy && button.dataset.loadingLabel) {
                button.dataset.previousAriaLabel = button.getAttribute("aria-label") || "";
                button.setAttribute("aria-label", button.dataset.loadingLabel);
            } else if (!busy && button.dataset.previousAriaLabel) {
                button.setAttribute("aria-label", button.dataset.previousAriaLabel);
                delete button.dataset.previousAriaLabel;
            }
        });
        if (busy && loadingLabel && filterStatus) filterStatus.textContent = loadingLabel;
    }

    function postAction(item, action, row, trigger) {
        setRowBusy(row, true, trigger?.dataset.loadingLabel);
        fetch("/api/review-queue/" + item.id + "/" + action, { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    row.remove();
                    updateCount();
                    if (action === "confirm") {
                        showToast("Confirmed", item.violation_type + " recorded as a violation.", "success");
                    } else {
                        showToast("Dismissed", item.violation_type + " detection dismissed.", "info");
                    }
                } else {
                    setRowBusy(row, false);
                    showToast("Error", payload.error || "Action failed.", "danger");
                }
            })
            .catch(function () {
                setRowBusy(row, false);
                showToast("Error", "Request failed. Please try again.", "danger");
            });
    }

    tbody?.addEventListener("click", function (e) {
        const row = e.target.closest("tr");
        if (!row || !row.dataset.item) return;
        let item;
        try { item = JSON.parse(row.dataset.item); } catch (err) { return; }

        const evidenceButton = e.target.closest(".btn-view-evidence");
        const approveButton = e.target.closest(".btn-approve");
        const dismissButton = e.target.closest(".btn-dismiss");
        if (evidenceButton) showEvidence(item);
        if (approveButton) {
            if (!window.confirm(approveButton.dataset.confirmMessage)) return;
            postAction(item, "confirm", row, approveButton);
        }
        if (dismissButton) postAction(item, "dismiss", row, dismissButton);
    });

    function updateVisibleCount() {
        if (!tbody || !filterStatus) return;
        const rows = Array.from(tbody.querySelectorAll("tr[data-item]"));
        const visible = rows.filter(function (row) { return row.style.display !== "none"; }).length;
        const total = Number(pendingCount?.dataset.total || 0);
        filterStatus.textContent = "Showing " + visible + " of " + rows.length + " on this page · " + total + " pending total.";
    }

    function filterRows(filterName) {
        tbody.querySelectorAll("tr[data-item]").forEach(function (row) {
            try {
                const item = JSON.parse(row.dataset.item);
                row.style.display = matchesReviewFilter(item, filterName) ? "" : "none";
            } catch (err) { row.style.display = "none"; }
        });
        filterButtons.forEach(function (button) {
            const active = button.dataset.reviewFilter === filterName;
            button.setAttribute("aria-pressed", String(active));
            button.classList.toggle("active", active);
        });
        updateVisibleCount();
    }

    filterButtons.forEach(function (button) {
        button.addEventListener("click", function () { filterRows(button.dataset.reviewFilter); });
    });

    updateVisibleCount();
})();
