---
name: decktalk-revise
description: Update an existing DeckTalk film after a product, a library, a number or a fact changed, re-voicing as few sections as possible. Use when someone says a demo, tutorial, explainer or lesson is out of date, asks for this week's update, or names a release that changed what the film claims. It covers listing the stale claims, mapping each change to the sentences, cues, slides and images it touches, proving the scope before anything is spent, keeping the cut that was already approved, and writing the change note. Do not use it for a first film, which belongs to decktalk-script.
compatibility: This is judgement about scope and cost. Run `decktalk status` for where the project stands and `decktalk --help` for the commands.
metadata:
  ends_with: The fewest edits, a rebuild of the changed sections alone, and a note saying what changed and what it cost.
---

# Revise the film

A revision is an edit that respects what has already been paid for. The goal is the smallest set of
changed sections and a written record of what changed and what it cost. Read
`references/edit-scope.md` for what each kind of edit costs and which neighbours it drags in, and
`references/claims-ledger.md` for the ledger.

## The craft

1. **Read the project as it stands.** Which sections exist, which recordings no longer match and
   why, whether the takes are paid or placeholders, and which cut is already built.
2. **Collect what changed.** Ask for the release notes or the list of changes, and for the date
   beyond which a claim counts as stale. List every changed name, number, price, feature and screen.
3. **List the stale claims.** Every ledger row older than that date, and every row a change touches.
4. **Map each change to the files it touches**, before editing anything: the sentences, the cues, the
   slides and the images that carry it. Show the map and wait for an answer.
5. **Retake only the pictures a change touched.** From a demo account, never from real customer data,
   at twice the stage scale. Compare each new image with the one it replaces, and keep only the ones
   that really changed. Record the date and the version beside each.
6. **Make the fewest edits.** Keep every section number, every chapter, every cue id and every voice
   setting. Edit only the sentences a change touches, and update the number on screen and the number
   in the script together.
7. **Prove the scope before anything is spent.** Every section that would be paid for should be a
   section you touched. When a section you did not touch would be paid for, stop and find out why:
   its text changed by accident, a voice setting changed, or this checkout has no take cache.
8. **Keep the previous cut.** Ask for a dated second file before rebuilding, so the cut the author
   already approved survives.
9. **Put the price in front of the author and wait**, exactly as a first build does.
10. **Verify, then write the change note.** The sections changed, the claims updated, the images
    replaced, what was spent, and every finding still open.

## Rules

- Never change the voice, the model or a voice setting. Each re-voices the whole film.
- Never renumber or insert a section without saying that every renumbered section is recorded again.
  No take is paid for twice for its position, so only new words cost money.
- Never run a build without a voice in a project that holds paid takes.
- Never leave a claim in the film that the ledger cannot source.
- Never pass a flag that hides a finding or forces a run past a refusal.

## Gotchas

- A changed screenshot changes the picture and not the words, so the section is recorded again and
  not voiced again.
- A fresh clone has no take cache, so every section looks new. Work where the build is, or say what a
  full re-voice would cost.
- Limiting a rebuild to some sections limits what is recorded, not what is voiced.
- A page edit outside any scene, to the head, a stylesheet or a script, reaches every scene of that
  page, so every section that plays it is stale.

## Hand off

Hand off to **decktalk-script** for a sentence that has to be rewritten rather than corrected, to
**decktalk-slide** for a picture that has to be redesigned, and to **decktalk-cues** when a rewritten
sentence leaves a cue without its word. Tell each one the sections on the map, the claims that
changed and the ids that must not move. Hand off to **decktalk-build** for the rebuild itself, and to
**decktalk-fix** whenever a command reports a finding.
