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

## Releasing

`uv version 0.2.0`, commit, `git tag v0.2.0`, push the tag. The release workflow checks the
tag against the version, builds, smoke-tests the wheel, publishes to PyPI through trusted
publishing, and creates a GitHub release.
