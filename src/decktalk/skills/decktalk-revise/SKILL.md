---
name: decktalk-revise
description: Update an existing video after the facts behind it changed. Use when the user says the product, the library or a fact changed.
---

# decktalk-revise

## What this skill does

Update a video that already exists after the facts behind it changed, by changing the fewest
sections, so the rest keep their cached narration and their recordings.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

End on `decktalk build --no-voice --only N` for each section that changed, then hand over to
`decktalk-build` for the whole video.
