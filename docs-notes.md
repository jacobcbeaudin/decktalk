# Docs notes from track T1 (phase 1, test net and CI)

Exact page and replacement text for the docs track. Nothing under `docs/` was edited in this track.

## docs/guides/ci-and-offline.mdx, line 131

Current: "own CI builds `tests/timing` and fails on any finding there. It runs `decktalk verify --no-fail` on the scaffold."

Replace with: "own CI builds `tests/e2e/fixture`, a five-section still deck, with `pytest -m e2e`, and fails on any
verify finding there, `OFF CUE` included, on Linux. The same test runs on macOS and Windows on every push to
`main` and before a release is published, where it reports `OFF CUE` and asserts wider limits instead."

## New facts a docs page may state

- The one local check command is `uv run scripts/check.py`. It may download Chromium, ffmpeg and KaTeX on its
  first run. `--fast` runs lint, types and the unit tests alone.
- A bare `pytest` runs the unit tests. The markers are `browser`, `media`, `e2e` and `scaffold`.
- Pull request CI runs on Linux only. macOS and Windows run on pushes to `main`, from the Actions tab, and in
  `release.yml` before `publish`.
- No ElevenLabs key is ever a CI secret.
