"use strict";

const assert = require("assert");
const jobs = require("../../static/js/processing_jobs.js");

async function flush() {
    await Promise.resolve();
    await Promise.resolve();
}

async function run() {
    // Catches malformed navigation state selecting the wrong video or tab.
    assert.deepStrictEqual(
        jobs.parseLiveMonitorTarget("?video_id=7&run_id=12&jobs=completed"),
        { videoId: 7, runId: 12, tab: "completed" }
    );
    assert.deepStrictEqual(
        jobs.parseLiveMonitorTarget("?video_id=nope&jobs=unknown"),
        { videoId: null, runId: null, tab: "active" }
    );
    // Catches terminal states masquerading as previewable current results.
    assert.strictEqual(jobs.describeJobState({ status: "completed", is_current_result: true }), "Current result");
    assert.strictEqual(jobs.describeJobState({ status: "completed", is_superseded: true }), "Superseded");
    assert.strictEqual(jobs.describeJobState({ status: "cancelled" }), "Cancelled");

    // Catches duplicate polling loops after repeated page/component initialization.
    const timers = [];
    const states = [];
    let requests = 0;
    const controller = jobs.createController({
        fetchFn: async () => {
            requests += 1;
            return {
                ok: true,
                json: async () => ({ success: true, poll_after_ms: 1500, active: [{ id: 9 }], recent: [] }),
            };
        },
        setTimeoutFn: (callback, delay) => {
            timers.push({ callback, delay });
            return timers.length;
        },
        clearTimeoutFn: () => {},
        onState: (snapshot, meta) => states.push({ snapshot, meta }),
    });
    controller.start();
    controller.start();
    await flush();
    assert.strictEqual(requests, 1);
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(timers[0].delay, 1500);
    assert.strictEqual(states[0].snapshot.active[0].id, 9);
    assert.strictEqual(states[0].meta.stale, false);

    // Catches transient failures clearing the last known globally visible job.
    const retryTimers = [];
    let fail = false;
    const retained = [];
    const retrying = jobs.createController({
        fetchFn: async () => {
            if (fail) throw new Error("offline");
            return { ok: true, json: async () => ({ success: true, poll_after_ms: 10000, active: [], recent: [{ id: 4 }] }) };
        },
        setTimeoutFn: (callback, delay) => {
            retryTimers.push({ callback, delay });
            return retryTimers.length;
        },
        clearTimeoutFn: () => {},
        onState: (snapshot, meta) => retained.push({ snapshot, meta }),
    });
    retrying.start();
    await flush();
    assert.strictEqual(retryTimers[0].delay, 10000);
    fail = true;
    await retryTimers[0].callback();
    await flush();
    assert.strictEqual(retained.at(-1).snapshot.recent[0].id, 4);
    assert.strictEqual(retained.at(-1).meta.stale, true);
    assert.ok(retryTimers.at(-1).delay >= 5000 && retryTimers.at(-1).delay <= 30000);

    console.log("processing jobs controller tests passed");
}

run().catch((error) => {
    console.error(error);
    process.exit(1);
});
