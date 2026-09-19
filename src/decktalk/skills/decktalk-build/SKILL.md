---
name: decktalk-build
description: Render, rebuild, verify or preview a DeckTalk video, and stop for the author's approval before any narration is paid for. Use when someone asks to build, render, export, preview or check a narrated video, an explainer, a tutorial or a lesson, or after any edit to script.md, cues.json, decktalk.toml or a deck page. It rehearses without voice, reports which sections a voiced run would send and what they cost, waits for approval, follows the long run through status, and finishes with verify and frames. Do not use it to repair a finding, which belongs to decktalk-fix.
compatibility: Requires the decktalk command on PATH, a browser and ffmpeg installed by decktalk install, and network access with a speech key for a voiced run. Step 10 reads PNG files, so a model that cannot read an image should ask the user to look at the frames instead.
metadata:
  ends_with: A verified build, with the frames shown to you before the work is called done.
---

# Build and verify

A build records each section's page, cuts it to the narration and writes one mp4 with its captions,
chapters and transcript. A voiced build spends the author's money, so it never starts without a stop.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table. Exit code 0 means nothing was found, 1 means a finding with
`error` null, 2 means the command line was wrong, and 3 means DeckTalk could not run, in which case
stop and tell the user what `error.message` says. Read `references/json-fields.md` whenever you need
the exact name of an envelope field, a command's payload key or a line of the progress log.

## Steps

1. **Confirm the tools.** Run `decktalk doctor --json` and stop when any row of `doctor.components`
   has `ok` false and `optional` false. Run `decktalk install` when the browser or ffmpeg is missing.
2. **Read the project.** Run `decktalk status --json`. `status.sections[]` gives each section, what
   it plays, whether it is recorded, and `stale`, which is one sentence saying why a recording no
   longer matches the project, or null. `status.narration.estimated` is true when the takes are
   placeholders and false when real takes exist, and `status.narration.exists` is false when nothing
   has been narrated yet. `status.run` is not null when a build is already going, in which case
   follow that run instead of starting another.
3. **Rehearse without voice, when nothing is voiced yet.** With `status.narration.estimated` true or
   `status.narration.exists` false, run `decktalk build --no-voice --json`. This spends nothing and
   gives a video whose timing is estimated. Say so when you show it.
4. **Never replace a real take.** With `status.narration.exists` true and
   `status.narration.estimated` false, do not run any command with `--no-voice`. The command refuses
   it, and the only way past throws the paid takes away. A project with no takes at all reports both
   as false, and step 3 applies to it.
5. **Check before spending.** Run `decktalk preflight --json`. Stop on exit code 1. Also stop on any
   row of `preflight.cues[]` whose `reason` is `NO_CATALOG`, `NO_SLIDE`, `NO_CUES` or `OPTED_OUT`, and
   on any row whose `verdict.code` is `NO_CHANGE` or `THIN_CHANGE`, because a skipped or uncertain
   row does not fail the exit code on its own.
6. **Price the run and stop for approval.** Run `decktalk narrate --dry-run --json`, which sends
   nothing. Report to the author, from `narrate.sections[]`, every row whose `status` is
   `synthesize`, with its `chapter`, its `reason` and its `characters_sent`, then
   `narrate.totals.characters_sent` and `narrate.totals.estimated_cost`, and the provider and model
   from `narrate.voice`. When `narrate.totals.unknown` is above zero, quote
   `narrate.totals.most_it_can_cost` as well, because those sections could not be checked against the
   cache. Report the spoken-math table from the script for the author to sign off. **Then wait for
   the author to approve. Run no voiced command until the author answers.** When no author can
   answer, run only the commands that spend nothing, which is every command with `--no-voice` or
   `--dry-run`, and report what you would have asked. When every section plans
   as `synthesize` in a project that was voiced before, say so and stop, because that means the take
   cache is missing rather than that the script changed.
7. **Run the build.** After the author approves, run `decktalk build --json`, or
   `decktalk build --only 3 --json` for each changed section when a full build already exists. A
   recording is kept only while that section's own scene markup, the rest of its page and the files
   the page loads are unchanged, so name every section that plays a scene you edited, name every
   section of a page whose head, styles or scripts you edited, and name the section after one you
   changed when that page reads the previous section's words. A section left out is reported as
   `INCONSISTENT`, and the film keeps what was recorded before. `decktalk build --dry-run --json` names the stages a run would
   execute and every artifact it would need and does not have, and it spends nothing.
8. **Follow a long run.** Recording happens in real time, so a long video takes longer than one
   command usually may. Poll `decktalk status --json` and read `status.run` for `stage`,
   `sections_done`, `alive` and the `pid`. Read `build/progress.jsonl` for the events themselves. Do
   not start a second build while `alive` is true.
9. **Verify the result.** Run `decktalk verify --json`. `verify.starts[]` catches a black opening,
   `verify.cuts[]` catches a cut over speech, `verify.seams[]` catches a jump at a seamless cut,
   `verify.cues[]` gives each cue its `offset_ms` and its `verdict`, and `verify.recordings[]`
   repeats what each recording log judged, which is the duration and brightness check.
10. **Show the evidence.** Run `decktalk screenshots --section 3 --at 12.5 --json` just after each
    changed section's main cue, look at every PNG, and show the author the frames with the verify
    summary before calling the work done. Report the paths of the mp4, the captions, the chapters and
    the transcript from `written`.

## Rules

- Never pass `--force`, `--exit-zero`, `--allow-unresolved-cues`, `--allow-unknown-cues` or
  `--allow-placeholders`.
- Never set `"verify": false` on a cue and never raise a `[verify]` limit to make a check pass.
- Never change `[voice]`, the voice id or the model without approval, because each re-voices
  everything.
- Never run `decktalk soundscape` without a separate approval, because it spends as well. Run
  `decktalk soundscape --dry-run --json` first and show what it would send.
- Try a fix at most three times, then stop and report the finding to the author.

## Gotchas

- `decktalk narrate --dry-run --json` prices the run at `[voice] price_per_1000_characters`. When
  that key is unset the cost keys are null, so quote the characters and let the author price them.
- A fresh clone or a new worktree has no take cache under `build/`, so every section plans as
  `synthesize` even though nothing changed. `[narration] cache_dir` is what a machine sets to share
  one take directory between checkouts.
- `--only` limits the sections that are recorded, not the sections that are voiced.
- `record` keeps a section whose scene, shared page, assets, words and cues are unchanged, and its
  row reads `kept` true. A section named by `--only` is always recorded again.
- `status.run.sections_total` is null, because no stage reports a total yet. Count the sections
  yourself from `status.sections[]`.
- A build that exits 0 has not proved the cues land. Run `decktalk verify --json` yourself.
- To look at a page in a browser rather than in a PNG, run `decktalk serve --open`, which serves the
  project over http so the page loads its own files.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off to **decktalk-fix** the moment any command exits non-zero with `error` null, or a preflight
row is skipped. Tell it the command that failed, the whole `findings.items[]` array, and the sections
involved. Hand off to **decktalk-cues** instead when the only findings are unresolved or unknown
cues, and to **decktalk-slide** when the only findings are reveals the checker could not see.
