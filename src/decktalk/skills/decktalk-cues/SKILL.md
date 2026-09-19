---
name: decktalk-cues
description: Match every reveal on a DeckTalk page to the spoken word it lands on, by writing and repairing cues.json. Use when a script, a page or a reveal changes, when align reports an unresolved or unknown cue, when a picture appears early, late or not at all, or when a reveal is too small for the checker to see. It checks the cue ids in both directions, picks phrases written as spoken, sets occurrence for a repeated phrase, measures the gap between neighbouring cues, and clears every NO CHANGE and THIN CHANGE? row. Do not use it to render the video, which belongs to decktalk-build.
compatibility: Requires the decktalk command on PATH and a browser installed by decktalk install. Every check in this skill reads JSON, so a model that cannot read an image can follow all of it.
metadata:
  ends_with: Every `data-cue` matched to `cues.json` in both directions, with the gaps inside their thresholds.
---

# Place the cues

`cues.json` says which spoken phrase each picture waits for. One entry names a cue id and the phrase
it lands on, and the page carries the same id on the element that reveals.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table. Exit code 0 means nothing was found, 1 means a finding with
`error` null, 2 means the command line was wrong, and 3 means DeckTalk could not run, in which case
stop and tell the user what `error.message` says.

## Steps

1. **Learn whether the project has been voiced.** Run `decktalk status --json` and read
   `status.narration`. `estimated` true, or `exists` false, means the takes are placeholders and a
   build without voice is free. `estimated` false means real takes exist and the narration must not
   be replaced.
2. **Match the ids in both directions.** List every `data-cue` value on the page that each section
   plays, and every `cue` in that section of `cues.json`. An id on the page with no entry appears at
   the slide mount instead of on a word. An id in `cues.json` that the page never carries is reported
   by `decktalk align --json` as an unknown cue, and an id on the page that `cues.json` never names
   is reported as an uncued element. Fix both sides.
3. **Write each phrase as the script speaks it.** The phrase must appear in that section of
   `script.md` word for word, in words and never in digits. Start it on the word that names the thing
   that appears, and prefer two or three words to one. Read `references/cue-phrases.md` for the
   patterns that work and the ones that do not.
4. **Choose the right match for a repeated phrase.** Set `"occurrence": 2` when the phrase appears
   more than once in the section. Set `"case_sensitive": true` only when the case is what
   distinguishes the two.
5. **Nudge with an offset, not with a rewrite.** Set `"offset": 0.15` for a reveal that points at
   something the voice has just named. Use a small negative offset only for text a viewer reads along
   with the voice.
6. **Resolve the phrases.** Run `decktalk narrate --no-voice --json` first when step 1 said the takes
   are placeholders, then run `decktalk align --json`. Read `align.sections[]`. Every entry of
   `cues` maps a cue id to its second on the section clock, and `notes` carries a row for every
   ambiguous phrase. `align.unresolved`, `align.unknown` and `align.uncued` are the three counts to
   clear: a phrase that is not in the narration, an id the page never carries, and an element that
   carries an id `cues.json` never names. Fix all three before going on.
7. **Measure the pacing.** Compute the gap between neighbouring cues from the seconds in
   `align.sections[].cues`. Flag a gap under two seconds, a gap over twenty seconds, and any section
   that averages under five seconds per cue. A cue that resolves to 0.00 seconds is flagged too,
   because it is skipped later as `AT_SECTION_START`, so move its phrase further into the sentence
   now. Show those rows to the user and ask before keeping one.
8. **Prove that each reveal is visible.** Run `decktalk preflight --json` and read `preflight.cues[]`.
   Fix every row whose `verdict.code` is `NO_CHANGE` or `THIN_CHANGE` by making the reveal larger or
   dimming what surrounds it. Treat any row whose `reason` is `NO_CATALOG`, `NO_SLIDE`, `NO_CUES` or
   `OPTED_OUT` as a stop, because a skipped row does not fail the exit code.
9. **Look at the moment.** Run `decktalk screenshots --slide 3.1 --after 3.1open --json` for each cue
   that points at part of a picture, and confirm that the thing revealed is the thing the words name.

## Rules

- Never edit the seconds in a slide's `preview` object to fix the video. Those seconds drive the
  browser preview only.
- Never set `"verify": false` on a cue to silence a finding, unless the reveal is small by design and
  the user has seen a frame of it and agreed.
- Never run a build without voice in a project that holds real takes, and never pass `--force`.
- Never pass `--allow-unresolved-cues` or `--allow-unknown-cues` to get a green result.
- Change the phrase or the page before the script. A script change re-voices the section.

## Gotchas

- A phrase written as "2.3" in the script cannot be cued as "version two point three", and the
  reverse fails too. The script and the phrase must agree word for word.
- `decktalk preflight --json` fills `preflight.cues[]` only when it freezes frames, so do not pass
  `--no-frames` when you need those rows.
- A cue that lands at the very start of a section is skipped with the reason `AT_SECTION_START`,
  because there is no earlier frame to compare it with. Move the phrase later in the sentence.
- Case is ignored and punctuation between words is ignored, and a hyphenated word counts as written.
  A possessive is part of its word, so `project` does not match `project's`.
- A cued element deserves a `data-describe` sentence, because the transcript page is written from
  those sentences and a reveal without one is silent to a reader.
- To watch the cues fire in a browser rather than in a PNG, run `decktalk serve --open` and open the
  page it prints.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off to **decktalk-build** once `decktalk align --json` and `decktalk preflight --json` both exit
0, so the video can be rendered. Tell it which sections changed, whether the project already holds
real takes, and which cues the user agreed to keep despite a tight gap. Hand off to **decktalk-fix**
instead when a command exits 1 with `error` null and the cause is not a phrase you just wrote.
