/*! The spoken words a page is handed, with the voice and the turn that say each one, tested without a browser. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { parseWords } from "../../../../src/decktalk/runtime/src/text.ts";

test("every word carries the voice and the turn whose second it falls at or after", () => {
  const words = parseWords("Alpha@0.50,beta@1.00,gamma@3.10,delta@3.40", "narrator@0.40,guest@3.10");
  assert.deepEqual(words, [
    { key: "alpha", at: 0.5, voice: "narrator", turn: 1 },
    { key: "beta", at: 1.0, voice: "narrator", turn: 1 },
    { key: "gamma", at: 3.1, voice: "guest", turn: 2 },
    { key: "delta", at: 3.4, voice: "guest", turn: 2 },
  ]);
});

test("a turn that says no word keeps the turns after it numbered as the section numbers them", () => {
  const words = parseWords("alpha@0.50,omega@6.00", "narrator@0.40,guest@3.10,narrator@5.90");
  assert.deepEqual(
    words.map((word) => [word.voice, word.turn]),
    [
      ["narrator", 1],
      ["narrator", 3],
    ],
  );
});

test("words handed no turns carry no voice and no turn", () => {
  assert.deepEqual(parseWords("alpha@0.50", null), [{ key: "alpha", at: 0.5, voice: null, turn: null }]);
});
