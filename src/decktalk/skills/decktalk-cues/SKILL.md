---
name: decktalk-cues
description: Match every reveal to the spoken phrase it lands on. Use when the script, a reveal or a page changes, or `decktalk align` reports a finding.
---

# decktalk-cues

## What this skill does

Write or change `cues.json`, which says which spoken phrase each reveal lands on. A cue id starts
with the id of the slide that owns it.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

End on `decktalk narrate --no-voice` and then `decktalk align --json`, which resolves every phrase
to a second, and hand over to `decktalk-build` once every cue resolves. Aligning before a narration
exists fails, because a phrase is resolved against words that have been spoken.
