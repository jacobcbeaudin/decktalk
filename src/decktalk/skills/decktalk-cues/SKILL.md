---
name: decktalk-cues
description: Choose the spoken phrase each reveal on a DeckTalk page waits for, by writing and repairing cues.json. Use when a script, a page or a reveal changes, when a cue is reported as unresolved or unknown, or when a picture appears early, late or not at all. It covers what makes a phrase match, which occurrence to take when a phrase repeats, when an offset is right and when it hides a real problem, and how far apart two reveals should sit. Do not use it to render the film, which belongs to decktalk-build.
compatibility: This is craft knowledge. The file's own keys are published by `decktalk schema project`, and resolving the phrases needs the narration, which `decktalk --help` names the command for.
metadata:
  ends_with: Every moment on the page matched to a phrase, in both directions, with the gaps a viewer can follow.
---

# Place the cues

`cues.json` says which spoken phrase each picture waits for. One row names a wire id and the phrase
it lands on, and the page carries the local half of that id on the element that reveals. The page
owns what a thing looks like and what it means. This file owns when it happens.

A wire id is the slide and the local name, joined by a colon: `data-in="expand"` inside
`<template data-slide="4.1">` is `4.1:expand`. Read `references/cue-phrases.md` for the shape of the
file and the patterns that work.

## The craft

1. **Match the ids in both directions.** A moment on the page with no row appears at the slide mount
   instead of on a word. A row the page no longer declares moves nothing. Both are reported, and both
   are fixed rather than silenced.
2. **Quote the phrase from the script.** It has to appear in that section word for word, in words and
   never in digits. Copy it rather than retyping it, because a possessive is part of its word and
   `project` does not match `project's`.
3. **Start on the word that names the thing that appears**, and prefer two or three words to one. A
   single common word such as "the", "one" or "it" matches early and lands on the wrong sentence.
4. **Name the occurrence when a phrase repeats.** The first match is usually too early. A phrase that
   occurs once is better than a phrase that needs an occurrence, so change the phrase first.
5. **Nudge with an offset, not with a rewrite.** A tenth or two later, for a reveal that points at
   something the voice has just named, so the eye follows the ear. A small negative offset, for text
   the viewer reads along with the voice. Never more than half a second: a reveal that needs more
   belongs on a different word.
6. **Measure the pacing.** A gap under two seconds means two reveals fight for one moment. A gap over
   twenty seconds means the picture is still while the voice talks. A section averaging under five
   seconds a cue is crammed. A lesson reads best at one reveal every ten to fifteen seconds, and a
   product demo can run faster. Show the numbers and let the author decide.
7. **Keep a cue off the very start of a section.** There is no earlier frame to compare it with, so
   nothing can be measured there. Move the phrase further into the sentence.
8. **Prove every reveal is visible.** A reveal the checker cannot see is a reveal a viewer cannot see
   either. Make it larger, dim what surrounds it, or move it.

## Rules

- Never exclude a cue from the reveal check to make a run green, unless the reveal is small by design
  and the author has seen a frame of it and agreed.
- Never pass a flag that allows an unresolved or unknown cue through.
- Change the phrase or the page before the script. A script change re-voices that section.
- Never run a build without a voice in a project that already holds paid takes.

## What never fixes a late reveal

- Raising a limit in the project file.
- Excluding the cue from the check.
- Changing how long a slide rests in the preview, which no recording reads.

A late reveal is fixed by changing the phrase, the offset, or the effect that is still moving when
the next reveal begins.

## Hand off

Hand off to **decktalk-build** once every phrase resolves and every reveal is seen, so the film can
be rendered. Tell it which sections changed, whether the project already holds paid takes, and which
tight gaps the author agreed to keep. Hand off to **decktalk-fix** when a command reports a finding
whose cause is not a phrase you just wrote, and to **decktalk-slide** when the fix is a picture.
