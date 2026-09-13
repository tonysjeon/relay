import test from "node:test";
import assert from "node:assert/strict";
import { cost, tokenTotal, usageCost } from "../lib/usage.ts";

test("small costs stay visible and missing cost stays unknown", () => {
  assert.equal(cost("0.0000476"), "$0.0000476");
  assert.equal(cost("0"), "$0.00");
  assert.equal(cost(null), "Unknown");
  assert.equal(cost("0.000000001"), "<$0.00000001");
});
test("totals distinguish no calls, unknown and partial usage", () => {
  const usage = {calls: 2, input_tokens: 19, output_tokens: 25, missing_token_calls: 1, estimated_cost_usd: "0.0000476", unpriced_calls: 1};
  assert.equal(tokenTotal(usage), "44 + unknown");
  assert.equal(usageCost(usage), "$0.0000476 + unknown");
  assert.equal(usageCost({...usage, unpriced_calls: 2}), "Unknown");
  assert.equal(tokenTotal({...usage, calls: 0}), "—");
});
