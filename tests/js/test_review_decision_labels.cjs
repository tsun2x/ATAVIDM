const assert = require("assert");
const helper = require("../../static/js/review_queue.js");

assert.strictEqual(helper.reviewDecisionLabel("insufficient_evidence"), "Insufficient evidence");
assert.notStrictEqual(helper.reviewDecisionLabel("insufficient_evidence"), "No violation");
assert.strictEqual(helper.reviewDecisionLabel("no_violation"), "No violation");
assert.strictEqual(helper.reviewDecisionLabel("confirm_proposed"), "Confirmed proposed violation");
assert.strictEqual(helper.reviewDecisionLabel("correct_canonical"), "Corrected canonical violation");
assert.strictEqual(helper.matchesReviewFilter({ confidence: 0.5 }, "low"), true);

const confirmPlan = helper.planReviewQueueAction("confirm");
const dismissPlan = helper.planReviewQueueAction("dismiss");
assert.strictEqual(confirmPlan.urlAction, "confirm");
assert.strictEqual(confirmPlan.decision, "confirm_proposed");
assert.strictEqual(dismissPlan.urlAction, "dismiss");
assert.strictEqual(dismissPlan.decision, "no_violation");
assert.strictEqual(helper.planReviewQueueAction("decide").urlAction, "decision");
assert.strictEqual(helper.reviewActionClosesRow({ success: true }), true);
assert.strictEqual(helper.reviewActionClosesRow({ success: false, error: "conflict" }), false);
assert.strictEqual(
    helper.reviewActionClosesRow({
        success: true,
        decision: "insufficient_evidence",
        disposition_label: "No violation",
    }),
    false
);

console.log("review decision label checks passed");
