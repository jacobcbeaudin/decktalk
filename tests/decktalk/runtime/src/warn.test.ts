/*! Everything the page could not honour, tested as the five fields the contract publishes.
 *
 * A warning is a row and nothing else, so nothing here needs a browser. What the browser tests hold
 * is which code is raised where, and what this file holds is that a row says the same thing however
 * it is raised and that a page never reports the same thing twice.
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { CODES } from "../../../../src/decktalk/runtime/src/contract.ts";
import { forget, warn, warnings } from "../../../../src/decktalk/runtime/src/warn.ts";

/** The console echo is the page's, not the test's, so it is swallowed for the length of this file. */
console.warn = () => {};

beforeEach(() => {
  forget();
});

test("a row carries the code, the sentence and the place, which is the whole wire shape", () => {
  warn("PAGE_SLIDE_UNUSED", "1.2");
  assert.deepEqual(warnings(), [
    {
      code: "PAGE_SLIDE_UNUSED",
      message: CODES.PAGE_SLIDE_UNUSED.message.replace("{slide}", "1.2"),
      slide: "1.2",
      cue: null,
      attr: null,
    },
  ]);
});

test("the sentence is filled from what the caller knows about the page", () => {
  warn("PAGE_BAD_VALUE", "1.1", null, { attr: "data-in-style", value: "zoom", allowed: "one of rise, fade" });
  const row = warnings()[0];
  assert.ok(row);
  assert.equal(row.message, "data-in-style=zoom is not one of one of rise, fade, so write one of those instead.");
  assert.equal(row.attr, "data-in-style");
});

test("a field the caller does not know is left as the template wrote it", () => {
  warn("PAGE_SWAP_AMBIGUOUS", "1.1", "1.1:swap");
  assert.match(warnings()[0]?.message ?? "", /\{value\}/);
});

test("the same thing about the same place is reported once", () => {
  warn("PAGE_SLIDE_UNUSED", "1.2");
  warn("PAGE_SLIDE_UNUSED", "1.2");
  assert.equal(warnings().length, 1);
});

test("the same code about two places is two rows", () => {
  warn("PAGE_SLIDE_UNUSED", "1.2");
  warn("PAGE_SLIDE_UNUSED", "1.3");
  assert.equal(warnings().length, 2);
});
