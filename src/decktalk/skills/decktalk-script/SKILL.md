---
name: decktalk-script
description: Write or change what the voice says. Use when the user asks for a new video, a new section, or a change to what the voice says.
---

# decktalk-script

## What this skill does

Write or change `script.md`, where `## N. Title` starts section N, and keep one `[[section]]` of
`decktalk.toml` per script section.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

End on `decktalk narrate --no-voice --dry-run --json`, which prints what a voiced run would send,
and hand over to `decktalk-slide` for the page that goes with the new words.
