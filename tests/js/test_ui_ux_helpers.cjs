"use strict";

const assert = require("assert");

global.window = { addEventListener: () => {}, innerWidth: 1280 };
global.document = {
    getElementById: () => null,
};
global.bootstrap = {
    Modal: function Modal() {},
    Toast: function Toast() {},
};

const mainUi = require("../../static/js/main.js");
const reviewUi = require("../../static/js/review_queue.js");

// Production defect: informational feedback is styled as danger and user/API text
// can break out of the toast body as executable HTML.
assert.strictEqual(mainUi.toastToneClass("info"), "text-bg-info");
assert.strictEqual(
    mainUi.escapeHtml('<img src=x onerror="alert(1)">'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
);

// Production defect: review filters have no single, reusable definition and can
// drift from the confidence bands shown to reviewers.
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.79 }, "low"), true);
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.80 }, "low"), false);
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.80 }, "careful"), true);
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.949 }, "careful"), true);
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.95 }, "careful"), false);
assert.strictEqual(reviewUi.matchesReviewFilter({ confidence: 0.95 }, "all"), true);

console.log("UI/UX helper tests passed");
