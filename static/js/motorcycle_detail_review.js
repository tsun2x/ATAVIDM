/**
 * TAVIDM - Motorcycle Detail Review
 *
 * Shows scene / padded crop / mapped-box overlay per selected frame, model
 * confidence, association states, and the reasons an observation is uncertain.
 * Human-selected outcomes are explicitly confirmed and sent to the audited
 * review-decision service; model outputs only provide review evidence.
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

function detailOutcomeRequestBody(item, outcome) {
    return {
        outcome: outcome,
        notes: item.reviewer_notes || null,
        expected_revision: item.review_revision || null,
        // Retained for older servers/clients that implement outcome-only CAS.
        expected_outcome: item.human_outcome || "pending",
    };
}

function isStaleDetailReviewResponse(status) {
    return Number(status) === 409;
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

    const rawVideo = item.source_video_url
        ? '<video controls preload="metadata" playsinline class="evidence-preview rounded w-100" data-detail-video data-start-sec="' + escapeHtml(String(item.timestamp_sec || 0)) + '" data-raw-src="' + escapeHtml(item.source_video_url) + '"' + (item.annotated_video_url ? ' data-annotated-src="' + escapeHtml(item.annotated_video_url) + '"' : '') + ' src="' + escapeHtml(item.source_video_url) + '"></video>'
        : '<p class="text-muted">Source video is unavailable.</p>';
    const videoMarkup = '<section class="border-bottom pb-3 mb-3"><h6>Evidence video playback</h6>' +
        '<div class="btn-group btn-group-sm mb-2" role="group" aria-label="Evidence video view">' +
        '<button type="button" class="btn btn-secondary" data-detail-video-mode="raw">Raw video</button>' +
        (item.annotated_video_url ? '<button type="button" class="btn btn-outline-secondary" data-detail-video-mode="annotated">Annotated video</button>' : '') +
        '</div>' + rawVideo + '<p class="small text-muted mb-0">Playback opens near the selected observation at ' + escapeHtml(String(item.timestamp_sec || 0)) + ' seconds.</p></section>';
    const frameMarkup = frames.length
        ? '<ul class="nav nav-tabs mb-3" role="tablist">' + tabs.join("") + "</ul><div class='tab-content'>" + panes.join("") + "</div>"
        : '<p class="text-muted py-3 mb-0">No stored detail-frame images for this candidate. Source video playback is still available when present.</p>';
    const choices = [
        ["no_helmet", "No Helmet", "Creates a No Helmet case"],
        ["nut_shell_helmet", "Nut-shell helmet", "Creates a Substandard / Nut-Shell Helmet case"],
        ["no_side_mirror", "No Side Mirror", "Creates a No Side Mirror case"],
        ["no_violation", "No Violation", "Closes this review without creating a case"]
    ];
    const choiceMarkup = choices.map(function (choice) {
        return '<label class="list-group-item d-flex align-items-start gap-2"><input class="form-check-input mt-1" type="radio" name="detailDecisionChoice" value="' + choice[0] + '"' + (item.scan_state !== "ready" ? " disabled" : "") + '><span><strong>' + escapeHtml(choice[1]) + '</strong><span class="d-block small text-muted">' + escapeHtml(choice[2]) + "</span></span></label>";
    }).join("");
    return '<div class="d-flex align-items-center gap-2 mb-3"><label class="small mb-0" for="detailEvidenceScale">Evidence size</label><input id="detailEvidenceScale" class="form-control form-control-sm" type="number" min="50" max="100" step="5" value="100" style="width:90px"><span class="small">%</span></div>' +
        videoMarkup + frameMarkup + '<dl class="row small mt-3 mb-0">' + facts + "</dl>" + reasonMarkup +
        '<section class="border-top mt-4 pt-3" aria-labelledby="detailDecisionHeading"><h6 id="detailDecisionHeading">Reviewer decision</h6><p class="small text-muted">Choose what the evidence shows. The model suggestion does not make this decision.</p><div class="list-group mb-3">' + choiceMarkup + '</div><button type="button" class="btn btn-primary" data-detail-submit-choice' + (item.scan_state !== "ready" ? " disabled" : "") + '>Confirm selection</button><span class="small text-muted ms-2">You will be asked to confirm before saving.</span></section>';
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
        uncertaintyList: uncertaintyList,
        detailOutcomeRequestBody: detailOutcomeRequestBody,
        isStaleDetailReviewResponse: isStaleDetailReviewResponse,
        buildEvidenceMarkup: buildEvidenceMarkup
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
    let activeDecisionItem = null;
    let activeDecisionRow = null;
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
        const resize = function (value) {
            const percent = Math.max(50, Math.min(100, Number(value) || 100));
            modalBody.querySelectorAll(".evidence-preview").forEach(function (media) {
                media.style.width = percent + "%";
                media.style.maxWidth = "none";
                media.style.height = "auto";
            });
        };
        modalBody.querySelector("#detailEvidenceScale")?.addEventListener("input", function () { resize(this.value); });
        resize(100);
        const player = modalBody.querySelector("[data-detail-video]");
        player?.addEventListener("loadedmetadata", function () {
            const target = Number(player.dataset.startSec || 0);
            if (Number.isFinite(target) && target > 0 && target < player.duration) player.currentTime = target;
        }, { once: true });
        modalBody.querySelectorAll("[data-detail-video-mode]").forEach(function (button) {
            button.addEventListener("click", function () {
                const source = button.dataset.detailVideoMode === "annotated" ? player.dataset.annotatedSrc : player.dataset.rawSrc;
                if (!source || player.src === new URL(source, window.location.href).href) return;
                const target = Number(player.dataset.startSec || 0);
                player.src = source;
                player.load();
                player.addEventListener("loadedmetadata", function () {
                    if (target > 0 && target < player.duration) player.currentTime = target;
                }, { once: true });
                modalBody.querySelectorAll("[data-detail-video-mode]").forEach(function (entry) {
                    const active = entry === button;
                    entry.classList.toggle("btn-secondary", active);
                    entry.classList.toggle("btn-outline-secondary", !active);
                });
            });
        });
        if (modalTitle) modalTitle.textContent = "Detail evidence " + item.display_id;
        if (evidenceModal) evidenceModal.show();
    }

    function submitSelectedDecision() {
        if (!activeDecisionItem || !activeDecisionRow) return;
        const selected = modalBody.querySelector("input[name='detailDecisionChoice']:checked");
        if (!selected) {
            showToast("Choose an outcome", "Select what the evidence shows before continuing.", "warning");
            return;
        }
        const labels = {
            no_helmet: "No Helmet case",
            nut_shell_helmet: "Substandard / Nut-Shell Helmet case",
            no_side_mirror: "No Side Mirror case",
            no_violation: "No Violation (no case)"
        };
        const label = labels[selected.value];
        if (!window.confirm("Are you sure? Save this human-reviewed decision as: " + label + "?")) return;
        const item = activeDecisionItem;
        const row = activeDecisionRow;
        setRowBusy(row, true, "Saving decision…");
        fetch("/api/motorcycle-detail-review/" + item.id + "/decision", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                choice: selected.value,
                human_confirmed: true,
                expected_revision: item.review_revision || null,
                expected_outcome: item.human_outcome || "pending"
            })
        }).then(function (response) {
            return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; });
        }).then(function (result) {
            setRowBusy(row, false);
            if (!result.ok || !result.payload.success) {
                showToast("Decision not saved", result.payload.error || "The review decision could not be saved.", "danger");
                return;
            }
            if (evidenceModal) evidenceModal.hide();
            if (item.human_outcome === "pending") updateCounts();
            if (result.payload.creates_case) {
                showToast("Violation case created", label + " is now available in Violations.", "success");
                window.location.href = "/violations";
                return;
            }
            showToast("No violation recorded", "The review is closed and no violation case was created.", "success");
            window.setTimeout(function () { window.location.reload(); }, 800);
        }).catch(function () {
            setRowBusy(row, false);
            showToast("Decision status unknown", "Reload Motorcycle Detail Review before retrying.", "danger");
        });
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
            body: JSON.stringify(detailOutcomeRequestBody(item, outcome))
        })
            .then(function (r) { return r.json().then(function (payload) {
                return { payload: payload, status: r.status };
            }); })
            .then(function (result) {
                const payload = result.payload;
                if (isStaleDetailReviewResponse(result.status)) {
                    setRowBusy(row, false);
                    showToast("Review Updated Elsewhere", "Reloading the latest reviewer state before another decision.", "warning");
                    window.location.reload();
                    return;
                }
                if (payload.success) {
                    const badge = row.querySelector("[data-outcome-badge]");
                    if (badge) badge.textContent = payload.outcome;
                    item.human_outcome = payload.outcome;
                    if (payload.reviewer_name) item.reviewer_name = payload.reviewer_name;
                    if (payload.reviewed_at) item.reviewed_at = payload.reviewed_at;
                    if (payload.reviewer_notes !== undefined) item.reviewer_notes = payload.reviewer_notes;
                    if (payload.review_revision) item.review_revision = payload.review_revision;
                    row.dataset.item = JSON.stringify(item);
                    const reviewMeta = row.querySelector("[data-review-meta]");
                    if (reviewMeta) {
                        const parts = [];
                        if (payload.reviewer_name) parts.push(payload.reviewer_name);
                        if (payload.reviewed_at) parts.push(payload.reviewed_at);
                        reviewMeta.textContent = parts.join(" · ") +
                            (payload.reviewer_notes ? " — " + payload.reviewer_notes : "");
                    }
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
            activeDecisionItem = item;
            activeDecisionRow = row;
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
            return;
        }
    });

    modalBody?.addEventListener("click", function (e) {
        if (e.target.closest("[data-detail-submit-choice]")) submitSelectedDecision();
    });
})();
