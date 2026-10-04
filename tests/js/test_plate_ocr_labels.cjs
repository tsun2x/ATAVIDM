"use strict";

// Focused tests for the experimental local plate OCR presentation layer.
//
// These are the rules that must hold in the browser even if the server is
// bypassed: machine states stay distinct from human plate status, OCR text is
// escaped, "no candidate" is never phrased as "no plate exists", and
// confirmation controls are rendered only for an administrator.

const assert = require("assert");

global.window = { addEventListener: () => {}, innerWidth: 1280 };
// Minimal DOM stub: the module's page bootstrap is skipped by the module guard,
// but the file still touches a few document helpers at load time.
const noopList = () => [];
global.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: noopList,
    body: { dataset: {} },
};
global.bootstrap = { Modal: function Modal() {}, Toast: function Toast() {} };

const reviewUi = require("../../static/js/review_queue.js");

const copy = reviewUi.plateMachineStateCopy;

// 1. Every machine outcome has an explicit, non-misleading label.
const requiredStates = [
    "disabled",
    "queued",
    "processing",
    "unavailable",
    "failed",
    "candidate_found",
    "no_candidate_detected",
    "detected_unreadable",
    "quality_rejected",
    "association_uncertain",
    "cancelled",
    "budget_exhausted",
];
for (const state of requiredStates) {
    const entry = reviewUi.plateMachineStateCopy(state);
    assert.ok(entry.label, "missing label for state " + state);
    assert.ok(entry.text, "missing copy for state " + state);
}

// 2. "No result" must never be phrased as "no plate exists" or as a human
//    "not visible" judgement.
// 2. "No result" must never be *affirmatively* phrased as "there is no plate"
//    or as a human "plate is not visible" judgement. The required copy
//    deliberately contains the negated forms ("this does not mean no plate
//    exists"), so only affirmative claims are rejected here.
const forbiddenAffirmativeClaims = [
    "there is no plate",
    "no plate exists on",
    "the plate is not visible",
    "plate was not visible",
    "no alpr in this build",
    "license plate not visible",
];
for (const state of requiredStates) {
    const text = String(reviewUi.plateMachineStateCopy(state).text).toLowerCase();
    for (const phrase of forbiddenAffirmativeClaims) {
        assert.ok(
            !text.includes(phrase),
            "state " + state + " copy must not claim " + phrase + ": " + text
        );
    }
    assert.ok(
        !/\bno alpr\b/i.test(text),
        "state " + state + " copy must not mention a missing ALPR build: " + text
    );
}

// 3. The empty outcome is explicitly ambiguous.
assert.ok(
    reviewUi.plateMachineStateCopy("no_candidate_detected").text.toLowerCase()
        .includes("does not mean no plate exists")
);
assert.strictEqual(reviewUi.plateMachineStateLabel("no_candidate_detected"), "No candidate detected");
assert.strictEqual(reviewUi.plateMachineStateLabel("detected_unreadable"), "Plate detected; text unreadable");
assert.strictEqual(reviewUi.plateMachineStateLabel("association_uncertain"), "Association uncertain");
assert.strictEqual(reviewUi.plateMachineStateLabel("totally_unknown"), "Unknown");

// 4. Case-detail confirmation eligibility is admin-only AND requires a
//    resolved machine candidate. Review Queue itself only explains the next step.
const eligible = {
    eligible_for_confirmation: true,
    association_uncertain: false,
    candidates: [{ candidate_id: "c1", ocr_raw: "ABC123" }],
};
assert.strictEqual(reviewUi.plateConfirmControlsAllowed(eligible, true), true);
assert.strictEqual(reviewUi.plateConfirmControlsAllowed(eligible, false), false);
assert.strictEqual(
    reviewUi.plateConfirmControlsAllowed({ ...eligible, association_uncertain: true }, true),
    false
);
assert.strictEqual(
    reviewUi.plateConfirmControlsAllowed({ ...eligible, eligible_for_confirmation: false }, true),
    false
);
assert.strictEqual(
    reviewUi.plateConfirmControlsAllowed({ ...eligible, candidates: [] }, true),
    false
);
assert.strictEqual(reviewUi.plateConfirmControlsAllowed(null, true), false);

// 5. OCR text is escaped, so a hostile read cannot inject markup.
assert.strictEqual(
    reviewUi.escapePlateText('<img src=x onerror="alert(1)">'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
);
assert.strictEqual(reviewUi.escapePlateText("O0I1"), "O0I1");
assert.strictEqual(reviewUi.escapePlateText(null), "");
assert.strictEqual(reviewUi.escapePlateText(undefined), "");

console.log("Plate OCR presentation tests passed");