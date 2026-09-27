// The version release-please proposes in each situation of the candidate cycle, computed by
// scripts/next_version.mjs with the repository's own config. One test per row of the table in
// CONTRIBUTING.md under "Releases".

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { nextRelease, packageConfig, rewrite, updates } from "../../scripts/next_version.mjs";

const { Version } = createRequire(import.meta.url)("release-please/build/src/version.js");

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

// The bump a rehearsal makes is release-please's own updaters, chosen from the same config.

const VERSION = Version.parse("9.9.9-rc1");

test("every file the config names takes the version", () => {
  for (const [path, updater] of updates(packageConfig(config, "."), VERSION, "## [9.9.9-rc1] (2026-01-01)\n")) {
    const before = readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
    const after = updater.updateContent(before);
    assert.notEqual(after, before, `${path} kept its version`);
    assert.match(after, /9\.9\.9-rc1/, path);
  }
});

test("an extra file of a type the rehearsal does not know is refused", () => {
  const settings = { "release-type": "python", "extra-files": [{ type: "yaml", path: "a.yml", jsonpath: "$.v" }] };
  assert.throws(() => updates(settings, VERSION, ""), /names no type/);
});

test("a file the bump would leave as it was is refused", () => {
  const dir = mkdtempSync(join(tmpdir(), "bump-"));
  writeFileSync(join(dir, "index.ts"), 'const VERSION = "0.4.1";\n');
  const [, generic] = updates(
    { "release-type": "python", "extra-files": [{ type: "generic", path: "index.ts" }] },
    VERSION,
    "",
  )[2];
  assert.throws(() => rewrite(dir, "index.ts", generic), /changed nothing in index.ts/);
});

test("a file the config names that does not exist is refused", () => {
  const dir = mkdtempSync(join(tmpdir(), "bump-"));
  assert.throws(
    () => rewrite(dir, "gone.ts", updates(packageConfig(config, "."), VERSION, "")[0][1]),
    /does not exist/,
  );
});
