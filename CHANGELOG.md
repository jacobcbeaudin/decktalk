# Changelog

## Unreleased

- Project document is `decktalk.toml` (was `scenes.json`); `cues.json` unchanged.
- Layered package: `decktalk.stages`, `decktalk.media`, `decktalk.providers`; typed
  `Project`, `Settings`, and build artifacts; a small public Python API.
- Configuration in three layers: defaults, `decktalk.toml` tuning tables, `DECKTALK_*` env.
- Stage functions return results and raise `DeckTalkError` subclasses; progress via `logging`.
- Removed text-to-video b-roll generation (never exercised). Put clips in `media/` instead.
- Cue verification counts changed pixels inside the section, so thin reveals register.
- Titled slate rendered for a missing clip.
- Runtime tests in a real Chromium (`pytest -m browser`); CI on Linux and macOS.

## 0.1.0

- First extraction from the hackathon pipeline: narrate, beats, record, measure,
  assemble, verify, shots, soundscape; `decktalk-runtime.js`; `decktalk init`.
