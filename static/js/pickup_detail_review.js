(() => {
    "use strict";

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
            if (status) status.textContent = `Saved as ${result.outcome}. This does not create a violation case.`;
            window.TAVIDMRefreshNavCounts?.(true);
        } catch (error) {
            if (status) status.textContent = error.message || "Could not save outcome.";
        } finally {
            button.disabled = false;
        }
    });
})();
