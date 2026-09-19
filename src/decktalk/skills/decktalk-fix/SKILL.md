---
name: decktalk-fix
description: Read the findings of a failed command and make the smallest fix. Use when a command exits non-zero with `error == null`, or preflight skips every cue.
---

# decktalk-fix

## What this skill does

Read the findings of a failed command and make the smallest change that answers them. A finding
names its section, its cue and the file it is about.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

Read the findings with `--json`, change the one file the finding names, and hand back to the skill
that owns that file: `decktalk-script`, `decktalk-slide` or `decktalk-cues`.
