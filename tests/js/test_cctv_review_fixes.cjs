const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
require("../../static/js/camera_reconnect.js");
require("../../static/js/camera_reconnect_ui.js");
require("../../static/js/zone_template_cache.js");

async function run() {
    const reconnect = global.TAVIDMCameraReconnect.create(3);
    assert.equal(reconnect.observe("live", true), "idle");
    reconnect.begin();
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("starting", false), "waiting");
    let requested = 0;
    assert.equal(reconnect.observe("live", true, () => { requested += 1; }), "attaching");
    assert.equal(requested, 1);
    assert.equal(reconnect.imageLoaded(), true);
    assert.equal(reconnect.observe("live", true), "idle");

    reconnect.begin();
    assert.equal(reconnect.observe("error", false), "error");
    reconnect.begin();
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("stopping", true), "waiting");
    assert.equal(reconnect.observe("stopping", true), "timeout");
    reconnect.reset();
    assert.equal(reconnect.observe("live", true), "idle");
    reconnect.begin();
    assert.equal(reconnect.observe("stopped", false), "stopped");
    assert.equal(reconnect.observe("live", true), "idle");

    // This is the DOM-facing production adapter used by live_monitor.js: it
    // requests the new MJPEG URL only for the backend's real `live` status,
    // and reports success only after that image emits its load event.
    const urls = [];
    const statuses = [];
    const camera = { id: 7, stream_url: "/api/cameras/7/stream" };
    const ui = global.TAVIDMLiveCameraReconnectUI.create(
        global.TAVIDMCameraReconnect.create(6),
        { showImage: (url) => urls.push(url), setStatus: (text) => statuses.push(text) }
    );
    assert.equal(ui.begin(camera, "stopping", true), "waiting");
    assert.equal(ui.observe(camera, "starting", false), "waiting");
    assert.equal(ui.observe(camera, "reconnecting", true), "waiting");
    assert.equal(urls.length, 0);
    assert.equal(ui.observe(camera, "live", true), "attaching");
    assert.equal(urls.length, 1);
    assert.equal(urls[0].startsWith(camera.stream_url + "?reconnect="), true);
    assert.equal(statuses.at(-1).includes("attaching"), true);
    assert.equal(ui.observe(camera, "live", true), "waiting");
    assert.equal(urls.length, 1, "repeated status polls must not multiply MJPEG requests");
    assert.equal(ui.imageLoaded("/stale-stream"), false);
    assert.equal(statuses.at(-1).includes("attaching"), true, "live status alone must not claim attachment success");
    assert.equal(ui.imageLoaded(urls[0]), true);
    assert.equal(statuses.at(-1), "Reconnected to the updated camera stream.");

    // A save callback that arrives after its replacement is already live uses
    // the same production path, and independent viewers own independent state.
    const otherUrls = [];
    const secondViewer = global.TAVIDMLiveCameraReconnectUI.create(
        global.TAVIDMCameraReconnect.create(4), { showImage: (url) => otherUrls.push(url) }
    );
    assert.equal(ui.begin(camera, "live", true), "attaching");
    assert.equal(secondViewer.begin(camera, "live", true), "attaching");
    assert.equal(urls.length, 2);
    assert.equal(otherUrls.length, 1);
    assert.equal(ui.imageFailed(urls[1]), "retry");
    assert.equal(ui.observe(camera, "live", true), "attaching");
    assert.equal(urls.length, 3);
    assert.equal(ui.imageLoaded(urls[2]), true);
    assert.equal(secondViewer.imageLoaded(otherUrls[0]), true);

    const immediateUrls = [];
    const immediate = global.TAVIDMLiveCameraReconnectUI.create(
        global.TAVIDMCameraReconnect.create(2), { showImage: (url) => immediateUrls.push(url) }
    );
    assert.equal(immediate.begin(camera, "live", true), "attaching");
    assert.equal(immediateUrls.length, 1);
    assert.equal(immediate.imageLoaded(immediateUrls[0]), true);

    const alreadyStopped = global.TAVIDMLiveCameraReconnectUI.create(
        global.TAVIDMCameraReconnect.create(2), { showImage: (url) => immediateUrls.push(url) }
    );
    assert.equal(alreadyStopped.begin(camera, "stopped", false), "stopped");
    assert.equal(immediateUrls.length, 1, "explicit stop must not trigger an unwanted stream start");

    const liveMonitorSource = fs.readFileSync(path.join(__dirname, "../../static/js/live_monitor.js"), "utf8");
    const liveMonitorTemplate = fs.readFileSync(path.join(__dirname, "../../templates/live_monitor.html"), "utf8");
    assert.match(liveMonitorSource, /cameraReconnectUI\.observe\(camera, payload\.status, payload\.running\)/);
    assert.match(liveMonitorSource, /cameraReconnectUI\.begin\(camera, stream\.status, stream\.running\)/);
    assert.match(liveMonitorSource, /cameraReconnectUI\.imageLoaded\(feedImage\.getAttribute\("src"\)\)/);
    assert.match(liveMonitorSource, /cameraReconnectUI\.imageFailed\(feedImage\.getAttribute\("src"\)\)/);
    assert.ok(liveMonitorTemplate.indexOf("js/camera_reconnect_ui.js") < liveMonitorTemplate.indexOf("js/live_monitor.js"));

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
