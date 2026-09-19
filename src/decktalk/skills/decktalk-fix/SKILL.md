---
name: decktalk-fix
description: Read a DeckTalk finding and make the smallest change that removes its cause. Use when preflight, align, record, verify or build exits non-zero with error set to null, when a cue is unresolved or unknown, when a reveal lands off cue or shows no change, when a recording stalls or starts black, or when preflight skips every cue. It maps each findings.items[] code to its cause and its smallest fix, changes the page or the phrase before the script, and reruns only the command that failed. Do not use it to plan a rewrite, which belongs to decktalk-revise.
compatibility: Requires the decktalk command on PATH and the same tools the failing command needed. Most fixes are decided from JSON, and a few ask for a look at a PNG, which a model that cannot read an image should pass to the user.
metadata:
  ends_with: The smallest edit that clears the finding, and the failing command run again with `--only`.
---

# Fix the finding

A finding is something DeckTalk measured and judged. An error is something DeckTalk could not run
through. They are different, and the exit code and the envelope tell them apart.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table.

## Steps

1. **Tell a finding from an error.** Exit code 1 with `error` null is a finding, and the project can
   be fixed. Exit code 2 means the command line was wrong, so correct the command. Exit code 3 means
   DeckTalk itself could not run, so stop and tell the user `error.code`, `error.message`,
   `error.hint` and `error.path`. Never edit the project to make an exit code 3 go away.
2. **Group the rows.** Read `findings.items[]` from the failing command's envelope and group the rows
   by `code`. Each row carries `certain`, `section`, `cue`, `where` and `detail`. Fix every certain
   row. Show every uncertain row to the user and ask.
3. **Look for the rows that do not fail.** A preflight row whose `reason` is `NO_CATALOG`,
   `NO_SLIDE`, `NO_CUES` or `OPTED_OUT` is skipped rather than failed, so it can
   leave a command at exit code 0 with nothing measured. Read `preflight.cues[]` yourself and treat a
   skipped row as a stop. `NO_CATALOG` on every row means the page did not run at all.
4. **Find the cause.** Read `references/verdicts.md` for each code, its usual cause and its smallest
   fix. Fix the cause and not the measurement.
5. **Change the cheapest file that can hold the fix.** Change the page first, then `cues.json`, then
   `decktalk.toml`, and change `script.md` last and only with the user's agreement, because a script
   change re-voices that section.
6. **Rerun only what failed**, with `--only 3` where the command takes it, such as
   `decktalk preflight --only 3 --json`, `decktalk record --only 3 --json` or
   `decktalk verify --only 3 --json`. Rerun the whole chain only when the fix changed the narration.
7. **Show the row before and after.** Quote the failing row from the first run and the same row from
   the rerun, so the user can see what changed.
8. **Stop after three tries.** When a row still fails after three attempts, stop, report what you
   changed, what the row says now, and what you think the cause is.

## Rules

- Never pass `--force`, `--exit-zero`, `--allow-unresolved-cues`, `--allow-unknown-cues` or
  `--allow-placeholders`.
- Never raise a limit in the `[verify]` table and never lower a change threshold to clear a row.
- Never set `"verify": false` on a cue except for a reveal that is small by design, and only after
  showing the user a frame of it and getting an answer.
- Never change the seconds in a slide's `preview` object to fix the video. They drive the browser
  preview only.
- Never re-voice a section to fix something a page or a phrase can fix.
- Never start a voiced run from this skill. Hand the sections back to decktalk-build, which prices
  the run and then has to wait for the author to approve it before anything is sent.
- Never report a video as done while a certain finding stands.

## Gotchas

- A negative `offset_ms` on an `OFF_CUE` row usually means the previous reveal was still animating,
  not that the phrase is wrong. Shorten `data-duration` on the earlier element before touching the
  phrase.
- `UNRESOLVED` means the phrase is not in that section's narration word for word. Compare the phrase
  with `narrate.sections[].request.text` from `decktalk narrate --dry-run --json`, which sends
  nothing and is the exact string the voice receives.
- `UNKNOWN_CUE` means `cues.json` names an id the page never carries, and `UNCUED_ELEMENT` is the
  opposite mistake, an element carrying an id `cues.json` never names. `align` reports both, so read
  `align.unknown` and `align.uncued` together.
- A rerun of `decktalk verify` needs a build that already exists. After a page fix, record that
  section again with `decktalk record --only 3 --json` before verifying it.
- A finding that appears only under `--strict` is uncertain. Decide it with the user rather than
  silencing it.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off back to **decktalk-build** once every certain row is clear, so the video can be rebuilt and
verified. Tell it which sections you changed and whether the narration changed. Hand off to
**decktalk-slide** when the fix is a slide that has to be redesigned, and to **decktalk-cues** when
the fix is a phrase that has to be rewritten across a section.
