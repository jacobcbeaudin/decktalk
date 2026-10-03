# Releasing

How CI judges a change, and how a judged change becomes a release. [CONTRIBUTING.md](CONTRIBUTING.md)
covers setup, the checks and the commit rules.

## What CI runs

`.github/workflows/ci.yml` is the only workflow that runs a check. A `plan` job runs
`scripts/check.py --json --when <moment>`, and a `run` job takes its runner, its timeout and its one
command from each row. A generated matrix cannot be a required check, because a ruleset names a
check by literal string, so the workflow ends with a static job named `ci` that aggregates every leg.
**`ci` is the one required check on `main`**, and nothing pushes to `main` except a pull request it
judged.

`.github/workflows/release.yml` never runs a check. It starts from a completed run of `ci` and
releases only when that run was a green push on `main`, so what ships is what `ci` judged.

Every action is pinned to a commit SHA with its version in a comment, every workflow grants
`contents: read` at the top and more only per job, and `uvx zizmor .github/workflows` is part of the
`lint` group. The name `release.yml` and the environment name `pypi` are registered with PyPI's
trusted publisher, so neither may be renamed.

Timing gates everywhere by default. A leg on a hosted runner whose compositor presents frames late
passes `--timing=report`, which is the three `-platforms` rows and the weekly `scaffold` row. Such a
leg still measures and prints what it tolerated, and any finding that is not a late landing fails
it. `tests/support/timing_policy.py` holds the slack.

Coverage has one floor, measured on Linux and written by `uv run scripts/check_coverage.py --write`
rather than typed. The record only rises, and a run is held to it less a point of margin. Every
suite that measures has to leave a data file with something in it, so a leg that never ran fails by
name.

## How the version moves

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml`, `uv.lock` and `src/decktalk/runtime/src/index.ts`, writes `CHANGELOG.md`, and
picks the bump from the commits since the last release. That pull request runs `ci` like any other,
and a job in `ci.yml` runs `uv run scripts/check.py --group generated --write` on its branch and
commits every file that changed, so no bot ever writes to `main`. The `rehearsal` group runs the same
release path on every pull request first, and the docstring of `scripts/rehearse_release.py` says
what it bumps and what it refuses.

| Commit | Bump while the version is below 1.0 | Bump from 1.0 |
|---|---|---|
| `fix:` | patch | patch |
| `feat:` | minor | minor |
| A `BREAKING CHANGE:` footer | minor, because `bump-minor-pre-major` is set | major |

The wheel is `src/decktalk` alone, so `exclude-paths` in `release-please-config.json` lists `docs`,
`assets`, `scripts`, `tests` and `.github`, and a commit confined to those bumps nothing. The option
matches directory prefixes only, so a commit that edits a root file such as `README.md` counts in
full under its own type.

## Candidates and finals

Every release is a candidate until a person names the final one. The package in
`release-please-config.json` stays in the series for good, with `"versioning": "prerelease"`,
`"prerelease-type": "rc1"` and `"prerelease": true`, and nobody edits those settings to release.

| On `main` since the last release | release-please proposes |
|---|---|
| Any fix, feature or breaking change after `0.5.0-rc2` | `0.5.0-rc3` |
| A commit with a `Release-As: 0.5.0` footer | `0.5.0` |
| A feature after the final `0.5.0` | `0.6.0-rc1` |
| A fix after the final `0.5.0` | `0.5.1-rc1` |
| A feature after `0.5.1-rc1` | `0.6.0-rc1` |
| Only hidden types, such as `docs:` or `chore:` | nothing |

`tests/scripts/next_version.test.mjs` runs each row through release-please's own code.

A final release is one pull request. Its commit carries a `Release-As: 0.5.0` footer as the last line
of the message and changes a file outside every entry of `exclude-paths`, such as `README.md`. On
merge, the release workflow marks it the latest release and replaces its notes with the notes of the
whole series from `scripts/release_notes.py`.

The tag is semver and the package is PEP 440, so the tag is `v0.5.0-rc2`, the wheel is
`decktalk-0.5.0rc2-py3-none-any.whl` and `uv version --short` prints `0.5.0rc2`.
`tests/contract/test_release_versions.py` accepts both spellings as long as every file names one
version. PyPI excludes a prerelease from plain resolution, so to install a candidate, name it:
`DECKTALK_VERSION=0.5.0rc2 sh install.sh`.

## To release

1. Merge the release pull request. release-please creates the tag and the GitHub release.
2. Wait for the release workflow. It checks that the tag and the packaged version agree, builds the
   wheel from the tag and opens it in an isolated environment.
3. Approve the `pypi` environment. A PyPI version can never be uploaded again, so this is the one
   step with a reviewer.
4. Check PyPI. The workflow publishes through trusted publishing and attaches the files to the
   GitHub release.
