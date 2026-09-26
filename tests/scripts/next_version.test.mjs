// The version release-please proposes in each situation of the candidate cycle, computed by
// scripts/next_version.mjs with the repository's own config. One test per row of the table in
// CONTRIBUTING.md under "Releases".

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { nextRelease } from "../../scripts/next_version.mjs";

const config = JSON.parse(readFileSync(new URL("../../release-please-config.json", import.meta.url), "utf8"));

let count = 0;
function commit(message, files = ["src/decktalk/cli/app.py"]) {
  count += 1;
  return { sha: `c${count}`.padEnd(40, "0"), message, files };
}

async function next(released, ...commits) {
  return nextRelease({ released, commits, config });
}

test("a fix on a candidate is the next candidate", async () => {
  assert.equal((await next("0.5.0-rc2", commit("fix: a fix"))).next, "0.5.0-rc3");
});

test("a feature on a candidate is the next candidate", async () => {
  assert.equal((await next("0.5.0-rc2", commit("feat: a feature"))).next, "0.5.0-rc3");
});

test("a breaking change on a candidate below 1.0 is the next candidate", async () => {
  assert.equal((await next("0.5.0-rc2", commit("feat!: a break"))).next, "0.5.0-rc3");
});

test("a Release-As footer graduates the series to the version it names", async () => {
  const result = await next("0.5.0-rc2", commit("fix: the last fix\n\nRelease-As: 0.5.0"));
  assert.equal(result.next, "0.5.0");
  assert.equal(result.named, "0.5.0");
});

test("a feature after a final release starts a numbered minor series", async () => {
  assert.equal((await next("0.5.0", commit("feat: a feature"))).next, "0.6.0-rc1");
});

test("a fix after a final release starts a numbered patch series", async () => {
  assert.equal((await next("0.5.0", commit("fix: a fix"))).next, "0.5.1-rc1");
});

test("a feature during a patch series moves the series to the next minor", async () => {
  assert.equal((await next("0.5.1-rc1", commit("feat: a feature"))).next, "0.6.0-rc1");
});

test("commits of hidden types open no release", async () => {
  const result = await next("0.5.0-rc2", commit("docs: a page"), commit("chore: a tidy"));
  assert.equal(result.next, null);
  assert.equal(result.rehearse, "0.5.0-rc3");
});

test("a Release-As footer on a docs commit that touches an included file opens the release it names", async () => {
  const result = await next("0.5.0-rc2", commit("docs(readme): the release\n\nRelease-As: 0.5.0", ["README.md"]));
  assert.equal(result.next, "0.5.0");
  assert.equal(result.named, "0.5.0");
});

test("a Release-As footer on a commit that touches only excluded paths is dropped", async () => {
  const result = await next("0.5.0-rc2", commit("fix: a page\n\nRelease-As: 0.5.0", ["docs/index.mdx"]));
  assert.deepEqual(
    result.dropped.map((d) => d.version),
    ["0.5.0"],
  );
  assert.equal(result.named, null);
});

test("a Release-As footer on an empty commit is dropped", async () => {
  const result = await next("0.5.0-rc2", commit("chore: release 0.5.0\n\nRelease-As: 0.5.0", []));
  assert.equal(result.dropped.length, 1);
});

test("the notes carry the commits release-please would list", async () => {
  const result = await next("0.5.0-rc2", commit("fix(cue): match a phrase over two lines"));
  assert.match(result.notes, /### Bug Fixes/);
  assert.match(result.notes, /match a phrase over two lines/);
});
