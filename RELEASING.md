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

A release goes to two places. PyPI gets the wheel and the sdist of every release. DeckTalk's conda
channel, which `install.sh` and `pixi global install` read, gets the noarch conda package of every
final release. Both are built from the tag by rows of `scripts/check.py` that every pull request
runs: `wheel` builds and installs the wheel, and `conda` builds the package with `pixi publish` and
installs it with `pixi global install` into an empty Pixi home. Nothing is uploaded until both
builds passed. Then a final release goes to the conda channel first and to PyPI second, because
prefix.dev can delete a package and PyPI can never take a version back, so an upload that fails
stops the release before the one that has no undo. The files are attached to the GitHub release
after both uploads.

## The conda channel

<!-- install-docs:channel:start -->
The channel is `jcbcodes/decktalk`. Its URL, which `install.sh` and `pixi global install` read, is `https://prefix.dev/jcbcodes/decktalk`, and its page is `https://prefix.dev/channels/@jcbcodes/decktalk`.
<!-- install-docs:channel:end -->

The upload uses trusted publishing, as PyPI does: the `publish-conda` job holds a GitHub OIDC
token, prefix.dev exchanges it for a short upload token, and no prefix.dev key is stored anywhere.
The upload carries a Sigstore attestation of the package, signed with the same identity. The job
runs in the GitHub environment `conda`, whose required reviewer approves each upload, and the
trusted publisher names that environment, so no other job can upload. `--skip-existing` makes a run
again after a partial upload pass over the file that already arrived, and the PyPI upload does the
same.

The channel names its trusted publisher once. On the channel page, open Settings, then Repository
Access, then Add access, and fill in:

| Field | Value |
|---|---|
| Source | GitHub |
| Owner | `jacobcbeaudin` |
| Repository | `decktalk` |
| Workflow file | `release.yml` |
| Environment | `conda` |
| Access | Read/write |
| Upload access | All packages |

## One-time setup

- On PyPI, under your account, Publishing, add a pending publisher: project `decktalk`, owner
  `jacobcbeaudin`, repository `decktalk`, workflow `release.yml`, environment `pypi`.
- On GitHub, under Settings, Environments, make the environment `pypi` and the environment `conda`,
  each with Jacob Beaudin as its required reviewer. A PyPI version can never be uploaded again, and
  a channel upload reaches every user of `pixi global install`.
- On GitHub, under Settings, Actions, General, Workflow permissions, allow GitHub Actions to create
  and approve pull requests.
- On prefix.dev, the trusted publisher above.

Every action is pinned to a commit SHA with its version in a comment, every workflow grants
`contents: read` at the top and more only per job, and the `zizmor` hook, at the version pixi.lock
pins, audits every workflow and the local action at every commit and in the `lint` group. The name
`release.yml` and the environment names `pypi` and `conda` are registered with the two trusted
publishers, so none of them may be renamed.

Every leg holds a reveal to its own frame, the hosted macOS and Windows rows and the weekly
`scaffold` row as much as the Linux ones, because the recorder steps the section clock and a slower
runner records the same frames. `tests/support/timing_policy.py` holds that rule and the time budgets.

Coverage has one floor, measured on Linux and written by `pixi run python scripts/check_coverage.py --write`
rather than typed. The record only rises, and a run is held to it less a point of margin. Every
suite that measures has to leave a data file with something in it, so a leg that never ran fails by
name.

## How the version moves

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml`, `src/decktalk/runtime/src/index.ts` and the pinned blocks of `README.md`,
writes `CHANGELOG.md`, and
picks the bump from the commits since the last release. That pull request runs `ci` like any other,
and a job in `ci.yml` runs `scripts/check.py --group generated --write` on its branch and
commits every file that changed, so no bot ever writes to `main`. The `rehearsal` group runs the same
release path on every pull request first, and the docstring of `scripts/rehearse_release.py` says
what it bumps and what it refuses.

| Commit | Bump while the version is below 1.0 | Bump from 1.0 |
|---|---|---|
| `fix:` | patch | patch |
| `feat:` | minor | minor |
| A `BREAKING CHANGE:` footer | minor, because `bump-minor-pre-major` is set | major |

The wheel is `src/decktalk` alone, so `exclude-paths` in `release-please-config.json` lists `docs`,
`assets`, `scripts`, `site`, `tests` and `.github`, and a commit confined to those bumps nothing. The option
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
`decktalk-0.5.0rc2-py3-none-any.whl` and its metadata says `0.5.0rc2`. pixi.lock holds no version
of the package itself, so a bump leaves it as it is.
`tests/contract/test_release_versions.py` accepts both spellings as long as every file names one
version. A candidate goes to PyPI and never to the conda channel, so to install a candidate, name it to uv:
`uv tool install decktalk==0.5.0rc2`.

## To release

1. Merge the release pull request. release-please creates the tag and the GitHub release.
2. Wait for the release workflow. It builds the wheel and the conda package from the tag with the
   `wheel` and `conda` rows, asks conda-forge for the Playwright pair, and checks that the tag and
   the packaged version agree.
3. For a final release, approve the `conda` environment, then check the channel page. The workflow
   uploads the conda package and its attestation through trusted publishing. A candidate has no
   channel upload and goes on to step 4.
4. Approve the `pypi` environment, then check PyPI. A PyPI version can never be uploaded again.
5. The workflow attaches the wheel, the sdist and, for a final release, the conda package to the
   GitHub release.

The two approvals are the two uploads: `conda` before `pypi`, each through a trusted publisher that
names its environment.

If a job fails after the tag exists, fix the cause and use **Re-run failed jobs** on the release
run. It runs the failed job and every job after it again, with the files the run already built,
and asks for each approval again. Both uploads pass over a file that already arrived, so a run
again after a partial upload completes it. Never use **Re-run all jobs**: it starts again at
release-please, which finds the release it already made, so every later job is skipped, the run is
green and nothing is uploaded.

If the channel has the package and PyPI refuses the version for good, delete the package on the
channel page, so the channel holds no version that PyPI does not, and release a new version.
