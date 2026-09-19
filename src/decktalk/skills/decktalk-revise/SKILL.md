---
name: decktalk-revise
description: Update an existing DeckTalk video after the product, a library, a number or a fact changed, re-voicing as few sections as possible. Use when someone says a demo, tutorial, explainer or lesson is out of date, asks for this week's update, or names a release that changed what the video claims. It lists the stale claims, maps each change to the sentences, cues, slides and screenshots it touches, proves with narrate --dry-run that only the changed sections are voiced, and builds with --only after approval. Do not use it for a first video, which belongs to decktalk-script.
compatibility: Requires the decktalk command on PATH and an existing project with a built video. Step 6 compares screenshots, so a model that cannot read an image should ask the user to compare the old and new pictures.
---

# Revise the video

A revision is an edit that respects what has already been paid for. The goal is the smallest set of
changed sections, and a written record of what changed and what it cost.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table. Exit code 0 means nothing was found, 1 means a finding with
`error` null, 2 means the command line was wrong, and 3 means DeckTalk could not run, in which case
stop and tell the user what `error.message` says.

## Steps

1. **Read the project as it stands.** Run `decktalk status --json`. Read `status.sections[]` for what
   exists and which rows carry a `stale` sentence, `status.narration.estimated` for whether the takes
   are real, and `status.final` for the video that is already built.
2. **Collect what changed.** Ask the user for the release notes, the changelog or the list of changes,
   and ask for the date beyond which a claim counts as stale. List every changed name, number, price,
   feature and screen.
3. **List the stale claims.** Open the project's claims ledger and list every row whose `checked`
   date is older than that date, and every row a change touches. Read
   `references/claims-ledger.md` for the ledger's columns and rules.
4. **Map each change to the files it touches.** For each change, name the sentences in `script.md`,
   the cues in `cues.json`, the slides on each page and the images under `media/` that carry it.
   Read `references/edit-scope.md` for what each kind of edit costs and which neighbours it drags in.
5. **Show the map and wait.** Show the user the stale claims and the map of sections before editing
   anything. Do not edit until the user answers.
6. **Capture the pictures again.** Retake every screenshot a change touches, from a demo account and
   never from real customer data, at two times the stage scale. Compare each new image with the one
   it replaces, list the images that really changed, and record the date and the product version
   beside each one.
7. **Make the fewest edits.** Keep every section number, every `chapter`, every cue id and every
   `[voice]` key as it is. Edit only the sentences a change touches. Update the on-screen number and
   the spoken number together.
8. **Prove the scope before spending.** Run `decktalk narrate --dry-run --json`, which sends
   nothing, and read `narrate.sections[]`. Every row whose `status` is `synthesize` is a section that
   will be paid for, `cached` is a take already on disk, and `unknown` is a section whose cache could
   not be checked. When a row you did not touch plans as `synthesize`, stop and find out why before
   going on. A project whose takes are placeholders is the one exception: every row there plans as
   `synthesize` with the reason `only a take without voice exists`, which is expected and is not a
   scope failure.
9. **Keep the old video.** Set `timestamped_copy = true` in the `[output]` table of `decktalk.toml`
   before rebuilding, so the build writes a dated second mp4 and the previous cut survives.
10. **Stop for approval.** Report each section that will be voiced with its `chapter`, its `reason`
    and its `characters_sent`, then `narrate.totals.characters_sent` and
    `narrate.totals.estimated_cost`, then the provider and model from `narrate.voice`.
    **Then wait for the user to approve. Run no voiced command until the user answers.**
11. **Rebuild only what changed.** After the user approves, run `decktalk build --only 3 --json`
    for each section on the map, including every section that plays a page you changed and the
    section after one you changed when that page reads the previous section's words. Follow the run
    with `decktalk status --json` and `status.run`.
12. **Verify and write the change note.** Run `decktalk verify --json` and read `verify.recordings[]`
    for each recording's own verdicts beside the film's. Write a note listing the sections changed,
    the claims updated, the images replaced, the characters sent and every finding that is still
    open.

## Rules

- Never pass `--force`, `--exit-zero` or any `--allow-` flag.
- Never change the voice id, the model or a `[voice]` key, because each of those re-voices the whole
  video.
- Never renumber a section, and never insert a new first section without telling the user that the
  old first section will be voiced again.
- Never run a command with `--no-voice` in a project that holds real takes.
- Never leave a claim in the video that the ledger cannot source.

## Gotchas

- A changed screenshot changes the picture but not the words, so the section must be recorded again
  even though it is not re-voiced.
- A fresh clone or a new worktree has no take cache under `build/`, so every section plans as
  `synthesize`. Work in the checkout that holds the build, or tell the user what a full re-voice
  costs.
- `--only` limits the sections that are recorded, not the sections that are voiced.
- Renumbering keeps a take, because a take is keyed by its text, with the first spoken section as the
  one exception, which carries the opening silence.
- `decktalk build --from record --json` reruns the picture without touching the narration, which is
  what a change that replaced only a screenshot needs.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off to **decktalk-script** for a sentence that has to be rewritten rather than corrected, to
**decktalk-slide** for a picture that has to be redesigned, and to **decktalk-cues** when a rewritten
sentence leaves a cue phrase without its word. Tell each one the sections on the map, the claims that
changed and the ids that must not move. Hand off to **decktalk-build** for the rebuild itself when
the revision grows past a few sections, and to **decktalk-fix** whenever a command reports a finding.
