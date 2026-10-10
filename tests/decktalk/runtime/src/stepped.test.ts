/*! The section clock under the stepped driver, which is how the recorder moves a page, tested without a browser.
 *
 * Each test file runs in a process of its own, and the driver is decided for the life of a page, so
 * this file holds the stepped driver and `clock.test.ts` holds the realtime one.
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  clear,
  driven,
  every,
  frameAt,
  now,
  observe,
  pending,
  schedule,
  start,
  started,
  stepTo,
} from "../../../../src/decktalk/runtime/src/clock.ts";

beforeEach(() => {
  clear();
});

test("the first step starts the clock at zero under the stepped driver", () => {
  assert.equal(started(), false);
  stepTo(0);
  assert.equal(started(), true);
  assert.equal(driven(), "stepped");
  assert.equal(now(), 0);
});

test("a cue due at a second runs on the step at that second and not on the step before it", () => {
  const ran: number[] = [];
  schedule(1.0, "cue", "a", () => ran.push(now()));
  stepTo(0.96);
  assert.deepEqual(ran, []);
  stepTo(1.0);
  assert.deepEqual(ran, [1.0]);
  assert.equal(frameAt(), 1.0);
});

test("a cue due exactly on a frame runs on that frame however the frame's second was divided", () => {
  const ran: string[] = [];
  schedule(0.7, "cue", "on-the-frame", () => ran.push("on-the-frame"));
  stepTo(21 * (1 / 30));
  assert.deepEqual(ran, ["on-the-frame"]);
});

test("a step runs every task it passed, in the order each was due", () => {
  const ran: string[] = [];
  schedule(1.1, "cue", "b", () => ran.push("b"));
  schedule(1.05, "cue", "a", () => ran.push("a"));
  stepTo(1.2);
  assert.deepEqual(ran, ["a", "b"]);
  assert.equal(pending().length, 0);
});

test("every hook is called once a step with the stepped second, until it says it has finished", () => {
  const seen: number[] = [];
  every((seconds) => {
    seen.push(seconds);
    return seconds < 2.08;
  });
  for (const seconds of [2.0, 2.04, 2.08, 2.12]) stepTo(seconds);
  assert.deepEqual(seen, [2.0, 2.04, 2.08]);
});

test("a person's start does not move a clock the recorder is stepping", () => {
  stepTo(3.0);
  start();
  assert.equal(driven(), "stepped");
  assert.equal(now(), 3.0);
});

test("a step answers the second the next task is due, so the recorder can stop there first", () => {
  schedule(5.02, "cue", "next", () => {});
  assert.equal(stepTo(5.0).due, 5.02);
  assert.equal(stepTo(5.04).due, null);
});

test("a step with no task to run, no hook and nothing moving answers that nothing changed", () => {
  stepTo(6.0);
  assert.equal(stepTo(6.04).changed, false);
  assert.equal(stepTo(6.08).changed, false);
});

test("a step that runs a task answers that the page changed, and the step after it does not", () => {
  stepTo(7.0);
  schedule(7.02, "cue", "a", () => {});
  assert.equal(stepTo(7.04).changed, true);
  assert.equal(stepTo(7.08).changed, false);
});

test("a step answers that the page changed for as long as a frame hook draws, and not once it has finished", () => {
  stepTo(8.0);
  every((seconds) => seconds < 8.08);
  assert.equal(stepTo(8.04).changed, true);
  assert.equal(stepTo(8.08).changed, true, "the hook drew the step it finished on");
  assert.equal(stepTo(8.12).changed, false);
});

test("a step answers that the page changed when what hears the clock says what it holds moved", () => {
  stepTo(9.0);
  let moving = true;
  observe({ running: () => {}, ran: () => {}, stepped: () => moving, still: () => null });
  assert.equal(stepTo(9.04).changed, true);
  moving = false;
  assert.equal(stepTo(9.08).changed, false);
  observe(null);
});

test("a step answers that nothing can change before the next task is due", () => {
  stepTo(10.0);
  schedule(12.5, "cue", "later", () => {});
  assert.equal(stepTo(10.04).stillUntil, 12.5);
  stepTo(12.5);
  assert.equal(stepTo(12.54).stillUntil, null, "nothing is owed and nothing moves");
});

test("a step answers no still stretch while a frame hook draws, and one once it has finished", () => {
  stepTo(13.0);
  every((seconds) => seconds < 13.08);
  assert.equal(stepTo(13.04).stillUntil, 13.04);
  assert.equal(stepTo(13.08).stillUntil, null, "the hook finished on this step");
});

test("a step answers the earliest second what hears the clock says it next moves at", () => {
  stepTo(14.0);
  schedule(16.0, "cue", "later", () => {});
  observe({ running: () => {}, ran: () => {}, stepped: () => false, still: () => 15.0 });
  assert.equal(stepTo(14.04).stillUntil, 15.0);
  observe({ running: () => {}, ran: () => {}, stepped: () => false, still: () => null });
  assert.equal(stepTo(14.08).stillUntil, 16.0);
  observe(null);
});
