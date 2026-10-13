"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

class Element {
    constructor(tagName = "DIV") {
        this.tagName = tagName;
        this.value = "";
        this.listeners = {};
        this.textContent = "";
        this.innerHTML = "";
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
}

const elements = new Map();
const getElementById = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id === "vehicleGrouping" ? "SELECT" : "DIV"));
    return elements.get(id);
};
const provenEvent = {
    id: 1, type: "Illegal Parking", type_slug: "illegal-parking", video_name: "camera.mp4",
    timestamp: "10:00", status: "pending", vehicle_class: "Car", confidence: 0.8,
};
let nextPayload = {
    success: true,
    groups: [
        { vehicle_identity_verified: true, track_id: 5, event_count: 1,
            violation_types: ["Illegal Parking"], events: [provenEvent] },
        { vehicle_identity_verified: false, event_count: 1, violation_types: ["Obstruction"],
            events: [{ ...provenEvent, id: 2, type: "Obstruction", type_slug: "obstruction" }] },
    ],
    total: 2, group_total: 2, verified_vehicle_total: 1,
    unassociated_event_total: 1, event_total: 2, page: 1,
};
const document = {
    body: new Element(),
    getElementById,
    querySelectorAll: () => [],
};
const window = {
    TAVIDM_VIOLATION_TOTAL: 0,
    location: { search: "" },
    addEventListener() {},
    setTimeout,
    clearTimeout,
};
const context = {
    window,
    document,
    bootstrap: { Modal: function Modal() { this.show = () => {}; this.hide = () => {}; } },
    fetch: async () => ({ ok: true, status: 200, json: async () => nextPayload }),
    URLSearchParams,
    setTimeout,
    clearTimeout,
    console,
};
vm.runInNewContext(fs.readFileSync("static/js/violations.js", "utf8"), context);

async function settle() {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
}

(async () => {
    const grouping = getElementById("vehicleGrouping");
    grouping.value = "vehicle";
    grouping.listeners.change();
    await settle();

    assert.match(getElementById("resultCount").textContent, /2 display groups · 1 proven vehicle · 1 unassociated event · 2 events/);
    assert.match(getElementById("paginationInfo").textContent, /2 display groups \(1 proven vehicle, 1 unassociated event, 2 matching events\)/);
    assert.match(getElementById("violationsBody").innerHTML, /Identity unavailable · individual event/);
    assert.match(getElementById("violationsBody").innerHTML, /<th>Violation Type<\/th>/);
    assert.doesNotMatch(getElementById("resultCount").textContent, /\bvehicles \/\b/);

    nextPayload = {
        success: true, groups: [], total: 0, group_total: 0,
        verified_vehicle_total: 0, unassociated_event_total: 0, event_total: 0, page: 1,
    };
    grouping.listeners.change();
    await settle();
    assert.match(getElementById("violationsBody").innerHTML, /No violation display groups match these filters/);
    assert.match(getElementById("resultCount").textContent, /^0 display groups/);
    console.log("vehicle grouping presentation checks passed");
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
