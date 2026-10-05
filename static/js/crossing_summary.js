/**
 * TAVIDM - Vehicle line crossings by class for a completed uploaded-video result.
 *
 * Reads `vehicles_crossed` and `crossing_counts` from process-status only when
 * that payload's `run_id` is the current completed result. A later queued,
 * running, failed, or cancelled attempt keeps those status fields from being
 * attributed to the earlier result: the saved counts then come from
 * GET /api/videos/<id>/history, matched by `current_result_run_id` and
 * `is_current_result`, using that run's `vehicles_crossed` and
 * `crossing_counts_json` only. Detection records, class_counts, ByteTrack IDs,
 * violation candidates, and review-queue rows are never used as counts.
 */

(function (global) {
    "use strict";

    const DEFAULT_VEHICLE_CLASSES = [
        "car", "van", "jeepney", "tricycle", "autorickshaw",
        "bus", "truck", "pickup_truck", "motorcycle", "bicycle",
    ];
    const ATTEMPT_STATUSES = ["queued", "running", "completed", "failed", "cancelled"];

    function attemptStatus(payload) {
        if (!payload) return null;
        const recorded = payload.latest_attempt_status;
        if (typeof recorded === "string" && ATTEMPT_STATUSES.indexOf(recorded) >= 0) return recorded;
        const state = payload.job_state || payload.state;
        const stage = payload.stage;
        if (state === "cancelled" || stage === "cancelled") return "cancelled";
        if (state === "error" || stage === "failed") return "failed";
        if (state === "done" || stage === "completed") return "completed";
        if (stage === "queued" || payload.queued) return "queued";
        if (state === "processing") return "running";
        return null;
    }

    function integerId(value) {
        if (typeof value === "number" && Number.isInteger(value) && value >= 0) return value;
        if (typeof value === "string" && /^[0-9]+$/.test(value)) return Number(value);
        return null;
    }

    /** True when the status payload is not itself the current completed result. */
    function needsHistoricalResult(payload) {
        if (!payload) return false;
        const currentId = integerId(payload.current_result_run_id);
        if (currentId == null) return false;
        const runId = integerId(payload.run_id);
        return !(runId === currentId && attemptCompleted(payload));
    }

    /**
     * Pick the current completed run from a video-history response.
     * state "result" carries describeResult output for that exact run.
     * state "none" means the response has no current completed result.
     * state "unavailable" means the id is present but no single completed,
     * current, available run matches it.
     * state "mismatch" means the history result id is not the expected id.
     * state "error" means the response itself cannot be used.
     */
    function resultFromHistory(history, expectedRunId, vehicleClasses) {
        if (!history || typeof history !== "object" || Array.isArray(history) || history.success === false) {
            return { state: "error" };
        }
        const currentId = integerId(history.current_result_run_id);
        if (currentId == null) return { state: "none" };
        if (expectedRunId != null && currentId !== integerId(expectedRunId)) {
            return { state: "mismatch" };
        }
        const runs = Array.isArray(history.processing_runs) ? history.processing_runs : [];
        const matches = runs.filter(function (run) {
            return run && integerId(run.id) === currentId && run.is_current_result === true;
        });
        if (matches.length !== 1) return { state: "unavailable", runId: currentId };
        const run = matches[0];
        if (run.status !== "completed" || run.results_removed_at) {
            return { state: "unavailable", runId: currentId };
        }
        const parsed = parseSavedCrossingCounts(run.crossing_counts_json);
        const described = describeResult(currentId, {
            vehicles_crossed: run.vehicles_crossed,
            crossing_counts: parsed.unreadable ? [] : parsed.counts,
        }, vehicleClasses);
        if (parsed.unreadable) described.unreadable = true;
        return { state: "result", result: described };
    }

    function parseSavedCrossingCounts(raw) {
        if (raw == null || raw === "") return { counts: null, unreadable: false };
        let parsed = raw;
        if (typeof raw === "string") {
            try {
                parsed = JSON.parse(raw);
            } catch (err) {
                return { counts: null, unreadable: true };
            }
        }
        if (parsed == null) return { counts: null, unreadable: false };
        if (typeof parsed !== "object" || Array.isArray(parsed)) {
            return { counts: null, unreadable: true };
        }
        return { counts: parsed, unreadable: false };
    }

    /** True only when this attempt itself finished; an earlier result never counts. */
    function attemptCompleted(payload) {
        if (!payload) return false;
        const recorded = payload.latest_attempt_status;
        if (typeof recorded === "string" && ATTEMPT_STATUSES.indexOf(recorded) >= 0) {
            return recorded === "completed";
        }
        return attemptStatus(payload) === "completed";
    }

    function isCount(value) {
        return typeof value === "number" && Number.isInteger(value) && value >= 0;
    }

    function readCounts(raw, vehicleClasses) {
        const classes = vehicleClasses && vehicleClasses.length ? vehicleClasses : DEFAULT_VEHICLE_CLASSES;
        const out = { rows: [], sum: 0, ignored: 0, unreadable: false };
        if (raw == null) return out;
        if (typeof raw !== "object" || Array.isArray(raw)) {
            out.unreadable = true;
            return out;
        }
        Object.keys(raw).forEach(function (key) {
            if (classes.indexOf(key) < 0 || !isCount(raw[key])) out.ignored += 1;
        });
        classes.forEach(function (label) {
            if (!Object.prototype.hasOwnProperty.call(raw, label) || !isCount(raw[label])) return;
            out.rows.push({ label: label, count: raw[label] });
            out.sum += raw[label];
        });
        return out;
    }

    function describeResult(runId, payload, vehicleClasses) {
        const rawTotal = payload ? payload.vehicles_crossed : null;
        const counts = readCounts(payload ? payload.crossing_counts : null, vehicleClasses);
        let total = null;
        let totalUnreadable = false;
        if (rawTotal != null) {
            if (isCount(rawTotal)) total = rawTotal;
            else totalUnreadable = true;
        }
        return {
            runId: runId,
            total: total,
            totalUnreadable: totalUnreadable,
            rows: counts.rows,
            sum: counts.sum,
            ignored: counts.ignored,
            unreadable: counts.unreadable,
        };
    }

    /**
     * kind "current": the status is the current completed result itself.
     * kind "earlier": a later attempt exists. Counts come from a history
     *   response for that exact run, or from a same-session cache of it.
     *   Process-status crossing fields are not used for this kind.
     * kind "none": no completed result.
     */
    function buildModel(payload, options) {
        const opts = options || {};
        const currentId = payload ? integerId(payload.current_result_run_id) : null;
        const runId = payload ? integerId(payload.run_id) : null;
        const status = attemptStatus(payload);
        const laterAttempt = runId != null && runId !== currentId ? { runId: runId, status: status } : null;
        if (currentId == null) {
            return { kind: "none", result: null, resultRunId: null, laterAttempt: laterAttempt, historyState: null };
        }
        if (runId === currentId && attemptCompleted(payload)) {
            return {
                kind: "current",
                result: describeResult(currentId, payload, opts.vehicleClasses),
                resultRunId: currentId,
                laterAttempt: null,
                historyState: null,
            };
        }
        const cached = opts.cached && integerId(opts.cached.runId) === currentId ? opts.cached : null;
        let result = null;
        let historyState = null;
        if (opts.historyFailed) {
            historyState = "error";
            result = cached;
        } else if (opts.history) {
            const picked = resultFromHistory(opts.history, currentId, opts.vehicleClasses);
            historyState = picked.state;
            if (picked.state === "result" && picked.result && integerId(picked.result.runId) === currentId) {
                result = picked.result;
            } else if (picked.state === "error") {
                result = cached;
            }
        } else {
            result = cached;
        }
        return {
            kind: "earlier",
            result: result,
            resultRunId: currentId,
            laterAttempt: laterAttempt,
            historyState: historyState,
        };
    }

    function createSelectionGuard() {
        let token = 0;
        let selected = null;
        return {
            next: function (videoId) {
                token += 1;
                selected = videoId == null ? null : Number(videoId);
                return token;
            },
            clear: function () {
                token += 1;
                selected = null;
            },
            current: function () { return token; },
            isCurrent: function (candidate, videoId) {
                return candidate === token && selected !== null && selected === Number(videoId);
            },
        };
    }

    function prettyLabel(label) {
        return String(label).replace(/_/g, " ");
    }

    function totalText(result) {
        if (result.totalUnreadable) return "Unavailable — the saved total could not be read.";
        if (result.total == null) {
            return "Unavailable — no counting-line total was recorded for this result. " +
                "A counting line must be drawn in the zone annotation before processing; " +
                "results saved before crossing counts were recorded also show this.";
        }
        if (result.total === 0) {
            return "0 line-crossing passages — a counting line was configured, but no vehicle passage was counted.";
        }
        return result.total + " line-crossing passage" + (result.total === 1 ? "" : "s");
    }

    function resultNotes(result) {
        const notes = [];
        if (result.unreadable) notes.push("The saved class breakdown could not be read.");
        if (result.ignored > 0) {
            notes.push(result.ignored + " saved entr" + (result.ignored === 1 ? "y was" : "ies were") +
                " ignored because it is not a vehicle class or not a whole non-negative number.");
        }
        if (result.total != null && result.total > 0 && !result.rows.length && !result.unreadable) {
            notes.push("No class breakdown was saved for this result; an empty breakdown is not proof of zero.");
        }
        if (result.total != null && result.rows.length && result.sum !== result.total) {
            notes.push("The class breakdown adds up to " + result.sum +
                ", which differs from the saved total; treat the breakdown as incomplete.");
        }
        return notes;
    }

    /**
     * Loads an earlier completed result for the selected video.
     * Status polls for the same video and current_result_run_id share one
     * in-flight history request and render with the latest of those snapshots.
     * A newer completed result, a different current result, or another video
     * retires that request so its success or failure cannot replace the panel.
     */
    function createCrossingLoader(options) {
        const opts = options || {};
        let epoch = 0;
        let inflight = null;

        function retireInflight() {
            epoch += 1;
            inflight = null;
        }

        function settle(flight, extra) {
            if (flight.settled) return;
            flight.settled = true;
            if (inflight === flight) inflight = null;
            if (flight.epoch !== epoch) return;
            if (!opts.guard.isCurrent(flight.token, flight.videoId)) return;
            opts.render(flight.videoId, flight.payload, extra || {});
        }

        function showStatus(videoId, token, payload) {
            if (!opts.guard.isCurrent(token, videoId) || !payload || !payload.success) return;
            const key = String(videoId);
            const currentId = integerId(payload.current_result_run_id);
            const cached = opts.cache ? opts.cache[key] : null;
            const cacheMatches = !!(cached && currentId != null && integerId(cached.runId) === currentId);
            if (!(needsHistoricalResult(payload) && !cacheMatches)) {
                retireInflight();
                opts.render(videoId, payload, {});
                return;
            }
            if (inflight && inflight.epoch === epoch && inflight.videoId === Number(videoId) && inflight.currentId === currentId) {
                inflight.payload = payload;
                inflight.token = token;
                return;
            }
            retireInflight();
            const flight = {
                videoId: Number(videoId),
                currentId: currentId,
                token: token,
                payload: payload,
                epoch: epoch,
                settled: false,
            };
            inflight = flight;
            let historyPromise;
            try {
                historyPromise = opts.fetchHistory(videoId);
            } catch (err) {
                historyPromise = Promise.reject(err);
            }
            Promise.resolve(historyPromise).then(
                function (history) {
                    if (!history || history.success === false) settle(flight, { historyFailed: true });
                    else settle(flight, { history: history });
                },
                function () { settle(flight, { historyFailed: true }); },
            );
        }

        return { showStatus: showStatus };
    }

    function laterAttemptText(model) {
        const later = model.laterAttempt;
        if (!later) return "";
        const status = later.status || "unknown";
        return "Latest attempt run #" + later.runId + ": " + status + " — not a completed result." +
            (model.result ? " Counts shown here belong to run #" + model.resultRunId + "." : "");
    }

    function el(doc, tag, className, text) {
        const node = doc.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = String(text);
        return node;
    }

    /** Render with textContent only; API labels never reach innerHTML. */
    function render(container, model, doc) {
        if (!container) return;
        const d = doc || global.document;
        container.textContent = "";
        if (!model || model.kind === "none") return;
        if (model.historyState === "none") {
            container.appendChild(el(d, "p", "small text-muted mb-1", "No current completed result is available."));
            if (model.laterAttempt) {
                container.appendChild(el(d, "p", "small text-warning-emphasis mb-1", laterAttemptText(model)));
            }
            return;
        }
        const heading = model.kind === "current"
            ? "Current completed result: run #" + model.resultRunId
            : "Current completed result: run #" + model.resultRunId + " (earlier attempt)";
        container.appendChild(el(d, "p", "small fw-semibold mb-1", heading));
        if (model.laterAttempt) {
            container.appendChild(el(d, "p", "small text-warning-emphasis mb-1", laterAttemptText(model)));
        }
        const result = model.result;
        if (!result) {
            let message = "Crossing counts for run #" + model.resultRunId +
                " are not shown because this status describes the later attempt.";
            if (model.historyState === "error") {
                message = "Crossing counts for run #" + model.resultRunId +
                    " could not be loaded. Counts from the later attempt are not shown.";
            } else if (model.historyState === "unavailable" || model.historyState === "mismatch") {
                message = "Crossing counts for run #" + model.resultRunId + " are unavailable.";
            }
            container.appendChild(el(d, "p", "small text-muted mb-0", message));
            return;
        }
        const total = el(d, "p", "small mb-1", null);
        total.appendChild(el(d, "strong", null, "Total: "));
        total.appendChild(d.createTextNode(totalText(result)));
        container.appendChild(total);
        if (result.rows.length) {
            const table = el(d, "table", "table table-sm small mb-1 crossing-summary-table", null);
            table.appendChild(el(d, "caption", "visually-hidden", "Vehicle line-crossing passages by class, run #" + result.runId));
            const head = el(d, "thead", null, null);
            const headRow = el(d, "tr", null, null);
            const classHead = el(d, "th", null, "Vehicle class");
            classHead.setAttribute("scope", "col");
            const countHead = el(d, "th", "text-end", "Line-crossing passages");
            countHead.setAttribute("scope", "col");
            headRow.appendChild(classHead);
            headRow.appendChild(countHead);
            head.appendChild(headRow);
            table.appendChild(head);
            const body = el(d, "tbody", null, null);
            result.rows.forEach(function (row) {
                const tr = el(d, "tr", null, null);
                const name = el(d, "th", null, prettyLabel(row.label));
                name.setAttribute("scope", "row");
                tr.appendChild(name);
                tr.appendChild(el(d, "td", "text-end", row.count));
                body.appendChild(tr);
            });
            table.appendChild(body);
            container.appendChild(table);
        }
        resultNotes(result).forEach(function (note) {
            container.appendChild(el(d, "p", "small text-muted mb-1", note));
        });
    }

    const api = {
        DEFAULT_VEHICLE_CLASSES: DEFAULT_VEHICLE_CLASSES,
        attemptStatus: attemptStatus,
        attemptCompleted: attemptCompleted,
        needsHistoricalResult: needsHistoricalResult,
        resultFromHistory: resultFromHistory,
        readCounts: readCounts,
        describeResult: describeResult,
        buildModel: buildModel,
        createSelectionGuard: createSelectionGuard,
        createCrossingLoader: createCrossingLoader,
        totalText: totalText,
        resultNotes: resultNotes,
        render: render,
    };
    global.TavidmCrossingSummary = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
