/**
 * TAVIDM - Review Queue (manual violation validation)
 */

(function () {
    "use strict";

    const tbody = document.getElementById("reviewBody");
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));
    const pendingCount = document.getElementById("pendingCount");

    function showEvidence(item) {
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;
        const sceneImg = item.evidence_url
            ? '<img src="' + item.evidence_url + '" alt="Evidence" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-5"><i class="bi bi-image fs-1 d-block mb-2"></i>No evidence snapshot available.</div>';
        const vehicleImg = item.vehicle_evidence_url
            ? '<img src="' + item.vehicle_evidence_url + '" alt="Vehicle" class="evidence-preview img-fluid rounded">'
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
            "<p class=\"mt-3 text-muted\">" + item.display_id + " · " + item.violation_type + " · Track #" + item.track_id + "</p>" +
            "<p class=\"small text-muted\">" + item.reason_log + "</p>" +
            "<p class=\"small text-muted\">" + plateLine + "</p>";
        evidenceModal.show();
    }

    function updateCount() {
        if (!pendingCount || !tbody) return;
        const remaining = tbody.querySelectorAll("tr[data-item]").length;
        pendingCount.textContent = remaining + " Pending";
    }

    function postAction(item, action, row) {
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
                    showToast("Error", payload.error || "Action failed.", "danger");
                }
            })
            .catch(function () {
                showToast("Error", "Request failed. Please try again.", "danger");
            });
    }

    tbody?.addEventListener("click", function (e) {
        const row = e.target.closest("tr");
        if (!row || !row.dataset.item) return;
        let item;
        try { item = JSON.parse(row.dataset.item); } catch (err) { return; }

        if (e.target.closest(".btn-view-evidence")) showEvidence(item);
        if (e.target.closest(".btn-approve")) postAction(item, "confirm", row);
        if (e.target.closest(".btn-dismiss")) postAction(item, "dismiss", row);
    });

    // Confidence-band filters (manuscript manual-review policy).
    document.getElementById("filterCareful")?.addEventListener("click", function () {
        filterRows(function (item) { return item.confidence >= 0.80 && item.confidence < 0.95; });
    });

    document.getElementById("filterLowConf")?.addEventListener("click", function () {
        filterRows(function (item) { return item.confidence < 0.80; });
    });

    document.getElementById("filterAll")?.addEventListener("click", function () {
        tbody.querySelectorAll("tr").forEach(function (row) { row.style.display = ""; });
    });

    function filterRows(fn) {
        tbody.querySelectorAll("tr[data-item]").forEach(function (row) {
            try {
                const item = JSON.parse(row.dataset.item);
                row.style.display = fn(item) ? "" : "none";
            } catch (err) { row.style.display = "none"; }
        });
    }
})();
