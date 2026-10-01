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
    const reasons = Array.isArray(association.reasons) ? association.reasons : [];
    switch (association.state) {
        case "acceptable": return "helmet detected (acceptable shape)";
        case "nut_shell": return "nut-shell / substandard helmet shape";
        case "uncovered_head":
            return "Uncovered head observed — review evidence requiring human verification; not a confirmed no-helmet violation or compliance decision";
        case "unknown": return "helmet unknown — absence is not proven";
        case "ambiguous":
            if (reasons.indexOf("contradictory_head_labels") >= 0 ||
                reasons.indexOf("contradictory_helmet_observations_across_frames") >= 0) {
                return "ambiguous: conflicting head observations; human verification required";
            }
            if (reasons.indexOf("uncovered_head_box_clipped_or_unclear") >= 0) {
                return "ambiguous: uncovered-head box is clipped or unclear";
            }
            if (reasons.indexOf("nearby_rider_context_incomplete_helmet_unattributed") >= 0) {
                return "ambiguous: nearby rider context is incomplete";
            }
            return "ambiguous: helmet may belong to another rider";
        default: return String(association.state);
    }
}

function formatClassMap(classes) {
    if (Array.isArray(classes)) {
        return classes.map(function (name, id) { return id + "=" + name; }).join(", ");
    }
    if (classes && typeof classes === "object") {
        return Object.keys(classes)
            .sort(function (a, b) { return Number(a) - Number(b); })
            .map(function (id) { return id + "=" + classes[id]; })
            .join(", ");
    }
    return "";
}

function describeModel(scan, item) {
    const identity = (scan && scan.model) || (item && item.scan_model) || "";
    if (!identity) return "—";
    if (!scan || !scan.model_kind) {
        return identity + " (older scan recorded before the three-class detail contract)";
    }
    const parts = [scan.model_kind];
    if (scan.architecture) parts.push("architecture " + scan.architecture);
    if (scan.contract) parts.push("contract " + scan.contract);
    return identity + " (" + parts.join(", ") + ")";
}

function describeAttribution(association) {
    const context = association && association.context;
    if (!context) return "not recorded for this scan";
    if (!context.recorded) {
        return "main detector rider box only; nearby motorcycles and riders were not recorded for this older row";
    }
    const bikes = Array.isArray(context.nearby_motorcycles) ? context.nearby_motorcycles.length : 0;
    const riders = Array.isArray(context.nearby_riders) ? context.nearby_riders.length : 0;
    const lists = context.truncated_lists;
    const knownLists = lists && typeof lists === "object";
    const bikesCut = knownLists ? lists.motorcycles === true : Boolean(context.truncated);
    const ridersCut = knownLists ? lists.riders === true : Boolean(context.truncated);
    return "main detector: rider " + (context.rider_state || "unknown") +
        (context.rider_track_id != null ? " (track #" + context.rider_track_id + ")" : "") +
        "; nearby motorcycles " + bikes + (bikesCut ? " (list truncated)" : "") +
        "; other riders " + riders + (ridersCut ? " (list truncated)" : "");
}

function describeMirror(association) {
    if (!association) return "not scanned yet";
    switch (association.state) {
        case "both_visible": return "both mounting areas observed";
        case "one_left": return "one mirror observed (left)";
        case "one_right": return "one mirror observed (right)";
        case "none_visible": return "no mirror observed — unknown, not proof of absence";
        case "ambiguous": return "ambiguous: mirror may belong to another motorcycle";
        default: return String(association.state);
    }
}

function describeRider(association) {
    if (!association) return "not scanned yet";
    const fromMain = association.source === "main_detector" || association.source === "legacy_row";
    switch (association.state) {
        case "associated": return "rider associated" + (fromMain ? " (main detector)" : "");
        case "ambiguous": return "ambiguous: multiple riders overlap";
        default: return "no rider associated";
    }
}

function frameDetections(item, index) {
    const frames = ((item && item.observations) || {}).frames;
    if (!Array.isArray(frames)) return [];
    const match = frames.filter(function (entry) { return entry && Number(entry.index) === Number(index); })[0];
    return match && Array.isArray(match.detections) ? match.detections : [];
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
        const detections = frameDetections(item, frame.index);
        const detectionList = detections.length
            ? '<ul class="small mb-0 mt-2">' + detections.map(function (d) {
                const conf = d.confidence != null ? " " + Math.round(Number(d.confidence) * 100) + "%" : "";
                const bbox = Array.isArray(d.source_bbox)
                    ? " at [" + d.source_bbox.map(function (v) { return Math.round(Number(v)); }).join(", ") + "]"
                    : "";
                let text = String(d.class_label) + conf + bbox;
                if (d.class_label === "no_helmet") {
                    text += " — Uncovered head observed (human verification only; not a confirmed violation)";
                }
                if (d.evidence_limited) text += " (clipped or unclear)";
                return "<li>" + escapeHtml(text) + "</li>";
            }).join("") + "</ul>"
            : (frame.overlay_url ? '<p class="small text-muted mb-0 mt-2">No detail detections in this crop (not proof of absence).</p>' : "");
        panes.push(
            '<div class="tab-pane fade' + (i === 0 ? " show active" : "") + '" id="detailFrame' + i + '" role="tabpanel">' +
            '<ul class="nav nav-pills mb-3" role="tablist">' +
            '<li class="nav-item"><span class="nav-link active small">Scene</span></li>' +
            "</ul>" + scene +
            '<div class="row g-3 mt-1"><div class="col-md-6"><p class="small text-muted mb-1">Padded motorcycle/rider crop</p>' + crop +
            "</div>" +
            '<div class="col-md-6"><p class="small text-muted mb-1">Mapped detections (source space)</p>' + overlay + detectionList + "</div></div>" +
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

    const classMap = formatClassMap(scan.class_map || item.scan_class_map || scan.class_names || []);
    const facts = [
        ["Rider", describeRider(rider)],
        ["Helmet", describeHelmet(helmet)],
        ["Side mirror", describeMirror(mirror)],
        ["Attribution", describeAttribution(association)],
        ["Model", describeModel(scan, item)],
        ["Scan confidence", scan.conf != null ? Math.round(scan.conf * 100) + "%" : "—"],
        ["Class map", classMap || "—"]
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
        describeModel: describeModel,
        describeAttribution: describeAttribution,
        formatClassMap: formatClassMap,
        frameDetections: frameDetections,
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
