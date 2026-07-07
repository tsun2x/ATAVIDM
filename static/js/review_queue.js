/**
 * TAVIDM - Review Queue (Admin)
 */

(function () {
    "use strict";

    const tbody = document.getElementById("reviewBody");
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));
    const forceReviewTypes = ["Reckless Driving"];

    function showEvidence(item) {
        const body = document.getElementById("evidenceModalBody");
        if (!body) return;
        body.innerHTML =
            '<img src="/static/images/' + item.evidence_image + '" alt="Evidence" class="evidence-preview">' +
            "<p class=\"mt-3 text-muted\">" + item.id + " · " + item.violation_type + " · Track #" + item.track_id + "</p>" +
            "<p class=\"small text-muted\">" + item.reason_log + "</p>";
        evidenceModal.show();
    }

    tbody?.addEventListener("click", function (e) {
        const row = e.target.closest("tr");
        if (!row) return;
        let item;
        try { item = JSON.parse(row.dataset.item); } catch (err) { return; }

        if (e.target.closest(".btn-view-evidence")) showEvidence(item);

        if (e.target.closest(".btn-approve")) {
            row.remove();
            showToast("Approved", item.violation_type + " confirmed as violation.", "success");
        }

        if (e.target.closest(".btn-dismiss")) {
            row.remove();
            showToast("Dismissed", item.violation_type + " detection dismissed.", "info");
        }
    });

    document.getElementById("filterForceReview")?.addEventListener("click", function () {
        filterRows(function (item) { return forceReviewTypes.includes(item.violation_type); });
    });

    document.getElementById("filterLowConf")?.addEventListener("click", function () {
        filterRows(function (item) { return item.confidence < 0.75; });
    });

    document.getElementById("filterAll")?.addEventListener("click", function () {
        tbody.querySelectorAll("tr").forEach(function (row) { row.style.display = ""; });
    });

    function filterRows(fn) {
        tbody.querySelectorAll("tr").forEach(function (row) {
            try {
                const item = JSON.parse(row.dataset.item);
                row.style.display = fn(item) ? "" : "none";
            } catch (err) { row.style.display = "none"; }
        });
    }
})();
