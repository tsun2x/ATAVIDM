/**
 * TAVIDM - Review Queue (manual violation validation)
 */

// --- Experimental local plate OCR: pure presentation helpers -------------
// Kept outside the DOM guard so the escaping and authorization rules can be
// unit-tested without a browser.
//
// These states are MACHINE outcomes. None of them is a human plate status, and
// none of them may ever be phrased as "no plate exists" or as a human
// "not visible" judgement.
const PLATE_MACHINE_STATE_COPY = {
    disabled: { label: "Disabled", css: "text-bg-secondary", text: "Experimental local OCR is disabled in this build. No machine processing is running." },
    none: { label: "No attempt", css: "text-bg-secondary", text: "No machine plate attempt is recorded for this observation." },
    queued: { label: "Queued", css: "text-bg-info", text: "Machine plate attempt is queued on the bounded local inference worker." },
    processing: { label: "Processing", css: "text-bg-info", text: "Machine plate attempt is running on the bounded local inference worker." },
    unavailable: { label: "Unavailable", css: "text-bg-warning", text: "Local plate OCR artifacts or the requested execution provider are unavailable. Nothing was downloaded and no fallback provider was used." },
    failed: { label: "Failed", css: "text-bg-danger", text: "Local plate OCR failed while running. No machine candidate is available." },
    no_candidate_detected: { label: "No candidate detected", css: "text-bg-secondary", text: "No plate candidate was detected in the selected evidence. This does not mean no plate exists and is not a human 'not visible' judgement." },
    detected_unreadable: { label: "Plate detected; text unreadable", css: "text-bg-warning", text: "A plate region was detected and passed the quality gate, but no text could be read." },
    quality_rejected: { label: "Quality rejected", css: "text-bg-warning", text: "A plate region was detected but rejected by the quality gate before recognition." },
    association_uncertain: { label: "Association uncertain", css: "text-bg-danger", text: "Vehicle-to-plate association is uncertain for this observation. Any candidate is not eligible for confirmation." },
    cancelled: { label: "Cancelled", css: "text-bg-secondary", text: "Collection ended before the machine attempt could complete." },
    budget_exhausted: { label: "Budget exhausted", css: "text-bg-warning", text: "The configured scheduling/work budget was exhausted. Partial results, if any, are shown below." },
    candidate_found: { label: "Machine candidate", css: "text-bg-warning", text: "Machine candidate text below. It is unverified until an administrator explicitly confirms it." },
};

function plateMachineStateLabel(state) {
    const copy = PLATE_MACHINE_STATE_COPY[state];
    if (copy) return copy.label;
    return "Unknown";
}

function plateMachineStateCopy(state) {
    return PLATE_MACHINE_STATE_COPY[state] || {
        label: "Unknown",
        css: "text-bg-secondary",
        text: "Unrecognized machine state; no conclusion can be drawn.",
    };
}

/**
 * Decide whether machine-candidate confirmation controls may be rendered.
 *
 * Requires BOTH an administrator viewer AND a machine-eligible outcome. This is
 * presentation only; the server enforces the same rule independently.
 */
function plateConfirmControlsAllowed(machine, isAdmin) {
    if (!isAdmin) return false;
    if (!machine) return false;
    if (machine.association_uncertain === true) return false;
    if (machine.eligible_for_confirmation !== true) return false;
    return Array.isArray(machine.candidates) && machine.candidates.length > 0;
}

function escapePlateText(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function matchesReviewFilter(item, filterName) {
    const confidence = Number(item && item.confidence);
    if (filterName === "low") return confidence < 0.80;
    if (filterName === "careful") return confidence >= 0.80 && confidence < 0.95;
    return true;
}

const REVIEW_DECISION_LABELS = {
    confirm_proposed: "Confirmed proposed violation",
    correct_canonical: "Corrected canonical violation",
    no_violation: "No violation",
    insufficient_evidence: "Insufficient evidence",
};

function reviewDecisionLabel(decision) {
    return REVIEW_DECISION_LABELS[decision] || "";
}

function planReviewQueueAction(action) {
    if (action === "confirm") {
        return { urlAction: "confirm", decision: "confirm_proposed" };
    }
    if (action === "dismiss") {
        return { urlAction: "dismiss", decision: "no_violation" };
    }
    if (action === "decide") {
        return { urlAction: "decision", decision: null };
    }
    return null;
}

function reviewActionClosesRow(payload) {
    if (!payload || payload.success !== true) return false;
    if (payload.decision === "insufficient_evidence"
        && payload.disposition_label === "No violation") {
        return false;
    }
    return true;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        matchesReviewFilter: matchesReviewFilter,
        reviewDecisionLabel: reviewDecisionLabel,
        REVIEW_DECISION_LABELS: REVIEW_DECISION_LABELS,
        planReviewQueueAction: planReviewQueueAction,
        reviewActionClosesRow: reviewActionClosesRow,
        PLATE_MACHINE_STATE_COPY: PLATE_MACHINE_STATE_COPY,
        plateMachineStateLabel: plateMachineStateLabel,
        plateMachineStateCopy: plateMachineStateCopy,
        plateConfirmControlsAllowed: plateConfirmControlsAllowed,
        escapePlateText: escapePlateText,
    };
}

if (typeof document !== "undefined") {
(function () {
    "use strict";

    const tbody = document.getElementById("reviewBody");
    const evidenceModal = new bootstrap.Modal(document.getElementById("evidenceModal"));
    const decisionModalEl = document.getElementById("decisionModal");
    const decisionModal = decisionModalEl ? new bootstrap.Modal(decisionModalEl) : null;
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
        const clipPane = item.evidence_clip_url
            ? '<video controls preload="metadata" class="evidence-preview w-100 rounded" src="' + escapeHtml(item.evidence_clip_url) + '">Your browser does not support evidence video playback.</video>'
            : '<div class="text-muted py-5"><i class="bi bi-film fs-1 d-block mb-2"></i>No finalized evidence clip available.</div>';
        const clipTab = '<li class="nav-item" role="presentation"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#rqClip" type="button" role="tab">Clip (3s before / 3s after)</button></li>';
        const plateStatus = item.plate_status || "not_attempted";
        const plateLine = plateStatus === "recognized" && item.plate_text
            ? "Plate (human-verified): " + item.plate_text
            : (plateStatus === "unreadable" ? "Plate: unreadable" : "Plate: no human-verified plate");
        body.innerHTML =
            '<ul class="nav nav-tabs mb-3" role="tablist">' +
            '<li class="nav-item" role="presentation"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#rqScene" type="button" role="tab">Scene</button></li>' +
            '<li class="nav-item" role="presentation"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#rqVehicle" type="button" role="tab">Vehicle</button></li>' +
            clipTab +
            "</ul>" +
            '<div class="tab-content">' +
            '<div class="tab-pane fade show active" id="rqScene" role="tabpanel">' + sceneImg + "</div>" +
            '<div class="tab-pane fade" id="rqVehicle" role="tabpanel">' + vehicleImg + "</div>" +
            '<div class="tab-pane fade" id="rqClip" role="tabpanel">' + clipPane + "</div>" +
            "</div>" +
            "<p class=\"mt-3 text-muted\">" + escapeHtml(item.display_id) + " · " + escapeHtml(item.violation_type) + " · Track #" + escapeHtml(item.track_id) + "</p>" +
            "<p class=\"small text-muted\">" + escapeHtml(item.reason_log) + "</p>" +
            "<p class=\"small text-muted\">" + escapeHtml(plateLine) + "</p>" +
            "<p class=\"small\">Legal status: <strong>" + escapeHtml(item.legal_status || "—") + "</strong>" +
            (item.flag_only ? " · <span class=\"badge bg-warning text-dark\">flag_only review material</span>" : "") + "</p>" +
            "<p class=\"small text-muted\">Proposed official: " + escapeHtml(item.proposed_official_category || "—") +
            (item.verified_official_category ? " · Verified: " + escapeHtml(item.verified_official_category) : " · Verified: none") + "</p>" +
            "<p class=\"small text-muted\">Timestamp OCR: unavailable — confirm event time after case materialization.</p>";
        renderPlateOcr(item);
        evidenceModal.show();
    }

    // -- Experimental local plate OCR (machine candidates only) --------
    // Every string coming from OCR is escaped before it reaches innerHTML.
    // Confirmation controls are rendered only for an active System
    // Administrator; the server enforces the same rule independently.

    function plateState(state) {
        return plateMachineStateCopy(state);
    }

    function isAdminViewer() {
        const body = document.body;
        return !!body && body.dataset && body.dataset.tavidmRole === "admin";
    }

    function fmtScore(value) {
        return (value === null || value === undefined) ? "—" : Number(value).toFixed(2);
    }

    function fmtConfidence(value) {
        if (value === null || value === undefined) return "—";
        const n = Number(value) * 100;
        return Number.isFinite(n) ? n.toFixed(1) + "%" : "—";
    }

    function renderCandidate(item, candidate, isPrimary, canConfirm) {
        const cropUrl = (candidate.plate_crop_available && candidate.plate_crop_name && item.plate_machine.attempt_id)
            ? "/api/review-queue/" + encodeURIComponent(item.id) + "/plate-crop/" +
              encodeURIComponent(item.plate_machine.attempt_id) + "/" + encodeURIComponent(candidate.plate_crop_name)
            : null;
        const rawText = candidate.ocr_raw === null || candidate.ocr_raw === undefined || candidate.ocr_raw === ""
            ? '<span class="text-muted">no text returned</span>'
            : '<code class="fs-6">' + escapeHtml(candidate.ocr_raw) + "</code>";
        const conflicting = (item.plate_machine.candidates || []).filter(function (c) {
            return (c.ocr_display_normalized || "") !== (candidate.ocr_display_normalized || "");
        }).length;
        const confirmControls = canConfirm
            ? '<div class="mt-2 d-flex flex-wrap gap-2">' +
              '<button type="button" class="btn btn-sm btn-outline-primary plate-confirm" data-candidate-id="' +
              escapeHtml(candidate.candidate_id || "") + '" data-raw="' + escapeHtml(candidate.ocr_raw || "") +
              '">Confirm this plate (admin)</button>' +
              '<button type="button" class="btn btn-sm btn-outline-secondary plate-manual" data-candidate-id="' +
              escapeHtml(candidate.candidate_id || "") + '">Enter different text (admin)</button>' +
              "</div>"
            : '<p class="small text-muted mb-0"><i class="bi bi-lock me-1"></i>Plate confirmation is restricted to an active System Administrator.</p>';

        return '<div class="border rounded p-2' + (isPrimary ? " border-warning" : "") + '">' +
            '<div class="d-flex flex-wrap justify-content-between gap-2">' +
              "<div>" + rawText +
                (isPrimary ? ' <span class="badge bg-warning text-dark">primary candidate</span>' : "") +
                (conflicting ? ' <span class="badge bg-secondary">' + conflicting + " conflicting read(s)</span>" : "") +
              "</div>" +
              '<span class="small text-muted">det ' + fmtConfidence(candidate.detection_confidence) +
              " · score " + fmtScore(candidate.ocr_scalar_score) + "</span>" +
            "</div>" +
            '<p class="small text-muted mb-1">Frame ' + escapeHtml(String(candidate.frame_number)) +
              " · t=" + fmtScore(candidate.timestamp_sec) + "s · track #" + escapeHtml(String(candidate.track_id)) +
              " · identity epoch " + escapeHtml(String(candidate.track_identity_epoch)) +
              " · vehicle box " + escapeHtml(JSON.stringify(candidate.vehicle_box_in_frame || [])) +
              " · plate box " + escapeHtml(JSON.stringify(candidate.plate_box_in_frame || [])) + "</p>" +
            '<p class="small text-muted mb-1"><i class="bi bi-info-circle me-1"></i>Scores are raw uncalibrated model values, not probabilities and not an accuracy estimate.</p>' +
            (cropUrl
                ? '<img src="' + escapeHtml(cropUrl) + '" alt="Machine plate candidate crop" class="img-fluid rounded border" style="max-height:120px;">'
                : '<p class="small text-muted mb-1"><i class="bi bi-image me-1"></i>Candidate crop is not available on disk.</p>') +
            confirmControls +
            "</div>";
    }

    function renderPlateOcr(item) {
        const panel = document.getElementById("plateOcrPanel");
        const body = document.getElementById("plateOcrBody");
        const badge = document.getElementById("plateOcrStateBadge");
        if (!panel || !body || !badge) return;

        const machine = item.plate_machine || {};
        const state = machine.state || "none";
        const copy = plateState(state);
        panel.hidden = false;
        badge.className = "badge " + copy.css;
        badge.textContent = copy.label;

        let html = '<p class="small mb-2">' + escapeHtml(copy.text) + "</p>";

        const humanStatus = item.plate_status || "not_attempted";
        if (humanStatus === "verified_readable" || (humanStatus === "recognized" && item.plate_text)) {
            html += '<p class="small mb-2"><span class="badge bg-success-subtle text-success">Human-verified plate (separate state)</span> ' +
                '<strong>' + escapeHtml(item.plate_text || "") + "</strong></p>";
        }

        if (machine.association_uncertain) {
            html += '<p class="small text-danger mb-2"><i class="bi bi-exclamation-triangle me-1"></i>Association uncertain; candidate is not eligible for confirmation.</p>';
        }
        if (machine.quality_rejections && machine.quality_rejections.length) {
            html += '<p class="small text-muted mb-2">Quality rejections: ' +
                escapeHtml(machine.quality_rejections.join(", ")) + "</p>";
        }

        const candidates = machine.candidates || [];
        if (candidates.length) {
            const canConfirm = plateConfirmControlsAllowed(machine, isAdminViewer());
            html += '<p class="small text-muted">Machine candidates (unverified):</p><div class="d-flex flex-column gap-2">';
            candidates.forEach(function (candidate) {
                html += renderCandidate(item, candidate, candidate.candidate_id === machine.primary_candidate_id, canConfirm);
            });
            html += "</div>";
        } else if (machine.outcome_is_ambiguous) {
            html += '<p class="small text-muted mb-0">No candidate text is available for this observation. This is an explicit machine outcome, not a statement that no plate exists.</p>';
        }

        if (machine.artifact) {
            html += '<p class="small text-muted mb-0 mt-2">Detector ' + escapeHtml(String(machine.artifact.detector_sha256 || "").slice(0, 12)) +
                " · OCR " + escapeHtml(String(machine.artifact.ocr_sha256 || "").slice(0, 12)) +
                " · provider " + escapeHtml(String(machine.artifact.provider_active || "")) + "</p>";
        }

        body.innerHTML = html;
        body.querySelectorAll(".plate-confirm, .plate-manual").forEach(function (button) {
            button.addEventListener("click", function () {
                const candidateId = button.getAttribute("data-candidate-id") || "";
                const raw = button.getAttribute("data-raw") || "";
                const suggested = button.classList.contains("plate-manual") ? "" : raw;
                const accepted = window.prompt(
                    "Confirm the plate text exactly as it is visibly readable (administrator action).\n" +
                    "Leave blank to mark the plate unclear instead. Partial text is rejected.",
                    suggested
                );
                if (accepted === null) return;
                submitPlateConfirmation(item, candidateId, accepted.trim());
            });
        });
    }

    // A pending review has no case yet, so plate confirmation is only offered on
    // the case page. This helper keeps the flow honest instead of silently
    // dropping the admin's action.
    function submitPlateConfirmation(item, candidateId, acceptedText) {
        const body = document.getElementById("plateOcrBody");
        const notice = document.createElement("p");
        notice.className = "small text-warning mb-0";
        notice.textContent = "Plate confirmation is recorded on the case, after the review is confirmed into a case. Open the case detail to confirm this plate.";
        if (body && !body.querySelector(".plate-defer-note")) {
            notice.classList.add("plate-defer-note");
            body.appendChild(notice);
        }
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
        const plan = planReviewQueueAction(action);
        if (!plan) return;
        setRowBusy(row, true, trigger?.dataset.loadingLabel);
        fetch("/api/review-queue/" + item.id + "/" + plan.urlAction, { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (reviewActionClosesRow(payload)) {
                    row.remove();
                    updateCount();
                    if (plan.decision === "confirm_proposed") {
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
        const decideButton = e.target.closest(".btn-decide");
        if (evidenceButton) showEvidence(item);
        if (approveButton) {
            if (!window.confirm(approveButton.dataset.confirmMessage)) return;
            postAction(item, "confirm", row, approveButton);
        }
        if (dismissButton) postAction(item, "dismiss", row, dismissButton);
        if (decideButton) openDecisionModal(item, row);
    });

    let decisionTarget = null;

    function selectedDecision() {
        const checked = document.querySelector("input[name='reviewDecision']:checked");
        return checked ? checked.value : "confirm_proposed";
    }

    function syncRuleSelect() {
        const rule = document.getElementById("decisionRule");
        if (!rule) return;
        const correcting = selectedDecision() === "correct_canonical";
        rule.disabled = !correcting;
        if (decisionTarget && !correcting) {
            rule.value = decisionTarget.violation_type;
        }
    }

    function openDecisionModal(item, row) {
        decisionTarget = { item: item, row: row };
        const original = document.getElementById("decisionOriginal");
        if (original) {
            original.textContent = "Original system suggestion: " + (item.violation_type || "—")
                + " · " + (item.display_id || "");
        }
        const confirmRadio = document.getElementById("decisionConfirm");
        if (confirmRadio) confirmRadio.checked = true;
        const reason = document.getElementById("decisionReason");
        if (reason) reason.value = "";
        syncRuleSelect();
        decisionModal?.show();
    }

    document.querySelectorAll("input[name='reviewDecision']").forEach(function (input) {
        input.addEventListener("change", syncRuleSelect);
    });

    document.getElementById("decisionForm")?.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!decisionTarget) return;
        const item = decisionTarget.item;
        const row = decisionTarget.row;
        const decision = selectedDecision();
        const reason = document.getElementById("decisionReason")?.value || "";
        const rule = document.getElementById("decisionRule")?.value || "";
        const submit = document.getElementById("decisionSubmit");
        if (submit) submit.disabled = true;
        setRowBusy(row, true, "Saving decision…");
        fetch("/api/review-queue/" + item.id + "/decision", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                decision: decision,
                reason: reason,
                selected_canonical_rule: decision === "correct_canonical" ? rule : null,
                idempotency_key: "ui-" + item.id + "-" + decision,
            }),
        })
            .then(function (response) { return response.json(); })
            .then(function (payload) {
                if (!payload.success) {
                    setRowBusy(row, false);
                    if (submit) submit.disabled = false;
                    showToast("Error", payload.error || "Decision failed.", "danger");
                    return;
                }
                const label = payload.disposition_label || reviewDecisionLabel(payload.decision);
                if (!reviewActionClosesRow(payload)) {
                    setRowBusy(row, false);
                    if (submit) submit.disabled = false;
                    const message = payload.decision === "insufficient_evidence" && label === "No violation"
                        ? "Insufficient evidence was mislabeled."
                        : (payload.error || "Decision failed.");
                    showToast("Error", message, "danger");
                    return;
                }
                row.remove();
                updateCount();
                decisionModal?.hide();
                if (submit) submit.disabled = false;
                showToast(label, (item.violation_type || "Suggestion") + " remains the original suggestion.", "success");
            })
            .catch(function () {
                setRowBusy(row, false);
                if (submit) submit.disabled = false;
                showToast("Error", "Request failed. Please try again.", "danger");
            });
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
}
