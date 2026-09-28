"use strict";

const assert = require("assert");

global.window = { addEventListener: () => {}, innerWidth: 1280, confirm: () => true };
global.document = { getElementById: () => null };
global.bootstrap = { Modal: function Modal() {} };

const detailUi = require("../../static/js/motorcycle_detail_review.js");

// Production defect: a missed mirror detection is UNKNOWN, never proof of
// absence. The reviewer-facing copy must never imply a proven violation.
assert.strictEqual(
    detailUi.describeMirror({ state: "none_visible" }),
    "no mirror observed — unknown, not proof of absence"
);
assert.strictEqual(detailUi.describeMirror({ state: "both_visible" }), "both mounting areas observed");
assert.strictEqual(detailUi.describeMirror({ state: "one_left" }), "one mirror observed (left)");
assert.strictEqual(detailUi.describeMirror({ state: "ambiguous" }),
    "ambiguous: overlapping motorcycles unresolved");
assert.strictEqual(detailUi.describeMirror(null), "not scanned yet");

// Helmet states must not be rendered as a decided "no helmet" outcome.
assert.strictEqual(detailUi.describeHelmet({ state: "unknown" }), "helmet unknown — absence is not proven");
assert.strictEqual(detailUi.describeHelmet({ state: "acceptable" }), "helmet detected (acceptable shape)");
assert.strictEqual(detailUi.describeHelmet({ state: "nut_shell" }), "nut-shell / substandard helmet shape");

// Rider association: ambiguous stays ambiguous; a person is never a rider.
assert.strictEqual(detailUi.describeRider({ state: "associated" }), "rider associated");
assert.strictEqual(detailUi.describeRider({ state: "ambiguous" }), "ambiguous: multiple riders overlap");
assert.strictEqual(detailUi.describeRider({ state: "unassociated" }), "no rider associated");

// Uncertainty reasons are merged and de-duplicated for display.
assert.deepStrictEqual(
    detailUi.uncertaintyList({
        uncertainty: ["mirror:absence_not_proven_unknown"],
        observations: {
            uncertainty: ["mirror:absence_not_proven_unknown", "crop_clamped_at_frame_edge"]
        }
    }),
    ["mirror:absence_not_proven_unknown", "crop_clamped_at_frame_edge"]
);
assert.deepStrictEqual(detailUi.uncertaintyList({}), []);

console.log("Motorcycle detail review helper tests passed");
