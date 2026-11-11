const assert = require("node:assert/strict");
require("../../static/js/annotation_drafts.js");
require("../../static/js/zone_editor.js");

const drafts = global.TAVIDMAnnotationDrafts;
const values = new Map();
const storage = {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
};
const scene = { schema_version: 2, zones: [{ id: "zone-a" }], lanes: [] };

assert.notEqual(drafts.key(1, "camera", 5), drafts.key(2, "camera", 5));
assert.notEqual(drafts.key(1, "camera", 5), drafts.key(1, "video", 5));
const legacyKey = "tavidm.annotation-draft.5";
storage.setItem(legacyKey, JSON.stringify({ scene: "belongs-to-unknown-account" }));
assert.equal(drafts.read(storage, 1, "video", 5).draft, null);
assert.equal(drafts.read(storage, 2, "video", 5).draft, null);
assert.notEqual(storage.getItem(legacyKey), null);
assert.equal(drafts.write(storage, 1, "camera", 5, scene, { schema_version: 2, zones: [] }).ok, true);
const read = drafts.read(storage, 1, "camera", 5);
assert.equal(read.ok, true);
assert.deepEqual(read.draft.scene, scene);
assert.deepEqual(read.draft.base_scene, { schema_version: 2, zones: [] });
const serialize = (value) => JSON.stringify(value);
assert.equal(drafts.isStale(read.draft, { schema_version: 2, zones: [] }, serialize), false);
assert.equal(drafts.isStale(read.draft, { schema_version: 2, zones: [{ id: "newer" }] }, serialize), true);
assert.equal(drafts.isStale({ scene: scene, base_scene: null }, {}, serialize), true);
assert.equal(drafts.read(storage, 2, "camera", 5).draft, null);
assert.equal(drafts.remove(storage, 1, "camera", 5).ok, true);
assert.equal(drafts.read(storage, 1, "camera", 5).draft, null);
assert.notEqual(storage.getItem(legacyKey), null);
assert.equal(drafts.write(storage, 1, "video", 5, scene, {}).ok, true);
assert.equal(drafts.read(storage, 2, "video", 5).draft, null);
assert.deepEqual(drafts.read(storage, 1, "video", 5).draft.scene, scene);

const blockedStorage = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
    removeItem() { throw new Error("blocked"); },
};
assert.equal(drafts.read(blockedStorage, 1, "camera", 5).ok, false);
assert.equal(drafts.write(blockedStorage, 1, "camera", 5, scene, {}).ok, false);
assert.equal(drafts.remove(blockedStorage, 1, "camera", 5).ok, false);

const fullScene = {
    schema_version: 2,
    zones: [{ id: "zone-a", type: "no_parking", points: [[1, 2], [3, 4], [5, 6]], custom: { source: "template" } }],
    lanes: [{ id: "lane-a", type: "active_lane", points: [[7, 8], [9, 10], [11, 12]], lane_ids: ["lane-a"] }],
    flow_arrows: [{ id: "arrow-a", type: "lane_flow", points: [[2, 2], [4, 4]], lane_ids: ["lane-a"] }],
    threshold_lines: [], markings: [], signs: [], activity_regions: [],
};
const copiedScene = global.TAVIDMZoneEditor.parseSceneDocument(fullScene, []);
assert.deepEqual(copiedScene, fullScene);
copiedScene.zones[0].points[0][0] = 99;
assert.equal(fullScene.zones[0].points[0][0], 1);
console.log("PASS");
