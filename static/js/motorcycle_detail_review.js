/**
 * TAVIDM - Motorcycle Detail Review (separate queue; never a violation case)
 *
 * Shows scene / padded crop / mapped-box overlay per selected frame, model
 * confidence, association states, and the reasons an observation is uncertain.
 * Records reviewed / uncertain / dismissed outcomes only — there is no
 * confirm-case action in this workflow.
 */

function escapeHtml(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function describeHelmet(association) {
    if (!association) return "not scanned yet";
    switch (association.state) {
        case "acceptable": return "helmet detected (acceptable shape)";
        case "nut_shell": return "nut-shell / substandard helmet shape";
        case "unknown": return "helmet unknown — absence is not proven";
        default: return String(association.state);
    }
}

function describeMirror(association) {
    if (!association) return "not scanned yet";
    switch (association.state) {
        case "both_visible": return "both mounting areas observed";
        case "one_left": return "one mirror observed (left)";
        case "one_right": return "one mirror observed (right)";
        case "none_visible": return "no mirror observed — unknown, not proof of absence";
        case "ambiguous": return "ambiguous: overlapping motorcycles unresolved";
        default: return String(association.state);
    }
}

function describeRider(association) {
    if (!association) return "not scanned yet";
    switch (association.state) {
        case "associated": return "rider associated";
        case "ambiguous": return "ambiguous: multiple riders overlap";
        default: return "no rider associated";
    }
}

function uncertaintyList(item) {
    const direct = Array.isArray(item.uncertainty) ? item.uncertainty.slice() : [];
    const nested = Array.isArray((item.observations || {}).uncertainty)
        ? (item.observations.uncertainty || [])
        : [];
    return direct.concat(nested).filter(function (value, index, all) {
        return value && all.indexOf(value) === index;
    });
}

function buildEvidenceMarkup(item) {
    const frames = Array.isArray(item.frames) ? item.frames : [];
    if (!frames.length) {
        return '<p class="text-muted py-4 mb-0">No stored detail frames for this candidate.</p>';
    }
    const tabs = [];
    const panes = [];
    frames.forEach(function (frame, i) {
        const target = "#detailFrame" + i;
        const label = "Frame " + (frame.frame_number != null ? frame.frame_number : i + 1);
        tabs.push(
            '<li class="nav-item" role="presentation"><button class="nav-link' +
            (i === 0 ? " active" : "") + '" data-bs-toggle="tab" data-bs-target="' + target +
            '" type="button" role="tab">' + label + "</button></li>"
        );
        const scene = frame.scene_url
            ? '<img src="' + escapeHtml(frame.scene_url) + '" alt="Scene frame ' + i + '" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-4">No scene image.</div>';
        const crop = frame.crop_url
            ? '<img src="' + escapeHtml(frame.crop_url) + '" alt="Padded crop ' + i + '" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-4">No padded crop.</div>';
        const overlay = frame.overlay_url
            ? '<img src="' + escapeHtml(frame.overlay_url) + '" alt="Mapped boxes ' + i + '" class="evidence-preview img-fluid rounded">'
            : '<div class="text-muted py-4">Not scanned yet — mapped boxes appear after the crop pass.</div>';
        panes.push(
            '<div class="tab-pane fade' + (i === 0 ? " show active" : "") + '" id="detailFrame' + i + '" role="tabpanel">' +
            '<ul class="nav nav-pills mb-3" role="tablist">' +
            '<li class="nav-item"><span class="nav-link active small">Scene</span></li>' +
            "</ul>" + scene +
            '<div class="row g-3 mt-1"><div class="col-md-6"><p class="small text-muted mb-1">Padded motorcycle/rider crop</p>' + crop +
            "</div>" +
            '<div class="col-md-6"><p class="small text-muted mb-1">Mapped detections (source space)</p>' + overlay + "</div></div>" +
            "</div>"
        );
    });

    const association = item.association || {};
    const helmet = association.helmet || {};
    const mirror = association.mirror || {};
    const rider = association.rider || {};
    const observations = (item.observations || {});
    const scan = observations.scan || {};
    const reasons = uncertaintyList(item);

    const facts = [
        ["Rider", describeRider(rider)],
        ["Helmet", describeHelmet(helmet)],
        ["Side mirror", describeMirror(mirror)],
        ["Model", scan.model || item.scan_model || "—"],
        ["Scan confidence", scan.conf != null ? Math.round(scan.conf * 100) + "%" : "—"],
        ["Class map", (item.scan_class_map || scan.class_names || []).length
            ? (item.scan_class_map || scan.class_names || []).join(", ")
            : "—"]
    ].map(function (pair) {
        return "<dt>" + escapeHtml(pair[0]) + "</dt><dd>" + escapeHtml(pair[1]) + "</dd>";
    }).join("");

    const reasonMarkup = reasons.length
        ? "<h6 class='mt-3'>Why this may be uncertain</h6><ul class='mb-0'>" +
          reasons.map(function (r) { return "<li class='small'>" + escapeHtml(r) + "</li>"; }).join("") +
          "</ul>"
        : "<p class='small text-muted mt-3 mb-0'>No recorded uncertainty reasons.</p>";

    return '<ul class="nav nav-tabs mb-3" role="tablist">' + tabs.join("") + "</ul>" +
        '<div class="tab-content">' + panes.join("") + "</div>" +
        '<dl class="row small mt-3 mb-0">' + facts + "</dl>" + reasonMarkup;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        describeHelmet: describeHelmet,
        describeMirror: describeMirror,
        describeRider: describeRider,
        uncertaintyList: uncertaintyList
    };
}

(function () {
    "use strict";

    const tbody = document.getElementById("detailBody");
    const modalEl = document.getElementById("detailEvidenceModal");
    const modalBody = document.getElementById("detailModalBody");
    const modalTitle = document.getElementById("detailModalTitle");
    const pendingCount = document.getElementById("detailPendingCount");
    const queueStatus = document.getElementById("detailQueueStatus");
    const evidenceModal = modalEl && bootstrap && bootstrap.Modal
        ? new bootstrap.Modal(modalEl)
        : null;

    function showToast(title, message, tone) {
        if (window.TAVIDMMainUI && typeof window.TAVIDMMainUI.showToast === "function") {
            window.TAVIDMMainUI.showToast(title, message, tone);
        }
    }

    function showEvidence(item) {
        if (!modalBody) return;
        modalBody.innerHTML = buildEvidenceMarkup(item);
        if (modalTitle) modalTitle.textContent = "Detail evidence " + item.display_id;
        if (evidenceModal) evidenceModal.show();
    }

    function updateCounts() {
        if (!pendingCount) return;
        const total = Math.max(0, Number(pendingCount.dataset.total || 0) - 1);
        pendingCount.dataset.total = String(total);
        pendingCount.textContent = total + " Pending";
        if (queueStatus) {
            queueStatus.textContent = "Showing this page after your decision; " + total + " pending total.";
        }
    }

    function setRowBusy(row, busy, loadingLabel) {
        if (!row) return;
        row.setAttribute("aria-busy", String(busy));
        row.querySelectorAll("button").forEach(function (button) {
            button.disabled = busy;
            if (busy && button.dataset.loadingLabel) {
                if (!button.dataset.previousAriaLabel) {
                    button.dataset.previousAriaLabel = button.getAttribute("aria-label") || "";
                }
                button.setAttribute("aria-label", button.dataset.loadingLabel);
            } else if (!busy && button.dataset.previousAriaLabel) {
                button.setAttribute("aria-label", button.dataset.previousAriaLabel);
                delete button.dataset.previousAriaLabel;
            }
        });
    }

    function postOutcome(item, outcome, row, trigger) {
        setRowBusy(row, true, trigger && trigger.dataset.loadingLabel);
        fetch("/api/motorcycle-detail-review/" + item.id + "/outcome", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ outcome: outcome, notes: item.reviewer_notes || null })
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                if (payload.success) {
                    const badge = row.querySelector("[data-outcome-badge]");
                    if (badge) badge.textContent = payload.outcome;
                    setRowBusy(row, false);
                    showToast("Saved", "Detail candidate marked " + payload.outcome + ".", "info");
                    if (payload.outcome === "dismissed" || payload.outcome === "reviewed") {
                        updateCounts();
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
        const row = e.target.closest("tr[data-item]");
        if (!row) return;
        let item;
        try { item = JSON.parse(row.dataset.item); } catch (err) { return; }

        if (e.target.closest(".btn-detail-evidence")) {
            showEvidence(item);
            return;
        }
        const outcomeButton = e.target.closest(".btn-detail-outcome");
        if (outcomeButton) {
            const outcome = outcomeButton.dataset.outcome;
            if (!outcome) return;
            if (outcome === "dismissed" && !window.confirm("Dismiss this detail flag for " + item.display_id + "?")) {
                return;
            }
            postOutcome(item, outcome, row, outcomeButton);
        }
    });
})();
