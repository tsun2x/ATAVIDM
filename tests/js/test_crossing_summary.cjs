"use strict";

const assert = require("assert");
const summary = require("../../static/js/crossing_summary.js");

const VEHICLES = summary.DEFAULT_VEHICLE_CLASSES;

// Minimal DOM: any innerHTML access fails the test.
function fakeDocument() {
    function node(tag) {
        const n = { tag: tag, children: [], attrs: {}, className: "", _text: "" };
        Object.defineProperty(n, "textContent", {
            get: function () { return n._text + n.children.map(function (c) { return c.textContent; }).join(" "); },
            set: function (v) { n._text = String(v); n.children = []; },
        });
        Object.defineProperty(n, "innerHTML", {
            get: function () { throw new Error("innerHTML must not be read"); },
            set: function () { throw new Error("innerHTML must not be used"); },
        });
        n.appendChild = function (child) { n.children.push(child); return child; };
        n.setAttribute = function (k, v) { n.attrs[k] = String(v); };
        return n;
    }
    return {
        createElement: node,
        createTextNode: function (text) { const t = node("#text"); t.textContent = text; return t; },
    };
}

function renderText(model) {
    const doc = fakeDocument();
    const container = doc.createElement("div");
    summary.render(container, model, doc);
    return container.textContent;
}

function completed(extra) {
    return Object.assign({
        success: true, run_id: 7, current_result_run_id: 7, latest_attempt_status: "completed",
        state: "done", stage: "completed",
        detection_records: 900, unique_tracks: 40, violation_candidates: 3,
        class_counts: { car: 600, person: 200, rider: 100 },
    }, extra);
}

// Current completed result: breakdown comes from crossing_counts, never class_counts.
{
    const model = summary.buildModel(completed({ vehicles_crossed: 3, crossing_counts: { car: 2, motorcycle: 1 } }),
        { vehicleClasses: VEHICLES });
    assert.strictEqual(model.kind, "current");
    assert.strictEqual(model.resultRunId, 7);
    assert.deepStrictEqual(model.result.rows, [{ label: "car", count: 2 }, { label: "motorcycle", count: 1 }]);
    assert.strictEqual(model.result.total, 3);
    const text = renderText(model);
    assert.ok(text.includes("Current completed result: run #7"));
    assert.ok(text.includes("3 line-crossing passages"));
    assert.ok(text.includes("Line-crossing passages"));
    assert.ok(!text.includes("600") && !text.includes("900") && !text.includes("40"));
    assert.ok(!/unique vehicles/i.test(text));
}

// Two-line passages of one track are displayed as two passages without a mismatch note.
{
    const model = summary.buildModel(completed({ vehicles_crossed: 2, crossing_counts: { bus: 2 } }), {});
    const text = renderText(model);
    assert.ok(text.includes("2 line-crossing passages"));
    assert.ok(!text.includes("differs from the saved total"));
}

// null (no counting line / legacy row) is unavailable, never zero.
{
    const model = summary.buildModel(completed({ vehicles_crossed: null, crossing_counts: {} }), {});
    assert.strictEqual(model.result.total, null);
    const text = renderText(model);
    assert.ok(text.includes("Unavailable"));
    assert.ok(text.includes("counting line"));
    assert.ok(!text.includes("0 line-crossing passages"));
}

// A valid zero is shown as zero with the counting-line explanation.
{
    const text = renderText(summary.buildModel(completed({ vehicles_crossed: 0, crossing_counts: {} }), {}));
    assert.ok(text.includes("0 line-crossing passages"));
    assert.ok(text.includes("counting line was configured"));
    assert.ok(!text.includes("Unavailable"));
}

// An empty map with a positive total is not proof of zero.
{
    const text = renderText(summary.buildModel(completed({ vehicles_crossed: 4, crossing_counts: {} }), {}));
    assert.ok(text.includes("4 line-crossing passages"));
    assert.ok(text.includes("not proof of zero"));
}

// Non-vehicle labels, markup-like keys, and malformed values are not vehicle counts.
{
    const model = summary.buildModel(completed({
        vehicles_crossed: 3,
        crossing_counts: {
            car: 3, person: 9, rider: 4, helmet_acceptable: 2, no_helmet: 1,
            "<img src=x onerror=alert(1)>": 5, van: "2", bus: -1, truck: 1.5,
        },
    }), {});
    assert.deepStrictEqual(model.result.rows, [{ label: "car", count: 3 }]);
    assert.strictEqual(model.result.ignored, 8);
    const text = renderText(model);
    assert.ok(!text.includes("<img") && !text.includes("person") && !text.includes("helmet"));
    assert.ok(text.includes("8 saved entries were ignored"));
}

// Older or malformed saved breakdowns and totals.
{
    const arrayCounts = summary.buildModel(completed({ vehicles_crossed: 2, crossing_counts: [1, 2] }), {});
    assert.strictEqual(arrayCounts.result.unreadable, true);
    assert.ok(renderText(arrayCounts).includes("could not be read"));
    const stringTotal = summary.buildModel(completed({ vehicles_crossed: "3", crossing_counts: { car: 3 } }), {});
    assert.strictEqual(stringTotal.result.total, null);
    assert.ok(renderText(stringTotal).includes("saved total could not be read"));
    const mismatch = summary.buildModel(completed({ vehicles_crossed: 5, crossing_counts: { car: 2 } }), {});
    assert.ok(renderText(mismatch).includes("differs from the saved total"));
    const missing = summary.buildModel(completed({ vehicles_crossed: undefined, crossing_counts: undefined }), {});
    assert.strictEqual(missing.result.total, null);
}

// A failed rerun after an earlier completed result: the later attempt's fields are
// not attributed to run #7, and the attempt is never called complete.
{
    const failed = {
        success: true, run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed",
        state: "error", stage: "failed", video_status: "processed",
        vehicles_crossed: 50, crossing_counts: { car: 50 },
    };
    assert.strictEqual(summary.attemptCompleted(failed), false);
    const model = summary.buildModel(failed, {});
    assert.strictEqual(model.kind, "earlier");
    assert.strictEqual(model.result, null);
    assert.deepStrictEqual(model.laterAttempt, { runId: 9, status: "failed" });
    const text = renderText(model);
    assert.ok(text.includes("run #7 (earlier attempt)"));
    assert.ok(text.includes("Latest attempt run #9: failed — not a completed result."));
    assert.ok(text.includes("not shown because this status describes the later attempt"));
    assert.ok(!text.includes("50"));

    const cachedRun7 = summary.describeResult(7, { vehicles_crossed: 2, crossing_counts: { jeepney: 2 } }, VEHICLES);
    const withCache = summary.buildModel(failed, { cached: cachedRun7 });
    assert.strictEqual(withCache.result, cachedRun7);
    const cachedText = renderText(withCache);
    assert.ok(cachedText.includes("2 line-crossing passages"));
    assert.ok(cachedText.includes("Counts shown here belong to run #7"));
    assert.ok(!cachedText.includes("50"));

    const staleCache = summary.describeResult(6, { vehicles_crossed: 8, crossing_counts: { car: 8 } }, VEHICLES);
    assert.strictEqual(summary.buildModel(failed, { cached: staleCache }).result, null);
}

// Queued, running, and cancelled later attempts are kept separate as well.
["queued", "running", "cancelled"].forEach(function (status) {
    const payload = { run_id: 12, current_result_run_id: 7, latest_attempt_status: status, video_status: "processed",
        job_state: "done", stage: "completed", vehicles_crossed: 1, crossing_counts: { car: 1 } };
    assert.strictEqual(summary.attemptCompleted(payload), false, status);
    const model = summary.buildModel(payload, {});
    assert.strictEqual(model.kind, "earlier", status);
    assert.strictEqual(model.laterAttempt.status, status);
});

// Completion means this attempt finished, not that the video has an older result.
assert.strictEqual(summary.attemptCompleted({ video_status: "processed", latest_attempt_status: "running" }), false);
assert.strictEqual(summary.attemptCompleted({ latest_attempt_status: "completed" }), true);
assert.strictEqual(summary.attemptCompleted({ latest_attempt_status: null, stage: "completed" }), true);
assert.strictEqual(summary.attemptCompleted({ job_state: "cancelled", stage: "completed" }), false);
assert.strictEqual(summary.attemptCompleted({ video_status: "processed" }), false);
assert.strictEqual(summary.attemptCompleted(null), false);

// No completed result: nothing is rendered as a result.
{
    const model = summary.buildModel({ run_id: 3, current_result_run_id: null, latest_attempt_status: "running" }, {});
    assert.strictEqual(model.kind, "none");
    assert.strictEqual(renderText(model), "");
}

function historyFor(status, counts, total, extraRun) {
    const run = Object.assign({
        id: 7, status: "completed", is_current_result: true, vehicles_crossed: total,
        crossing_counts_json: JSON.stringify(counts),
        class_counts_json: JSON.stringify({ car: 40, person: 12 }),
        detection_records: 57, unique_tracks: 9,
    }, extraRun || {});
    return {
        success: true,
        current_result_run_id: 7,
        processing_runs: [
            {
                id: 9, status: status, is_current_result: false, vehicles_crossed: 50,
                crossing_counts_json: JSON.stringify({ car: 50 }),
                class_counts_json: JSON.stringify({ car: 900 }),
                detection_records: 800, unique_tracks: 70,
            },
            run,
        ],
    };
}

// Fresh session, no cached summary: a failed later attempt must still show the
// earlier completed run's saved line crossings, not the later attempt's counts.
{
    const statusPayload = {
        success: true, run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed",
        state: "error", stage: "failed", vehicles_crossed: 50, crossing_counts: { car: 50 },
        class_counts: { car: 900 }, detection_records: 800, unique_tracks: 70,
    };
    assert.strictEqual(summary.needsHistoricalResult(statusPayload), true);
    const model = summary.buildModel(statusPayload, {
        history: historyFor("failed", { car: 2, motorcycle: 1, person: 9 }, 3),
    });
    assert.strictEqual(model.kind, "earlier");
    assert.strictEqual(model.resultRunId, 7);
    assert.strictEqual(model.result.total, 3);
    assert.deepStrictEqual(model.result.rows, [
        { label: "car", count: 2 }, { label: "motorcycle", count: 1 },
    ]);
    assert.deepStrictEqual(model.laterAttempt, { runId: 9, status: "failed" });
    const text = renderText(model);
    assert.ok(text.includes("Current completed result: run #7 (earlier attempt)"));
    assert.ok(text.includes("3 line-crossing passages"));
    assert.ok(text.includes("car") && text.includes("motorcycle"));
    assert.ok(text.includes("Latest attempt run #9: failed — not a completed result."));
    assert.ok(text.includes("Counts shown here belong to run #7"));
    assert.ok(!text.includes("50") && !text.includes("900") && !text.includes("800") && !text.includes("70"));
    assert.ok(!text.includes("person"));
}

// The same earlier result is shown for queued, running, and cancelled attempts.
["queued", "running", "cancelled"].forEach(function (status) {
    const payload = {
        run_id: 9, current_result_run_id: 7, latest_attempt_status: status,
        vehicles_crossed: 50, crossing_counts: { bus: 50 },
    };
    const model = summary.buildModel(payload, {
        history: historyFor(status, { jeepney: 4 }, 4),
    });
    assert.strictEqual(model.laterAttempt.status, status);
    assert.strictEqual(model.result.total, 4);
    const text = renderText(model);
    assert.ok(text.includes("4 line-crossing passages"), status);
    assert.ok(text.includes("jeepney"), status);
    assert.ok(text.includes("Latest attempt run #9: " + status), status);
    assert.ok(!text.includes("50"), status);
});

// The latest attempt itself remains the current completed result; no history is required.
{
    const payload = completed({ vehicles_crossed: 2, crossing_counts: { bus: 2 } });
    assert.strictEqual(summary.needsHistoricalResult(payload), false);
    const model = summary.buildModel(payload, {});
    assert.strictEqual(model.kind, "current");
    assert.strictEqual(model.result.total, 2);
}

// No current result, a removed result, a missing run, and a run that is not current.
{
    const none = summary.buildModel(
        { run_id: 3, current_result_run_id: null, latest_attempt_status: "failed", vehicles_crossed: 4, crossing_counts: { car: 4 } },
        { history: { success: true, current_result_run_id: null, processing_runs: [
            { id: 2, status: "completed", is_current_result: false, vehicles_crossed: 4, crossing_counts_json: "{\"car\":4}" },
        ] } },
    );
    assert.strictEqual(none.kind, "none");
    assert.strictEqual(none.result, null);
    assert.strictEqual(renderText(none), "");

    const missing = summary.buildModel(
        { run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed", vehicles_crossed: 50, crossing_counts: { car: 50 } },
        { history: { success: true, current_result_run_id: 7, processing_runs: [
            { id: 9, status: "failed", is_current_result: false, vehicles_crossed: 50, crossing_counts_json: "{\"car\":50}" },
        ] } },
    );
    assert.strictEqual(missing.result, null);
    const missingText = renderText(missing);
    assert.ok(missingText.includes("unavailable"));
    assert.ok(!missingText.includes("50"));

    const notCurrent = summary.buildModel(
        { run_id: 9, current_result_run_id: 7, latest_attempt_status: "cancelled", vehicles_crossed: 1, crossing_counts: { car: 1 } },
        { history: { success: true, current_result_run_id: 7, processing_runs: [
            { id: 7, status: "completed", is_current_result: false, vehicles_crossed: 6, crossing_counts_json: "{\"truck\":6}" },
        ] } },
    );
    assert.strictEqual(notCurrent.result, null);
    assert.ok(!renderText(notCurrent).includes("6"));

    const cleared = summary.buildModel(
        { run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed", vehicles_crossed: 8, crossing_counts: { car: 8 } },
        { history: { success: true, current_result_run_id: null, processing_runs: [
            { id: 7, status: "completed", is_current_result: false, vehicles_crossed: 8, crossing_counts_json: "{\"car\":8}" },
        ] } },
    );
    assert.strictEqual(cleared.result, null);
    const clearedText = renderText(cleared);
    assert.ok(clearedText.includes("No current completed result"));
    assert.ok(!clearedText.includes("8"));

    const wrongVideo = summary.buildModel(baseStatus(), {
        history: { success: true, current_result_run_id: 8, processing_runs: [
            { id: 8, status: "completed", is_current_result: true, vehicles_crossed: 11, crossing_counts_json: "{\"van\":11}" },
        ] },
    });
    assert.strictEqual(wrongVideo.result, null);
    assert.ok(!renderText(wrongVideo).includes("11"));
}

function baseStatus() {
    return {
        run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed",
        vehicles_crossed: 50, crossing_counts: { car: 50 },
    };
}

// Saved JSON on the historical run: null versus zero, malformed, absent, and non-vehicle classes.
{
    const base = {
        run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed",
        vehicles_crossed: 50, crossing_counts: { car: 50 },
    };
    function fromRun(runFields) {
        return summary.buildModel(base, { history: { success: true, current_result_run_id: 7, processing_runs: [
            Object.assign({ id: 7, status: "completed", is_current_result: true }, runFields),
        ] } });
    }
    const absent = fromRun({ vehicles_crossed: null, crossing_counts_json: null });
    assert.strictEqual(absent.result.total, null);
    assert.ok(renderText(absent).includes("Unavailable"));
    assert.ok(!renderText(absent).includes("0 line-crossing passages"));

    const zero = fromRun({ vehicles_crossed: 0, crossing_counts_json: "{}" });
    assert.strictEqual(zero.result.total, 0);
    assert.ok(renderText(zero).includes("0 line-crossing passages"));

    const malformed = fromRun({ vehicles_crossed: 2, crossing_counts_json: "not json" });
    assert.strictEqual(malformed.result.total, 2);
    assert.strictEqual(malformed.result.unreadable, true);
    assert.ok(renderText(malformed).includes("could not be read"));
    assert.ok(!renderText(malformed).includes("50"));

    const arrayJson = fromRun({ vehicles_crossed: 2, crossing_counts_json: "[1,2]" });
    assert.strictEqual(arrayJson.result.unreadable, true);

    const rejected = fromRun({
        vehicles_crossed: 3,
        crossing_counts_json: JSON.stringify({
            car: 3, person: 9, rider: 4, no_helmet: 1, van: "2", bus: -1,
        }),
    });
    assert.deepStrictEqual(rejected.result.rows, [{ label: "car", count: 3 }]);
    assert.ok(!renderText(rejected).includes("person"));
}

// A failed history request is visible and does not keep the later attempt's counts.
{
    const model = summary.buildModel({
        run_id: 9, current_result_run_id: 7, latest_attempt_status: "running",
        vehicles_crossed: 50, crossing_counts: { car: 50 },
    }, { historyFailed: true });
    assert.strictEqual(model.result, null);
    const text = renderText(model);
    assert.ok(text.includes("could not be loaded"));
    assert.ok(text.includes("Latest attempt run #9: running"));
    assert.ok(!text.includes("50"));
    const stale = summary.describeResult(6, { vehicles_crossed: 8, crossing_counts: { car: 8 } }, VEHICLES);
    assert.strictEqual(summary.buildModel({
        run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed", vehicles_crossed: 50,
    }, { historyFailed: true, cached: stale }).result, null);
}

// Status and history resolving out of order must not replace the selected video.
{
    const guard = summary.createSelectionGuard();
    const shown = [];
    const waits = [];
    function select(videoId) {
        guard.next(videoId);
        return { videoId: videoId, token: guard.current() };
    }
    function onStatus(entry, payload) {
        if (!guard.isCurrent(entry.token, entry.videoId)) return;
        if (summary.needsHistoricalResult(payload)) {
            waits.push({ entry: entry, payload: payload });
            return;
        }
        shown.push({ videoId: entry.videoId, model: summary.buildModel(payload, {}) });
    }
    function onHistory(wait, history, failed) {
        if (!guard.isCurrent(wait.entry.token, wait.entry.videoId)) return;
        shown.push({
            videoId: wait.entry.videoId,
            model: summary.buildModel(wait.entry.payload, failed ? { historyFailed: true } : { history: history }),
        });
    }
    const first = select(1);
    const second = select(2);
    const failed = {
        run_id: 9, current_result_run_id: 7, latest_attempt_status: "failed",
        vehicles_crossed: 50, crossing_counts: { car: 50 },
    };
    const current = completed({ run_id: 21, current_result_run_id: 21, vehicles_crossed: 1, crossing_counts: { car: 1 } });
    onStatus(second, current);
    onStatus(first, failed);
    assert.strictEqual(waits.length, 0);
    assert.strictEqual(shown.length, 1);
    assert.strictEqual(shown[0].videoId, 2);
    assert.strictEqual(shown[0].model.result.total, 1);

    const again = select(1);
    onStatus(again, failed);
    assert.strictEqual(waits.length, 1);
    const third = select(3);
    onHistory(waits[0], historyFor("failed", { car: 2, motorcycle: 1 }, 3), false);
    onStatus(third, completed({ run_id: 31, current_result_run_id: 31, vehicles_crossed: 6, crossing_counts: { truck: 6 } }));
    assert.strictEqual(shown.length, 2);
    assert.strictEqual(shown[1].videoId, 3);
    assert.strictEqual(shown[1].model.result.total, 6);
    assert.ok(shown.every(function (item) { return item.videoId !== 1 || item.model.resultRunId !== 7; }));
}

// Rapid video switching: a late response for an earlier selection is discarded.
{
    const guard = summary.createSelectionGuard();
    const rendered = [];
    const pending = [];
    function select(videoId) {
        guard.next(videoId);
        const token = guard.current();
        pending.push({ videoId: videoId, token: token });
    }
    function respond(entry, payload) {
        if (!guard.isCurrent(entry.token, entry.videoId)) return;
        rendered.push({ videoId: entry.videoId, model: summary.buildModel(payload, {}) });
    }
    select(1);
    select(2);
    respond(pending[1], completed({ run_id: 21, current_result_run_id: 21, vehicles_crossed: 1, crossing_counts: { car: 1 } }));
    respond(pending[0], completed({ run_id: 11, current_result_run_id: 11, vehicles_crossed: 9, crossing_counts: { bus: 9 } }));
    assert.strictEqual(rendered.length, 1);
    assert.strictEqual(rendered[0].videoId, 2);
    assert.strictEqual(rendered[0].model.resultRunId, 21);
    guard.clear();
    respond(pending[1], completed({}));
    assert.strictEqual(rendered.length, 1);
    select(1);
    assert.strictEqual(guard.isCurrent(pending[0].token, 1), false);
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise(function (res, rej) {
        resolve = res;
        reject = rej;
    });
    return { promise: promise, resolve: resolve, reject: reject };
}

function harness() {
    const guard = summary.createSelectionGuard();
    const cache = {};
    const shown = [];
    const pending = [];
    const loader = summary.createCrossingLoader({
        guard: guard,
        cache: cache,
        fetchHistory: function () {
            const wait = deferred();
            pending.push(wait);
            return wait.promise;
        },
        render: function (videoId, payload, extra) {
            const options = extra || {};
            const model = summary.buildModel(payload, {
                cached: cache[String(videoId)],
                history: options.history || null,
                historyFailed: !!options.historyFailed,
            });
            if (model.result && (model.kind === "current" || model.kind === "earlier")) {
                cache[String(videoId)] = model.result;
            }
            shown.push({ videoId: videoId, model: model, text: renderText(model) });
        },
    });
    return { guard: guard, cache: cache, shown: shown, pending: pending, loader: loader };
}

function run1History() {
    return {
        success: true,
        current_result_run_id: 1,
        processing_runs: [{
            id: 1, status: "completed", is_current_result: true, vehicles_crossed: 3,
            crossing_counts_json: JSON.stringify({ car: 2, motorcycle: 1, person: 9 }),
            class_counts_json: JSON.stringify({ car: 900 }),
        }, {
            id: 9, status: "failed", is_current_result: false, vehicles_crossed: 50,
            crossing_counts_json: JSON.stringify({ bus: 50 }),
        }],
    };
}

// The loader used by Live Monitor: an older history response for the same video
// must not replace a newer completed result, including when that history fails.
function testSameVideoHistoryOrder() {
    const page = harness();
    page.guard.next(1);
    const token = page.guard.current();
    const earlier = {
        success: true, run_id: 9, current_result_run_id: 1, latest_attempt_status: "failed",
        vehicles_crossed: 50, crossing_counts: { bus: 50 }, class_counts: { car: 900 },
    };
    page.loader.showStatus(1, token, earlier);
    assert.strictEqual(page.pending.length, 1);
    assert.strictEqual(page.shown.length, 0);

    const newer = completed({
        run_id: 2, current_result_run_id: 2, vehicles_crossed: 4, crossing_counts: { bus: 4 },
    });
    page.loader.showStatus(1, token, newer);
    assert.strictEqual(page.shown.length, 1);
    assert.strictEqual(page.shown[0].model.kind, "current");
    assert.strictEqual(page.shown[0].model.resultRunId, 2);
    assert.strictEqual(page.shown[0].model.result.total, 4);

    page.pending[0].resolve(run1History());
    return page.pending[0].promise.then(function () {
        assert.strictEqual(page.shown.length, 1);
        assert.strictEqual(page.shown[0].model.resultRunId, 2);
        assert.strictEqual(page.shown[0].model.result.total, 4);
        assert.ok(!page.shown[0].text.includes("could not be loaded"));
        assert.ok(!page.shown[0].text.includes("unavailable"));
        assert.strictEqual(page.cache["1"].runId, 2);
    }).then(function () {
        const failedPage = harness();
        failedPage.guard.next(1);
        const failedToken = failedPage.guard.current();
        failedPage.loader.showStatus(1, failedToken, earlier);
        failedPage.loader.showStatus(1, failedToken, newer);
        failedPage.pending[0].reject(new Error("history failed"));
        return failedPage.pending[0].promise.then(
            function () { throw new Error("history rejection should reject"); },
            function () { return failedPage; },
        );
    }).then(function (failedPage) {
        assert.strictEqual(failedPage.shown.length, 1);
        assert.strictEqual(failedPage.shown[0].model.resultRunId, 2);
        assert.strictEqual(failedPage.shown[0].model.result.total, 4);
        assert.ok(!failedPage.shown[0].text.includes("could not be loaded"));
        assert.strictEqual(failedPage.cache["1"].runId, 2);
    });
}

// A history response that is still current shows the earlier result, not the later attempt.
function testCurrentHistoryStillApplies() {
    const page = harness();
    page.guard.next(1);
    const token = page.guard.current();
    page.loader.showStatus(1, token, {
        success: true, run_id: 9, current_result_run_id: 1, latest_attempt_status: "queued",
        vehicles_crossed: 50, crossing_counts: { bus: 50 },
    });
    page.pending[0].resolve(run1History());
    return page.pending[0].promise.then(function () {
        assert.strictEqual(page.shown.length, 1);
        assert.strictEqual(page.shown[0].model.kind, "earlier");
        assert.strictEqual(page.shown[0].model.result.total, 3);
        assert.deepStrictEqual(page.shown[0].model.result.rows, [
            { label: "car", count: 2 }, { label: "motorcycle", count: 1 },
        ]);
        assert.ok(page.shown[0].text.includes("Latest attempt run #9: queued"));
        assert.ok(!page.shown[0].text.includes("50") && !page.shown[0].text.includes("900"));
        assert.ok(!page.shown[0].text.includes("person"));
    });
}

// A history response for video A must not render after video B is selected.
function testHistoryAfterVideoSwitch() {
    const page = harness();
    page.guard.next(1);
    const tokenA = page.guard.current();
    page.loader.showStatus(1, tokenA, {
        success: true, run_id: 9, current_result_run_id: 1, latest_attempt_status: "running",
        vehicles_crossed: 50, crossing_counts: { bus: 50 },
    });
    page.guard.next(2);
    const tokenB = page.guard.current();
    page.loader.showStatus(2, tokenB, completed({
        run_id: 21, current_result_run_id: 21, vehicles_crossed: 6, crossing_counts: { truck: 6 },
    }));
    page.pending[0].resolve(run1History());
    return page.pending[0].promise.then(function () {
        assert.strictEqual(page.shown.length, 1);
        assert.strictEqual(page.shown[0].videoId, 2);
        assert.strictEqual(page.shown[0].model.resultRunId, 21);
        assert.strictEqual(page.shown[0].model.result.total, 6);
        assert.ok(!page.shown[0].text.includes("motorcycle"));
    });
}

function attemptStatusPayload(attemptRun, status) {
    return {
        success: true, run_id: attemptRun, current_result_run_id: 1, latest_attempt_status: status,
        vehicles_crossed: 50, crossing_counts: { bus: 50 }, class_counts: { car: 900 },
    };
}

// Repeated status polls must share one history request and still render the
// earlier result once, labeled with the latest later attempt.
function testPollsShareOneHistoryRequest() {
    const page = harness();
    page.guard.next(1);
    const token = page.guard.current();
    page.loader.showStatus(1, token, attemptStatusPayload(9, "queued"));
    page.loader.showStatus(1, token, attemptStatusPayload(9, "running"));
    page.loader.showStatus(1, token, attemptStatusPayload(10, "running"));
    page.loader.showStatus(1, token, attemptStatusPayload(10, "failed"));
    assert.strictEqual(page.pending.length, 1);
    assert.strictEqual(page.shown.length, 0);
    page.pending[0].resolve(run1History());
    return page.pending[0].promise.then(function () {
        assert.strictEqual(page.shown.length, 1);
        assert.strictEqual(page.shown[0].model.kind, "earlier");
        assert.strictEqual(page.shown[0].model.result.total, 3);
        assert.deepStrictEqual(page.shown[0].model.result.rows, [
            { label: "car", count: 2 }, { label: "motorcycle", count: 1 },
        ]);
        assert.ok(page.shown[0].text.includes("Latest attempt run #10: failed"));
        assert.ok(!page.shown[0].text.includes("run #9"));
        assert.ok(!page.shown[0].text.includes("50") && !page.shown[0].text.includes("900"));
        assert.ok(!page.shown[0].text.includes("person"));
    });
}

// A failed history request is visible, and the next poll retries as one request.
function testFailedHistoryCanRetry() {
    const page = harness();
    page.guard.next(1);
    const token = page.guard.current();
    page.loader.showStatus(1, token, attemptStatusPayload(9, "running"));
    page.loader.showStatus(1, token, attemptStatusPayload(9, "failed"));
    assert.strictEqual(page.pending.length, 1);
    page.pending[0].reject(new Error("history failed"));
    return page.pending[0].promise.then(
        function () { throw new Error("history rejection should reject"); },
        function () {
            assert.strictEqual(page.shown.length, 1);
            assert.strictEqual(page.shown[0].model.result, null);
            assert.ok(page.shown[0].text.includes("could not be loaded"));
            assert.ok(page.shown[0].text.includes("Latest attempt run #9: failed"));
            assert.ok(!page.shown[0].text.includes("50"));
            page.loader.showStatus(1, token, attemptStatusPayload(11, "queued"));
            page.loader.showStatus(1, token, attemptStatusPayload(11, "running"));
            assert.strictEqual(page.pending.length, 2);
            page.pending[1].resolve(run1History());
            return page.pending[1].promise;
        },
    ).then(function () {
        assert.strictEqual(page.shown.length, 2);
        assert.strictEqual(page.shown[1].model.result.total, 3);
        assert.deepStrictEqual(page.shown[1].model.result.rows, [
            { label: "car", count: 2 }, { label: "motorcycle", count: 1 },
        ]);
        assert.ok(page.shown[1].text.includes("Latest attempt run #11: running"));
        assert.ok(!page.shown[1].text.includes("50"));
    });
}

testSameVideoHistoryOrder()
    .then(testCurrentHistoryStillApplies)
    .then(testHistoryAfterVideoSwitch)
    .then(testPollsShareOneHistoryRequest)
    .then(testFailedHistoryCanRetry)
    .then(function () {
        console.log("Crossing summary helper tests passed");
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
