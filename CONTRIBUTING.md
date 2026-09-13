# Contributing

Issues and pull requests are welcome. This is a one-person project, so expect a reply
within about a week, and open an issue before a large change so the design is agreed
first. If you build something with DeckTalk, a link in an issue is welcome too.

## Setup

You need [uv](https://docs.astral.sh/uv/), which installs the Python version and every
dependency. The smoke build in `tests/smoke.sh` needs bash, which on Windows means Git Bash.
Run every command from the repository root.

```console
uv sync --group dev
uv run decktalk setup            # headless Chromium, ffmpeg, and KaTeX, once
uvx pre-commit install           # the lint hooks and the commit message check
```

## Checks

Run every command from the repository root.

```console
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src
uv run pytest -q                                # unit tests
uv run pytest -q -m browser                     # the page runtime, in a real Chromium
bash tests/smoke.sh                             # scaffold a project and build it offline
uv run scripts/build_assets.py --check          # graphics are generated, so regenerate them
uv run scripts/build_config_reference.py --check # so is docs/reference/configuration.mdx
```

No check needs an ElevenLabs key or network access after `decktalk setup`. Do not add a
check that calls the API.

CI runs the same checks except `scripts/build_assets.py --check`, with the unit checks on
Python 3.12 to 3.14. Linux is the canary:
every push to `main` and every pull request runs the browser tests and the smoke build
there. The macOS and Windows builds run from the Actions tab through "Run workflow" and on
a tag that a person pushes. A release tag that release-please creates does not start the
CI workflow. Each run uploads its smoke video as an artifact.

## Layout

```text
src/decktalk/
  config.py      tuning dataclasses; defaults -> decktalk.toml tables -> DECKTALK_* env
  project.py     the decktalk.toml document, validated at load
  artifacts.py   typed build artifacts (manifest, timeline, beats, sidecar)
  stages/        narrate, beats, record, measure (with check), assemble, verify, shots, soundscape, build
  media/         ffmpeg and Chromium (internal)
  providers/     the speech protocol and the ElevenLabs provider (internal)
  runtime/       decktalk-runtime.js, the page contract
  template/      what `decktalk init` writes
tests/
  test_units.py      config layering, project validation, script parsing, cue matching
  test_runtime.py    drives decktalk-runtime.js in a real Chromium (-m browser)
  smoke.sh           an offline build of the scaffold, verified cue by cue
scripts/
  build_assets.py             generates assets/*.svg, docs/images, docs/logo, the favicon
  build_changelog.py          generates docs/changelog.mdx from CHANGELOG.md
  build_config_reference.py   generates docs/reference/configuration.mdx from config.py
docs/                          the Mintlify site at docs.decktalk.app
site/                          the landing page at decktalk.app
```

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed
result, and raise `DeckTalkError` subclasses. The CLI is a thin layer that prints tables
and maps errors to exit codes.

Two seams are meant for extension. The page contract lives in `runtime/decktalk-runtime.js`
and is documented at [docs.decktalk.app/concepts/page-contract](https://docs.decktalk.app/concepts/page-contract).
The voice lives behind `providers/speech.py`, a two-method protocol. There is no plugin
loading, so a new provider is a pull request.

## Roadmap

- A local text-to-speech provider paired with a forced aligner will let a project build with no API.
- A second slide template will offer a lighter visual style.
- The README will show a real demo video, built from the scaffold with a cloned voice.

## Prose

Documentation and comments use complete sentences with subjects. Avoid fragments,
semicolons, and version-specific wording that goes stale.

## Commits and releases

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org). A `fix:`
commit bumps the patch version. A `feat:` commit bumps the minor version. A `feat!:` commit or
a `BREAKING CHANGE:` footer bumps the major version, except that while the version is below
1.0 it bumps the minor version, because `bump-minor-pre-major` is set. The commit message
hook from `uvx pre-commit install` checks the message. The `pr-title` workflow checks pull
request titles, because a squash merge turns the title into the commit.

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml` and `uv.lock`, writes `CHANGELOG.md`, and picks the bump from the commits
landed since the last release. Merging that PR creates the tag and the GitHub release. The
release workflow then runs the unit checks on Linux, builds the wheel, checks that the
installed wheel runs `decktalk init`, and publishes to PyPI through trusted publishing. To
force a version, put `Release-As: 1.0.0` in a commit body.
