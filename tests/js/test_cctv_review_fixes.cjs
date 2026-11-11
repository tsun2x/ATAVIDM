const assert = require("node:assert/strict");
require("../../static/js/camera_reconnect.js");
require("../../static/js/zone_template_cache.js");

async function run() {
    const reconnect = global.TAVIDMCameraReconnect.create(3);
    assert.equal(reconnect.observe("running", true), "idle");
    reconnect.begin();
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("starting", false), "waiting");
    assert.equal(reconnect.observe("running", true), "reconnect");
    assert.equal(reconnect.observe("running", true), "idle");

    reconnect.begin();
    assert.equal(reconnect.observe("error", false), "error");
    reconnect.begin();
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("stopping", true), "timeout");
    reconnect.reset();
    assert.equal(reconnect.observe("running", true), "idle");
    reconnect.begin();
    assert.equal(reconnect.observe("stopped", false), "stopped");
    assert.equal(reconnect.observe("running", true), "idle");

    const cache = global.TAVIDMZoneTemplateCache;
    const old = { id: 1, template_name: "Existing" };
    const created = { id: 2, template_name: "New camera copy" };
    assert.deepEqual(cache.upsert([old], created), [old, created]);

    const refreshed = await cache.refresh(
        [old, { id: 9, template_name: "Deleted on server" }],
        created,
        async () => [{ id: 1, template_name: "Updated" }]
    );
    assert.equal(refreshed.ok, true);
    assert.deepEqual(refreshed.templates.map((item) => item.id), [1, 2]);
    assert.equal(refreshed.templates[0].template_name, "Updated");
    assert.equal(refreshed.templates.some((item) => item.id === 9), false);

    const failed = await cache.refresh([old], created, async () => { throw new Error("offline"); });
    assert.equal(failed.ok, false);
    assert.deepEqual(failed.templates.map((item) => item.id), [1, 2]);
    assert.equal(failed.templates[1].template_name, "New camera copy");
    console.log("PASS");
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
