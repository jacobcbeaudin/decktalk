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

A release goes to two places. PyPI gets the wheel and the sdist of every release. The conda channel
`https://prefix.dev/jacobcbeaudin/jcbcodes`, which `install.sh` and `pixi global install` read, gets the noarch
conda package of every final release, built by `pixi build` from the tag and uploaded after PyPI
has the same version. The upload uses trusted publishing, as PyPI does: the `publish-conda` job
holds a GitHub OIDC token, prefix.dev exchanges it for a short upload token, and no prefix.dev key
is stored anywhere. The upload carries a Sigstore attestation of the package, signed with the same
identity. When prefix.dev gives no token, the job stops with an error that names the trusted
publisher to register.

The channel `jacobcbeaudin/jcbcodes` names its trusted publisher once. On the channel page
`https://prefix.dev/channels/@jacobcbeaudin/jcbcodes`, open Settings, then Repository Access, then
Add access, and fill in:

| Field | Value |
|---|---|
| Source | GitHub |
| Owner | `jacobcbeaudin` |
| Repository | `decktalk` |
| Workflow file | `release.yml` |
| Environment | empty, any environment |
| Access | Read/write |
| Upload access | All packages |

The entry then reads "jacobcbeaudin / decktalk, READ/WRITE, release.yml, Any environment".

Every action is pinned to a commit SHA with its version in a comment, every workflow grants
`contents: read` at the top and more only per job, and `zizmor .github/workflows`, at the version
pixi.lock pins, is part of the `lint` group. The name `release.yml` and the environment name `pypi` are registered with PyPI's
trusted publisher, and the name `release.yml` with the trusted publisher of the channel `jacobcbeaudin/jcbcodes`, so neither may be renamed.

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
2. Wait for the release workflow. It checks that the tag and the packaged version agree, builds the
   wheel from the tag and opens it in an isolated environment.
3. Approve the `pypi` environment. A PyPI version can never be uploaded again, so this is the one
   step with a reviewer.
4. Check PyPI. The workflow publishes through trusted publishing and attaches the files to the
   GitHub release.
5. For a final release, check `https://prefix.dev/channels/@jacobcbeaudin/jcbcodes`. The workflow uploads the conda
   package and its attestation through trusted publishing after PyPI, and attaches the package to
   the GitHub release.
