/*! The names the markup reader uses, held against the registry that owns them.
 *
 * No module outside the contract may spell an attribute, so every name the runtime reads is built
 * from the prefix the contract publishes and the word the row is called. Nothing type checks that
 * arithmetic, so this file is what does: every name is a row the registry defines, and every row the
 * registry defines is a name the runtime has.
 *
 * The rest of the module reads markup, so it is held by the browser tests against the shipped bundle.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ATTRS, known } from "../../../../src/decktalk/runtime/src/contract.ts";
import { ATTR } from "../../../../src/decktalk/runtime/src/scene.ts";

test("every name the runtime reads is a row the contract defines", () => {
  for (const [field, name] of Object.entries(ATTR)) {
    assert.ok(known(name), `${field} is spelled ${name}, which the registry does not define`);
  }
});

test("every row the contract defines is a name the runtime has", () => {
  const read = new Set<string>(Object.values(ATTR));
  for (const name of Object.keys(ATTRS)) {
    assert.ok(read.has(name), `${name} is in the registry and no runtime module reads it`);
  }
});

test("no two fields name the same attribute", () => {
  assert.equal(new Set(Object.values(ATTR)).size, Object.values(ATTR).length);
});
