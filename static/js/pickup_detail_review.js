(() => {
    "use strict";

    const selectAll = document.getElementById("pickupSelectAll");
    const bulkButton = document.getElementById("pickupBulkDismiss");
    const bulkStatus = document.getElementById("pickupBulkStatus");
    const selectedItems = () => Array.from(document.querySelectorAll(".pickup-review-item .pickup-select-item:checked"));
    const refreshBulkControls = () => {
        const count = selectedItems().length;
        if (bulkButton) {
            bulkButton.disabled = count === 0;
            bulkButton.querySelector("[data-selected-count]").textContent = String(count);
        }
        const eligible = Array.from(document.querySelectorAll(".pickup-review-item .pickup-select-item:not(:disabled)"));
        if (selectAll) {
            selectAll.disabled = eligible.length === 0;
            selectAll.checked = eligible.length > 0 && eligible.every((checkbox) => checkbox.checked);
            selectAll.indeterminate = count > 0 && count < eligible.length;
        }
    };

    document.addEventListener("change", (event) => {
        if (event.target.matches(".pickup-select-item")) refreshBulkControls();
        if (event.target === selectAll) {
            document.querySelectorAll(".pickup-review-item .pickup-select-item:not(:disabled)")
                .forEach((checkbox) => { checkbox.checked = selectAll.checked; });
            refreshBulkControls();
        }
    });

    bulkButton?.addEventListener("click", async () => {
        const checkboxes = selectedItems();
        if (!checkboxes.length) return;
        if (!window.confirm(`Dismiss ${checkboxes.length} selected pending pickup review item(s)? The evidence records will be kept.`)) return;
        bulkButton.disabled = true;
        let dismissed = 0;
        const failures = [];
        for (const checkbox of checkboxes) {
            const card = checkbox.closest(".pickup-review-item");
            try {
                const response = await fetch(`/api/pickup-detail-review/${card.dataset.candidateId}/outcome`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ outcome: "dismissed", notes: "", expected_outcome: "pending" })
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.error || "Could not dismiss item.");
                dismissed += 1;
            } catch (error) {
                failures.push(`${card.querySelector("code")?.textContent || card.dataset.candidateId}: ${error.message}`);
            }
        }
        if (bulkStatus) {
            bulkStatus.textContent = failures.length
                ? `Dismissed ${dismissed}; ${failures.length} failed. Refreshing to show current outcomes. ${failures.join("; ")}`
                : `Dismissed ${dismissed} item(s). Refreshing the queue.`;
        }
        window.TAVIDMRefreshNavCounts?.(true);
        window.setTimeout(() => window.location.reload(), 900);
    });

    refreshBulkControls();

    document.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-pickup-outcome]");
        if (!button) return;
        const card = button.closest(".pickup-review-item");
        if (!card) return;
        const status = card.querySelector("[data-save-status]");
        const notes = card.querySelector("textarea");
        const outcome = button.dataset.pickupOutcome;
        button.disabled = true;
        if (status) status.textContent = "Saving review outcome…";
        try {
            const response = await fetch(`/api/pickup-detail-review/${card.dataset.candidateId}/outcome`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    outcome,
                    notes: notes ? notes.value : "",
                    expected_outcome: card.dataset.outcome || "pending"
                })
            });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.error || "Could not save outcome.");
            card.dataset.outcome = result.outcome;
            const badge = card.querySelector("[data-outcome]");
            if (badge) badge.textContent = result.outcome;
            const selector = card.querySelector(".pickup-select-item");
            if (selector && result.outcome !== "pending") {
                selector.checked = false;
                selector.disabled = true;
                refreshBulkControls();
            }
            if (status) status.textContent = `Saved as ${result.outcome}. This does not create a violation case.`;
            window.TAVIDMRefreshNavCounts?.(true);
        } catch (error) {
            if (status) status.textContent = error.message || "Could not save outcome.";
        } finally {
            button.disabled = false;
        }
    });
})();
