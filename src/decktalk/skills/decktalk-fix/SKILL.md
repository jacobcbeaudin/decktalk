---
name: decktalk-fix
description: Read a DeckTalk finding and make the smallest change that removes its cause. Use when a command reports a finding, when a cue is unresolved or unknown, when a reveal lands off its word or shows no change, when a recording stalls or opens black, or when a check could measure nothing at all. It covers telling a finding from an error, telling a certain judgement from an uncertain one, fixing the cause rather than the measurement, choosing the cheapest file that can hold the fix, and knowing when to stop. Do not use it to plan a rewrite, which belongs to decktalk-revise.
compatibility: This is judgement rather than a code table. Every code carries its own sentence, its location and often a fix, and `decktalk schema finding` prints the whole set.
metadata:
  ends_with: The smallest edit that removes the cause, and the command that failed run again on what changed.
---

# Read the finding

A finding is something DeckTalk measured and judged. An error is something DeckTalk could not run
through. They are different, and the exit code and the result tell them apart. Never edit the
project to make an error go away, and never raise a limit to make a finding go away.

## What a finding carries

A code to dispatch on, a sentence with the measured number in it, where it was judged, how certain
it is, and often a fix that can be applied as it stands. Read the sentence and the location before
touching anything, and never parse a printed table when the JSON is right there.

## The craft

1. **Tell the two apart.** A finding means the project can be fixed. A refused command line means
   your command was wrong. An error means DeckTalk itself could not run, and it names the file, the
   line and the next action: stop and say so.
2. **Fix every certain row. Ask about every uncertain one.** Certain means the measurement is exact.
   Uncertain means it is a risk a person has to look at, so show the frame and let the author decide.
3. **Read the rows that did not fail.** A row a check could not measure proves nothing and still
   leaves a run green. When every row is unmeasured, the page did not run at all, and that is the
   only thing worth fixing on that pass.
4. **Fix the cause, not the measurement.** A reveal the checker cannot see is a reveal a viewer
   cannot see. A phrase that does not resolve is a phrase the voice never said.
5. **Change the cheapest file that can hold the fix.** The page first, then the cues, then the
   project file, and the script last and only with the author's agreement, because a script change
   re-voices that section.
6. **Rerun only what failed**, on the sections that changed. Rerun the whole chain only when the fix
   changed the narration.
7. **Quote the row before and after.** The failing row from the first run beside the same row from
   the rerun is the whole evidence that the fix worked.
8. **Stop after three tries.** Report what you changed, what the row says now, and what you think the
   cause is. A fourth guess is worse than a question.

## Where a cause usually lives

- A reveal that landed early is usually an earlier effect still moving, not a phrase in the wrong
  place. Shorten the earlier entrance before touching the words.
- A reveal that landed late is usually a phrase too far into its sentence.
- A reveal that changed nothing is usually too small, the same brightness as what it covers, or a
  line that types rather than a shape that arrives.
- A phrase that will not resolve is usually a digit, a symbol or a possessive that the script writes
  differently from the cue.
- A cue nothing owns and an element nothing cues are the same mistake seen from the two sides, so
  read both counts together.
- A page that opens black is usually a scene number that does not match its section, or a first
  slide with nothing on it at the mount.
- A recording that stalls is usually the page waiting for something that never arrives.
- Maths that shows as red or as raw source is a typesetting refusal, and maths that shows as plain
  text means the typesetter never loaded.

## Rules

- Never pass a flag that hides a finding, and never lower a threshold to clear a row.
- Never exclude a cue from the reveal check except for a reveal that is small by design, and only
  after the author has seen a frame of it and answered.
- Never re-voice a section to fix something a page or a phrase can fix.
- Never start a run that spends from this skill. Hand the sections back, so the price is put in front
  of the author first.
- Never report a film as done while a certain finding stands.

## Hand off

Hand off back to **decktalk-build** once every certain row is clear, naming the sections you changed
and whether the narration changed. Hand off to **decktalk-slide** when the fix is a picture that has
to be redesigned, to **decktalk-cues** when it is a phrase, and to **decktalk-script** when it is a
sentence.
