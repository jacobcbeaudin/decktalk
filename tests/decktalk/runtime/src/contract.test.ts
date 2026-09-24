/*! The readers of the page contract, tested without a browser and without a bundle.
 *
 * Everything in `contract.ts` is data and arithmetic over data, so `node --test` reads the module
 * directly and every rule the contract rests on is a plain assertion here. The rules the Python side
 * needs as well, which are the closed exemption list and the span ceiling, are asserted again in
 * `tests/decktalk/test_page.py` against the generated module, because a rule held in one language
 * and generated into another is a rule that can be lost in the generator.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ATTRS,
  type Attr,
  CAPTURE_FPS,
  CODES,
  EXEMPT,
  ENTRANCES,
  FRAME_STEP_MS,
  MEASURABLE_SPAN_SECONDS,
  MOMENTS,
  MOMENT_SELECTOR,
  known,
  measurable,
  message,
  pairs,
  refuse,
  scaled,
  staggerSpan,
  wireId,
} from "../../../../src/decktalk/runtime/src/contract.ts";

test("the frame step is the capture rate written the other way round", () => {
  assert.equal(1000 / CAPTURE_FPS, FRAME_STEP_MS);
});

test("a local moment qualifies into the id cues.json carries", () => {
  assert.equal(wireId("4.1", "expand"), "4.1:expand");
});

test("a pair list keeps whole pairs and drops half written ones", () => {
  assert.deepEqual(pairs("cancel:stale|result:live"), [
    { moment: "cancel", value: "stale" },
    { moment: "result", value: "live" },
  ]);
  assert.deepEqual(pairs(" cancel : the h is struck out "), [{ moment: "cancel", value: "the h is struck out" }]);
  assert.deepEqual(pairs("cancel|:live|result:"), []);
});

test("a pair's value may hold the mark that opened it", () => {
  assert.deepEqual(pairs("cancel:a ratio of 3:4"), [{ moment: "cancel", value: "a ratio of 3:4" }]);
});

test("a message is filled from what the caller knows and keeps what it does not", () => {
  const filled = message("PAGE_BAD_VALUE", { attr: "data-in-style", value: "drop", allowed: "rise, pop" });
  assert.equal(filled, "data-in-style=drop is not one of rise, pop, so write one of those instead.");
  assert.match(message("PAGE_BAD_VALUE", { attr: "data-count" }), /\{value\}/);
});

test("an attribute is known only when the registry defines it", () => {
  assert.equal(known("data-in"), true);
  assert.equal(known("data-inn"), false);
  assert.equal(known("data-reveal"), false);
});

test("a closed word set refuses every word outside it", () => {
  assert.equal(refuse("data-in-style", "pop"), null);
  assert.match(String(refuse("data-in-style", "drop")), /one of rise, settle, fade, pop, draw, cut/);
});

test("a ranged attribute refuses a value off the range or off the step", () => {
  assert.equal(refuse("data-in-seconds", "0.4"), null);
  assert.equal(refuse("data-in-seconds", "0.12"), null);
  assert.match(String(refuse("data-in-seconds", "0.5")), /0.12 to 0.48 seconds/);
  assert.match(String(refuse("data-in-seconds", "0.3")), /a multiple of 0.04 seconds/);
  assert.match(String(refuse("data-in-seconds", "quick")), /a number of seconds/);
});

test("an attribute whose value is the author's words takes any non-empty one", () => {
  assert.equal(refuse("data-describe", "the definition of the derivative"), null);
  assert.match(String(refuse("data-describe", "  ")), /a value/);
});

test("a stagger's whole span is its step per earlier child plus one entrance", () => {
  assert.equal(staggerSpan(0.08, 4, ENTRANCES.rise.seconds), 0.08 * 3 + 0.32);
  assert.equal(staggerSpan(0.08, 0, ENTRANCES.rise.seconds), 0);
  assert.equal(measurable(staggerSpan(0.08, 3, ENTRANCES.pop.seconds)), true);
  assert.equal(measurable(staggerSpan(0.2, 4, ENTRANCES.draw.seconds)), false);
});

test("a reduced render never scales a span past the ceiling", () => {
  assert.equal(scaled(0.2, 1.5), 0.30000000000000004);
  assert.equal(measurable(scaled(ENTRANCES.draw.seconds, 4)), true);
});

test("every attribute either carries a code or is named in the closed exemption list", () => {
  for (const [name, row] of Object.entries(ATTRS)) {
    const exempt = Object.hasOwn(EXEMPT, name);
    assert.equal(row.code === null, exempt, `${name} must carry a code or be exempt, and never both`);
    if (row.code) assert.ok(Object.hasOwn(CODES, row.code), `${name} names a code the registry does not define`);
  }
  assert.equal(Object.keys(EXEMPT).length, 5);
  for (const name of Object.keys(EXEMPT)) assert.ok(known(name), `${name} is exempt but is not an attribute`);
});

test("no span an author can declare reaches the ceiling that makes a cue unmeasurable", () => {
  for (const [name, row] of Object.entries(ATTRS)) {
    if (row.span !== null) assert.ok(measurable(row.span), `${name} declares a span at or above the ceiling`);
    if (row.range && row.range.unit === "seconds" && name !== "data-hold") {
      assert.ok(measurable(row.range.max), `${name} publishes a range whose top is unmeasurable`);
    }
  }
  for (const entrance of Object.values(ENTRANCES)) assert.ok(measurable(entrance.seconds));
  assert.ok(MEASURABLE_SPAN_SECONDS > FRAME_STEP_MS / 1000);
});

test("the moment selector is built from the registry and never typed out", () => {
  assert.deepEqual(MOMENTS, ["data-in", "data-back", "data-front", "data-out"] as Attr[]);
  assert.equal(MOMENT_SELECTOR, "[data-in],[data-back],[data-front],[data-out]");
});

test("every word set publishes a default that is one of its own words", () => {
  for (const [name, row] of Object.entries(ATTRS)) {
    if (row.default === null || row.values.length === 0) continue;
    assert.ok(row.values.includes(row.default as never), `${name} defaults to a word it does not admit`);
  }
});
