---
name: decktalk-build
description: Build the video and check it before calling it done. Use when the user asks to render, rebuild or check the video.
---

# decktalk-build

## What this skill does

Build the video and check it before calling it done.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

Run `decktalk build --no-voice`, then `decktalk verify --json`, and hand over to `decktalk-fix` when
either exits non-zero.
