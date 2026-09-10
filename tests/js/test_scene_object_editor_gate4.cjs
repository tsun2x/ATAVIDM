/**
 * Gate 4 — executable SceneObjectEditor / ZoneEditor behavioral tests.
 *
 * Exercises create, select, move, delete, save (toDocument), and reload
 * (loadDocument) across v2 object kinds. Static source-string checks are
 * not sufficient.
 *
 * Run: node tests/js/test_scene_object_editor_gate4.cjs
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");
const JS = fs.readFileSync(path.join(ROOT, "static", "js", "zone_editor.js"), "utf8");

const ZONE_TYPES = [
    { key: "no_parking", label: "No Parking Zone", color: "#e63946" },
    { key: "active_lane", label: "Active Lane", color: "#3b82f6" },
    { key: "pedestrian_crossing", label: "Pedestrian Crossing", color: "#22c55e" },
    { key: "truck_ban_zone", label: "Truck Ban Zone", color: "#f59e0b" },
    { key: "loading_unloading", label: "No Loading/Unloading Zone", color: "#8b5cf6" },
    { key: "restricted_lane", label: "Restricted Lane", color: "#ec4899" },
];

function fakeCtx() {
    return {
        clearRect() {},
        save() {},
        restore() {},
        translate() {},
        drawImage() {},
        beginPath() {},
        moveTo() {},
        lineTo() {},
        closePath() {},
        fill() {},
        stroke() {},
        arc() {},
        fillText() {},
        fillStyle: "",
        strokeStyle: "",
        lineWidth: 1,
        font: "",
    };
}

function loadEditor() {
    const listeners = [];
    const canvas = {
        width: 400,
        height: 300,
        style: {},
        parentElement: { clientWidth: 400, clientHeight: 300 },
        getContext() { return fakeCtx(); },
        addEventListener(type, fn) { listeners.push({ type, fn }); },
        removeEventListener() {},
        getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
    };
    const sandbox = {
        console,
        Image: function FakeImage() {},
        window: {
            addEventListener() {},
            removeEventListener() {},
        },
        document: {},
        global: null,
    };
    sandbox.window = sandbox.window;
    sandbox.global = sandbox;
    vm.runInNewContext(JS, sandbox);
    const api = sandbox.window.TAVIDMZoneEditor || sandbox.TAVIDMZoneEditor;
    assert.ok(api, "TAVIDMZoneEditor must be exported");
    return { api, canvas };
}

function run() {
    const { api, canvas } = loadEditor();

    const kinds = {
        zones: { type: "loading_unloading", points: [[10, 10], [40, 10], [40, 40], [10, 40]] },
        lanes: { type: "active_lane", points: [[50, 10], [120, 10], [120, 50], [50, 50]] },
        flow_arrows: { type: "lane_flow", points: [[60, 30], [110, 30]], fields: { lane_ids: ["lane-keep"] } },
        threshold_lines: { type: "no_entry_threshold", points: [[20, 80], [90, 80]] },
        markings: { type: "double_solid", points: [[0, 60], [200, 60]], fields: { prohibited_from: "both" } },
        signs: { type: "no_entry", points: [[130, 10], [160, 10], [160, 40], [130, 40]] },
        activity_regions: { type: "passenger_activity", points: [[170, 50], [220, 50], [220, 90], [170, 90]] },
    };

    const editor = api.createSceneObjectEditor();
    const createdIds = {};

    Object.keys(kinds).forEach(function (kind) {
        const spec = kinds[kind];
        const fields = Object.assign({}, spec.fields || {});
        if (kind === "lanes") fields.id = "lane-keep";
        const obj = editor.create(kind, spec.type, spec.points, fields);
        assert.ok(obj.id, kind + " must receive a stable id");
        createdIds[kind] = obj.id;
        assert.strictEqual(editor.select(obj.id), obj.id);
    });

    assert.ok(editor.movePoint(createdIds.lanes, 0, [55, 12]));
    const moved = editor.findById(createdIds.lanes).object.points[0];
    assert.strictEqual(Number(moved[0]), 55);
    assert.strictEqual(Number(moved[1]), 12);

    const extraLane = editor.create("lanes", "active_lane", [[230, 10], [300, 10], [300, 50], [230, 50]]);
    assert.notStrictEqual(extraLane.id, createdIds.lanes);

    const signId = createdIds.signs;
    assert.ok(editor.delete(signId));
    assert.strictEqual(editor.findById(signId), null);
    editor.create("signs", "no_left_turn", [[130, 10], [160, 10], [160, 40], [130, 40]], { id: "sign-keep" });

    const saved = editor.toDocument();
    assert.strictEqual(saved.schema_version, 2);
    assert.strictEqual(saved.lanes.length, 2);
    assert.strictEqual(saved.lanes[0].id, "lane-keep");
    assert.strictEqual(Number(saved.lanes[0].points[0][0]), 55);
    assert.strictEqual(Number(saved.lanes[0].points[0][1]), 12);
    assert.ok(saved.zones.some(function (z) { return z.type === "loading_unloading"; }));
    assert.ok(saved.flow_arrows[0].lane_ids.indexOf("lane-keep") >= 0);
    assert.strictEqual(saved.markings[0].prohibited_from, "both");
    const snapshotIds = {};
    ["zones", "lanes", "flow_arrows", "threshold_lines", "markings", "signs", "activity_regions"].forEach(function (key) {
        snapshotIds[key] = saved[key].map(function (o) { return o.id; });
    });

    const reloaded = api.createSceneObjectEditor();
    reloaded.loadDocument(saved);
    const again = reloaded.toDocument();
    assert.strictEqual(again.schema_version, 2);
    ["zones", "lanes", "flow_arrows", "threshold_lines", "markings", "signs", "activity_regions"].forEach(function (key) {
        const gotIds = again[key].map(function (o) { return o.id; });
        assert.strictEqual(
            JSON.stringify(gotIds),
            JSON.stringify(snapshotIds[key]),
            key + " ids must survive reload"
        );
        assert.strictEqual(JSON.stringify(again[key]), JSON.stringify(saved[key]), key + " geometry/metadata must survive reload");
    });
    assert.ok(!Object.prototype.hasOwnProperty.call(again, "no_loading"));

    const canvasEditor = api.create(canvas, {
        zoneTypes: ZONE_TYPES,
        sceneDocument: saved,
        image: { naturalWidth: 400, naturalHeight: 300 },
    });
    assert.strictEqual(canvasEditor.getSceneDocument().schema_version, 2);
    assert.strictEqual(canvasEditor.getSceneDocument().lanes[0].id, "lane-keep");
    canvasEditor.setObjectKind("zones", "no_parking");
    canvasEditor._ensureCreating([5, 5]);
    canvasEditor._ensureCreating([25, 5]);
    canvasEditor._ensureCreating([25, 25]);
    canvasEditor._ensureCreating([5, 25]);
    const selectedId = canvasEditor.scene.selectedId;
    assert.ok(selectedId);
    assert.ok(canvasEditor.scene.movePoint(selectedId, 1, [28, 6]));
    const beforeDelete = canvasEditor.getSceneDocument().zones.length;
    canvasEditor.scene.select(selectedId);
    canvasEditor.deleteSelected();
    assert.strictEqual(canvasEditor.getSceneDocument().zones.length, beforeDelete - 1);

    canvasEditor.loadSceneDocument(saved);
    const afterLoad = canvasEditor.getSceneDocument();
    assert.strictEqual(afterLoad.schema_version, 2);
    assert.strictEqual(
        JSON.stringify(afterLoad.lanes.map(function (o) { return o.id; })),
        JSON.stringify(snapshotIds.lanes)
    );
    const legacy = canvasEditor.getZones();
    assert.ok(legacy.loading_unloading && legacy.loading_unloading.length >= 3);
    assert.strictEqual(typeof legacy.no_loading, "undefined");

    const roundTrip = api.parseSceneDocument(JSON.stringify(saved), ZONE_TYPES);
    assert.strictEqual(roundTrip.schema_version, 2);
    assert.strictEqual(roundTrip.lanes[0].id, "lane-keep");
    assert.ok(roundTrip.flow_arrows.length >= 1);
    assert.ok(roundTrip.activity_regions.length >= 1);

    console.log("PASS gate4 scene object editor behavioral tests");
}

run();
