---
name: decktalk-build
description: Decide when a DeckTalk film is ready to be rendered, and never spend the author's money without an answer from them. Use when someone asks to build, render, export, preview or check a narrated video, an explainer, a tutorial or a lesson, or after any edit to the script, the cues, the project file or a deck page. It covers rehearsing without a voice, the storyboard as the checkpoint before credits are spent, what to put in front of the author before buying, what a build without a voice proves and what it does not, and when to stop trying. Do not use it to repair a finding, which belongs to decktalk-fix.
compatibility: This is judgement rather than a command list. Run `decktalk --help` for the commands, `decktalk schema` for every flag and result, and `decktalk status` for where the project stands.
metadata:
  ends_with: A verified film, and an author who saw the price and the frames before either was spent.
---

# Build, and stop before you spend

A build records each section's page, cuts it to the narration, and writes one film with its captions,
chapters and transcript. Narration costs money. Everything else is free and repeatable, so the whole
craft of a build is knowing which half you are in.

## The order to work in

1. **Read the project before you change it.** `decktalk status` says which sections exist, which
   recordings no longer match the project and why, whether the takes are placeholders or paid, and
   whether a run is already going. Follow a run that is already going rather than starting a second.
2. **Rehearse without a voice.** A project whose takes are placeholders costs nothing to build, and
   the film it gives has estimated timing. Say so whenever you show it.
3. **Never replace a paid take.** Once real takes exist, a build without a voice is refused, and the
   only way past throws them away. That is the refusal working, not an obstacle.
4. **Judge before you spend.** `decktalk check` measures the project without producing anything and
   prices what a voiced run would cost. Stop on anything certain. Read the rows it could not measure
   as well as the rows it failed, because a skipped row proves nothing and still leaves a run green.
5. **Show the storyboard.** It is the frames of every slide at every cue, and it is the human
   checkpoint before credits are spent. Look at it yourself, and put its path in front of the author.
6. **Put the price in front of the author and wait.** Name every section that would be voiced and
   why, the total, and the price the estimate is based on. **Then wait for an answer. Run nothing
   that spends until the author has given one.** When nobody can answer, run only what spends
   nothing and report what you would have asked.
7. **Record what changed, and name every section it touches.** A recording is kept only while the
   section's own scene, the rest of its page and the files the page loads are unchanged. So a page
   edit outside any scene stales every section of that page, and a section that reads the previous
   section's words goes stale with it.
8. **Follow a long run rather than restarting it.** Recording happens in real time, so a long film
   takes longer than one command usually may. Watch the run's own events.
9. **Verify, and show the evidence.** A build that exits zero has not proved the cues land. Verify
   measures the finished film against what the project said it would be. Show the author the frames
   and the verify summary before calling the work done.

## Rules

- Never pass a flag that hides a finding, forces a run past a refusal, or discards a paid take.
- Never change the voice, the model or a voice setting without an answer, because each re-voices
  everything.
- Never buy sound effects or music without a separate answer. It spends too.
- Try a fix at most three times, then stop and report the finding and what you think the cause is.
- Never report a film as done while a certain finding stands.

## Gotchas

- A fresh clone has no take cache, so every section looks like it needs voicing even though nothing
  changed. Work in the checkout that holds the build, or say what a full re-voice would cost.
- When every section plans as new in a project that was voiced before, the cache is missing rather
  than the script changed. Stop and find out which.
- Limiting a build to some sections limits what is recorded, not what is voiced.
- A film built without a voice has no spoken landmark, so some cue measurements cannot be taken until
  it is voiced.

## Hand off

Hand off to **decktalk-fix** the moment a command reports a finding, with the finding rows and the
sections involved. Hand off to **decktalk-cues** when the only findings are phrases, and to
**decktalk-slide** when the only findings are reveals the checker could not see. Hand off to
**decktalk-script** when the fix is a sentence.
