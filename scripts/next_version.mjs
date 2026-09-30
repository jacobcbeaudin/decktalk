// The version release-please would propose next, computed by release-please's own code.
//
//     node scripts/next_version.mjs                    # print what the next release pull request would carry, as JSON
//     node scripts/next_version.mjs --bump DIR PATH... # write that version into the copy at DIR, as release-please would
//
// release-please reads the history through the GitHub API, so what it will do is otherwise known
// only once its pull request opens. This reads the same history from git and hands it to the same
// classes release-please uses: its commit parser, its exclude-paths filter, its prerelease
// versioning strategy and its changelog writer. `release-please` is a development dependency pinned
// to the version the release workflow's action bundles, so the two cannot disagree about the rules.
//
// It needs the history back to the last release tag, so a shallow clone fails with the command
// that fixes it. It reads nothing from the network.
//
// `--bump` makes the edits release-please's python release makes, with release-please's own updaters:
// the project file, the changelog, every entry of `extra-files` by the updater its `type` names, and
// the manifest. It refuses a file that is missing or that the bump would leave as it was, because a
// rehearsal that silently skipped a file would pass for the wrong reason.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { parseConventionalCommits } = require("release-please/build/src/commit.js");
const { CommitExclude } = require("release-please/build/src/util/commit-exclude.js");
const { PrereleaseVersioningStrategy } = require("release-please/build/src/versioning-strategies/prerelease.js");
const { DefaultVersioningStrategy } = require("release-please/build/src/versioning-strategies/default.js");
const { DefaultChangelogNotes } = require("release-please/build/src/changelog-notes/default.js");
const { Version } = require("release-please/build/src/version.js");
const { setLogger } = require("release-please/build/src/util/logger.js");
const { Changelog } = require("release-please/build/src/updaters/changelog.js");
const { Generic } = require("release-please/build/src/updaters/generic.js");
const { GenericJson } = require("release-please/build/src/updaters/generic-json.js");
const { GenericToml } = require("release-please/build/src/updaters/generic-toml.js");
const { PyProjectToml } = require("release-please/build/src/updaters/python/pyproject-toml.js");
const { ReleasePleaseManifest } = require("release-please/build/src/updaters/release-please-manifest.js");

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
// release-please's warnings and errors reach stderr, where the rehearsal prints them, because an
// updater that found no version to move says why only there. Its progress lines stay quiet.
const say = (...parts) => console.error(...parts);
const LOGGER = { error: say, warn: say, info() {}, debug() {}, trace() {} };
setLogger(LOGGER);

/** The commit a rehearsal pretends landed when nothing releasable has, which is one fix. */
export const REHEARSAL_COMMIT = { sha: "rehearsal", message: "fix: rehearse the release path", files: [] };

/** One package's settings, the package's own over the config's top level, as release-please merges them. */
export function packageConfig(config, path) {
  const { packages, ...top } = config;
  return { ...top, ...packages[path] };
}

function strategy(settings) {
  const options = {
    bumpMinorPreMajor: settings["bump-minor-pre-major"] === true,
    bumpPatchForMinorPreMajor: settings["bump-patch-for-minor-pre-major"] === true,
    prereleaseType: settings["prerelease-type"],
    prerelease: settings.prerelease === true,
    logger: LOGGER,
  };
  const versioning = settings.versioning ?? "default";
  if (versioning === "prerelease") return new PrereleaseVersioningStrategy(options);
  if (versioning === "default") return new DefaultVersioningStrategy(options);
  throw new Error(`the versioning ${versioning} is not one this tool knows, so teach it before relying on it`);
}

function releaseAs(commit) {
  return commit.notes.find((note) => note.title === "RELEASE AS")?.text;
}

/**
 * What release-please would do with these commits, newest first, since the release `released`.
 *
 * `next` is null when release-please would open no pull request, which is when no commit survives
 * the path filter or when every one that does is of a type the changelog hides. `named` is the
 * version a Release-As footer asked for, and `dropped` lists the footers release-please would never
 * read, because their commit touched only excluded paths.
 */
export async function nextRelease({ released, commits, config, path = ".", owner, repository }) {
  const settings = packageConfig(config, path);
  const current = Version.parse(released);
  const parsed = parseConventionalCommits(commits);
  const exclude = new CommitExclude({ [path]: { excludePaths: settings["exclude-paths"] } });
  const kept = exclude.excludeCommits({ [path]: parsed })[path];
  const keptShas = new Set(kept.map((commit) => commit.sha));
  const dropped = parsed
    .filter((commit) => releaseAs(commit) && !keptShas.has(commit.sha))
    .map((commit) => ({ sha: commit.sha, version: releaseAs(commit) }));
  const named = kept.map(releaseAs).find(Boolean) ?? null;
  const versioning = strategy(settings);
  const notes = new DefaultChangelogNotes();
  const build = async (list) => {
    const version = versioning.bump(current, list).toString();
    const body = await notes.buildNotes(list, {
      version,
      previousTag: `v${released}`,
      currentTag: `v${version}`,
      changelogSections: settings["changelog-sections"],
      owner,
      repository,
    });
    return { version, body };
  };
  let next = null;
  if (kept.length > 0) {
    const built = await build(kept);
    // A body of one line is a heading with nothing under it, which release-please skips.
    if (built.body.split("\n").length > 1) next = built;
  }
  const rehearse = next ?? (await build(parseConventionalCommits([REHEARSAL_COMMIT])));
  return {
    released,
    next: next?.version ?? null,
    named,
    dropped,
    notes: next?.body ?? null,
    rehearse: rehearse.version,
    rehearseNotes: rehearse.body,
  };
}

/** The updater release-please runs on one entry of `extra-files`, chosen by the `type` the entry names. */
function extraFile(entry, version) {
  const updaters = {
    generic: () => new Generic({ version }),
    json: () => new GenericJson(entry.jsonpath, version),
    toml: () => new GenericToml(entry.jsonpath, version),
  };
  const make = updaters[entry.type];
  if (!make) throw new Error(`the extra file ${JSON.stringify(entry)} names no type among generic, json and toml`);
  return [entry.path, make()];
}

/** Every file release-please rewrites in one python package, each with the updater it rewrites it by. */
export function updates(settings, version, notes) {
  if (settings["release-type"] !== "python") {
    throw new Error(`a ${settings["release-type"]} release is not one this rehearsal knows, so teach it first`);
  }
  return [
    ["pyproject.toml", new PyProjectToml({ version })],
    [settings["changelog-path"] ?? "CHANGELOG.md", new Changelog({ version, changelogEntry: notes })],
    ...(settings["extra-files"] ?? []).map((entry) => extraFile(entry, version)),
  ];
}

/** One file rewritten by its updater, refused when it is missing or when the updater leaves it as it was. */
export function rewrite(dir, path, updater) {
  const file = join(dir, path);
  if (!existsSync(file)) throw new Error(`${path} does not exist, and release-please would bump it`);
  const before = readFileSync(file, "utf8");
  const after = updater.updateContent(before, LOGGER);
  if (after === before)
    throw new Error(`the bump changed nothing in ${path}, so release-please would leave its version behind`);
  writeFileSync(file, after);
  process.stdout.write(`bumped ${path}\n`);
}

function git(...args) {
  return execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function hasTag(tag) {
  try {
    git("rev-parse", "--verify", "--quiet", `refs/tags/${tag}`);
    return true;
  } catch {
    return false;
  }
}

/** The last released version: the manifest's when its tag exists, else the newest release tag behind HEAD. */
function lastReleased(tree) {
  if (hasTag(`v${tree}`)) return tree;
  try {
    return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*").trim().replace(/^v/, "");
  } catch {
    throw new Error(
      "no release tag is reachable from HEAD. Fetch the history and the tags: git fetch --tags --unshallow",
    );
  }
}

/** The owner and name of the repository `origin` points at, which the changelog links into. */
function origin() {
  const url = git("remote", "get-url", "origin").trim();
  const found = url.match(/github\.com[:/](?<owner>[^/]+)\/(?<repository>[^/]+?)(?:\.git)?$/);
  return found ? { ...found.groups } : {};
}

/** Every commit since `tag`, newest first, with the files it touched, which is what release-please reads. */
function commitsSince(tag) {
  const log = git("log", "--no-merges", "--name-only", "--format=%x1e%H%x1f%B%x1f", `${tag}..HEAD`);
  return log
    .split("\x1e")
    .filter(Boolean)
    .map((record) => {
      const [sha, message, files] = record.split("\x1f");
      return { sha, message: message.trim(), files: files.split("\n").filter(Boolean) };
    });
}

async function main([flag, dir, ...bumped]) {
  const config = JSON.parse(readFileSync(join(ROOT, "release-please-config.json"), "utf8"));
  const manifest = JSON.parse(readFileSync(join(ROOT, ".release-please-manifest.json"), "utf8"));
  const report = {};
  for (const path of Object.keys(config.packages)) {
    const tree = manifest[path];
    const released = lastReleased(tree);
    const commits = commitsSince(`v${released}`);
    const result = await nextRelease({ released, commits, config, path, ...origin() });
    report[path] = { tree, ...result };
  }
  if (flag !== "--bump") {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
    return;
  }
  const versions = new Map(bumped.map((path) => [path, Version.parse(report[path].rehearse)]));
  for (const [path, version] of versions) {
    for (const [file, updater] of updates(packageConfig(config, path), version, report[path].rehearseNotes)) {
      rewrite(join(dir, path), file, updater);
    }
  }
  rewrite(dir, ".release-please-manifest.json", new ReleasePleaseManifest({ versionsMap: versions }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exit(1);
  });
}
