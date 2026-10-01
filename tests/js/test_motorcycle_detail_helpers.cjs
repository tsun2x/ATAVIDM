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
    "ambiguous: mirror may belong to another motorcycle");
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

// Helmet attribution shared with another rider stays ambiguous, never decided.
assert.strictEqual(detailUi.describeHelmet({ state: "ambiguous" }),
    "ambiguous: helmet may belong to another rider");
assert.ok(detailUi.describeHelmet({ state: "uncovered_head" }).startsWith("Uncovered head observed"));
assert.ok(detailUi.describeHelmet({ state: "uncovered_head" }).includes("not a confirmed no-helmet violation"));
assert.ok(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["contradictory_head_labels"]
}).startsWith("ambiguous:"));
assert.ok(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["contradictory_helmet_observations_across_frames"]
}).includes("ambiguous"));
assert.ok(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["uncovered_head_box_clipped_or_unclear"]
}).includes("ambiguous"));

// Cross-frame summaries: evidence from a later selected frame keeps the
// review-only wording, and an ambiguous later frame stays visibly ambiguous.
const laterUncovered = detailUi.describeHelmet({
    state: "uncovered_head",
    reasons: ["evidence_on_some_selected_frames_only"]
});
assert.ok(laterUncovered.startsWith("Uncovered head observed"));
assert.ok(laterUncovered.includes("not a confirmed no-helmet violation"));
assert.strictEqual(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["evidence_on_some_selected_frames_only", "helmet_in_other_rider_head_region_unattributed"]
}), "ambiguous: helmet may belong to another rider");
assert.strictEqual(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["evidence_on_some_selected_frames_only", "contradictory_head_labels"]
}), "ambiguous: conflicting head observations; human verification required");
assert.strictEqual(detailUi.describeHelmet({
    state: "ambiguous",
    reasons: ["contradictory_helmet_observations_across_frames", "evidence_on_some_selected_frames_only"]
}), "ambiguous: conflicting head observations; human verification required");
assert.strictEqual(
    detailUi.formatClassMap(["helmet_nut_shell", "helmet_acceptable", "side_mirror", "no_helmet"]),
    "0=helmet_nut_shell, 1=helmet_acceptable, 2=side_mirror, 3=no_helmet"
);
const fourClass = detailUi.describeModel({
    model: "md4c:best.pt:abc123def456",
    model_kind: "motorcycle_detail_4class",
    architecture: "yolov8n",
    contract: "md-detail-4c-v1"
}, {});
assert.ok(fourClass.includes("motorcycle_detail_4class"));
assert.ok(fourClass.includes("md-detail-4c-v1"));
assert.ok(!fourClass.includes("motorcycle_detail_3class"));
assert.strictEqual(detailUi.describeRider({ state: "associated", source: "main_detector" }),
    "rider associated (main detector)");

// Class maps are shown with their IDs, for both new and older stored rows.
assert.strictEqual(
    detailUi.formatClassMap({ "0": "helmet_nut_shell", "1": "helmet_acceptable", "2": "side_mirror" }),
    "0=helmet_nut_shell, 1=helmet_acceptable, 2=side_mirror"
);
assert.strictEqual(detailUi.formatClassMap(["car", "van"]), "0=car, 1=van");
assert.strictEqual(detailUi.formatClassMap(null), "");

// A three-class detail checkpoint is labelled as such, never as the main detector.
const described = detailUi.describeModel({
    model: "md3c:best.pt:abc123def456",
    model_kind: "motorcycle_detail_3class",
    architecture: "yolov8n",
    contract: "md-detail-3c-v1"
}, {});
assert.ok(described.includes("motorcycle_detail_3class"));
assert.ok(described.includes("yolov8n"));
assert.ok(!described.includes("15-class"));
assert.ok(detailUi.describeModel({ model: "YOLOv8m:mock" }, {}).includes("older scan"));
assert.strictEqual(detailUi.describeModel({}, {}), "—");

// Attribution context: older rows say nearby motorcycles were not recorded.
assert.ok(detailUi.describeAttribution({ context: { recorded: false } }).includes("not recorded"));
assert.ok(detailUi.describeAttribution({
    context: {
        recorded: true, rider_state: "associated", rider_track_id: 11,
        nearby_motorcycles: [{ track_id: 8 }], nearby_riders: []
    }
}).includes("nearby motorcycles 1"));
assert.strictEqual(detailUi.describeAttribution({}), "not recorded for this scan");

// Truncation is shown per list; an older generic flag marks both lists.
const truncatedBikes = detailUi.describeAttribution({
    context: {
        recorded: true, rider_state: "associated", nearby_motorcycles: [], nearby_riders: [],
        truncated: true, truncated_lists: { motorcycles: true, riders: false }
    }
});
assert.ok(truncatedBikes.includes("nearby motorcycles 0 (list truncated)"));
assert.ok(!truncatedBikes.includes("other riders 0 (list truncated)"));
const genericFlag = detailUi.describeAttribution({
    context: { recorded: true, rider_state: "associated", nearby_motorcycles: [], nearby_riders: [], truncated: true }
});
assert.ok(genericFlag.includes("nearby motorcycles 0 (list truncated)"));
assert.ok(genericFlag.includes("other riders 0 (list truncated)"));

// Mapped detections are looked up per stored frame index.
assert.deepStrictEqual(
    detailUi.frameDetections({ observations: { frames: [{ index: 2, detections: [{ class_label: "side_mirror" }] }] } }, 2),
    [{ class_label: "side_mirror" }]
);
assert.deepStrictEqual(detailUi.frameDetections({}, 1), []);

console.log("Motorcycle detail review helper tests passed");
