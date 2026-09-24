/*! The narration clock and its queue, tested without a browser.
 *
 * Everything the clock owns but the frame loop is arithmetic over a sorted list, so `node --test`
 * reads the module directly. The loop itself belongs to a browser and is held by the browser tests
 * that watch a cue fire at its own second.
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { clear, now, pending, round, schedule, start, started } from "../../../../src/decktalk/runtime/src/clock.ts";

beforeEach(() => {
  clear();
});

test("a page has no narration second until its clock has started", () => {
  assert.equal(started(), false);
  assert.equal(now(), Number.NEGATIVE_INFINITY);
});

test("a second is carried at the precision every report and every log row uses", () => {
  assert.equal(round(1.23456), 1.235);
  assert.equal(round(2), 2);
});

test("the queue runs in order of the second each task is due", () => {
  schedule(3, "cue", "c", () => {});
  schedule(1, "cue", "a", () => {});
  schedule(2, "cue", "b", () => {});
  assert.deepEqual(
    pending().map((task) => task.id),
    ["a", "b", "c"],
  );
});

test("a mount comes before anything else due at the same second", () => {
  schedule(1, "cue", "cue", () => {});
  schedule(1, "reveal", "reveal", () => {});
  schedule(1, "mount", "mount", () => {});
  assert.deepEqual(
    pending().map((task) => task.id),
    ["mount", "cue", "reveal"],
  );
});

test("starting the clock twice keeps the origin every fired cue was measured against", () => {
  start();
  assert.equal(started(), true);
  const first = now();
  start();
  assert.ok(now() >= first);
});
