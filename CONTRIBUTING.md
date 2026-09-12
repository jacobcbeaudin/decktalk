# Contributing

Issues and pull requests are welcome. If you build something with DeckTalk, a link in an
issue is welcome too.

## Setup

```console
uv sync --group dev
uv run decktalk setup            # headless Chromium and ffmpeg, once
```

## Checks

```console
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src
uv run pytest -q                 # unit tests
uv run pytest -q -m browser      # the page runtime, in a real Chromium
bash tests/smoke.sh              # scaffold a project and build it offline
uv run scripts/build_assets.py --check   # README graphics are generated; regenerate, do not hand-edit
```

CI runs the same on Linux, macOS and Windows.

## Layout

```
src/decktalk/
  config.py      tuning dataclasses; defaults -> decktalk.toml tables -> DECKTALK_* env
  project.py     the decktalk.toml document, validated at load
  artifacts.py   typed build artifacts (manifest, timeline, beats, sidecar)
  stages/        narrate, beats, record, measure, assemble, verify, shots, soundscape, build
  media/         ffmpeg and Chromium (internal)
  providers/     ElevenLabs (internal)
  runtime/       decktalk-runtime.js, the page contract
  template/      what `decktalk init` writes
```

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed
result, and raise `DeckTalkError` subclasses. The CLI is a thin layer that prints tables
and maps errors to exit codes.

## Commits and releases

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org): `fix:`
bumps the patch version, `feat:` the minor, `feat!:` or a `BREAKING CHANGE:` footer the major
(minor while 0.x). `pre-commit install` adds a hook that checks the message; the `pr-title`
workflow checks pull request titles, because a squash merge turns the title into the commit.

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml` and `uv.lock`, writes `CHANGELOG.md`, and picks the bump from the commits
landed since the last release. Merging that PR creates the tag and the GitHub release; the
release workflow then builds, smoke-tests the wheel, and publishes to PyPI through trusted
publishing. To force a version, put `Release-As: 1.0.0` in a commit body.
