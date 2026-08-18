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
        const image = item.evidence_url
            ? '<img src="' + item.evidence_url + '" alt="Evidence" class="evidence-preview">'
            : '<div class="text-muted py-5"><i class="bi bi-image fs-1 d-block mb-2"></i>No evidence snapshot available.</div>';
        body.innerHTML =
            image +
            "<p class=\"mt-3 text-muted\">" + item.display_id + " · " + item.violation_type + " · Track #" + item.track_id + "</p>" +
            "<p class=\"small text-muted\">" + item.reason_log + "</p>";
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
