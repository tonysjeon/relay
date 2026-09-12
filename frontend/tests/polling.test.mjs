import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { startPolling } from "../lib/polling.ts";

test("refreshes every two seconds without overlapping slow or manual requests", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let calls = 0;
  let finish;
  const poll = startPolling(() => {
    calls++;
    return new Promise((resolve) => {
      finish = resolve;
    });
  }, assert.fail);
  t.after(() => poll.stop());
  assert.equal(calls, 1);
  t.mock.timers.tick(6000);
  poll.refresh();
  assert.equal(calls, 1);
  finish();
  await setImmediate();
  t.mock.timers.tick(2000);
  assert.equal(calls, 2);
  finish();
});

test("hidden pages skip requests and refresh on demand when visible again", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let visible = false;
  let calls = 0;
  const poll = startPolling(
    async () => {
      calls++;
    },
    assert.fail,
    () => visible,
  );
  t.after(() => poll.stop());
  t.mock.timers.tick(4000);
  assert.equal(calls, 0);
  visible = true;
  poll.refresh();
  await setImmediate();
  assert.equal(calls, 1);
  t.mock.timers.tick(2000);
  assert.equal(calls, 2);
});

test("an error is reported and the next scheduled request can recover", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let calls = 0;
  const errors = [];
  const poll = startPolling(
    async () => {
      if (++calls === 1) throw new Error("offline");
    },
    (error) => errors.push(error.message),
  );
  t.after(() => poll.stop());
  await setImmediate();
  assert.deepEqual(errors, ["offline"]);
  t.mock.timers.tick(2000);
  await setImmediate();
  assert.equal(calls, 2);
  assert.equal(errors.length, 1);
});

test("cleanup aborts the active request, ignores its failure, and stops future ticks", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let calls = 0;
  let signal;
  let reject;
  const poll = startPolling((s) => {
    calls++;
    signal = s;
    return new Promise((_, fail) => {
      reject = fail;
    });
  }, assert.fail);
  poll.stop();
  assert.equal(signal.aborted, true);
  reject(new Error("aborted"));
  await setImmediate();
  t.mock.timers.tick(10000);
  poll.refresh();
  assert.equal(calls, 1);
});
