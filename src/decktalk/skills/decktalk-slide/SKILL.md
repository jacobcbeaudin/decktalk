---
name: decktalk-slide
description: Write or change a DeckTalk deck page so that every reveal is visible, cued, described and legible. Use when a narrated video needs a new slide or scene, when maths or code has to appear on screen, when a picture must be added to an existing section, or when a reveal is too small, overlapping or off frame. It covers the four moments an element has, the closed style words, describing a reveal for the transcript, the change floor a check measures against, the caption band, and the frame the recorder captures. Do not use it for what the voice says, which belongs to decktalk-script.
compatibility: This is craft knowledge. The attribute table itself is published by `decktalk schema page`, and a check that looks at pictures needs a browser, which DeckTalk fetches the first time a command needs one.
metadata:
  ends_with: Markup that obeys the page contract, with every reveal large enough to be seen and described for a reader.
---

# Write the picture for the words

A deck page is one HTML file under `deck/`. A `[data-scene]` wrapper is one scene, each
`<template data-slide>` inside it is one slide, and the runtime clones a slide when it mounts. Write
the pictures for words that already exist, in markup rather than in code.

## The grammar

An element has four moments, and each one is the local name of a cue: it arrives, it steps back, it
comes to the front, and it leaves. The runtime qualifies a local name with the slide it is written
in, so `data-in="expand"` inside `<template data-slide="4.1">` is the cue `4.1:expand` that
`cues.json` gives a spoken phrase. Everything else is either how a moment looks, which is a closed
word, or what a moment means, which is a sentence for the transcript. **No attribute ever writes a
second**, because `cues.json` owns seconds.

`decktalk schema page` prints every attribute with its values, its default, its range and the
finding it raises. Read it rather than guessing, and read
`references/slide-patterns.md` for the shapes that work.

## The craft

1. **Start from the simplest page that works.** Copy a pattern, or the deck page the project was
   created with. Write no render function and no handler unless the author asks for one by name, and
   never start from a page that draws with a frame clock, a canvas or a hand-written colour ramp.
2. **Give the scene the number its section names, and the slide an id its cues can be local to.**
   A cue only a handler serves is listed in `data-owns`, and nothing else is.
3. **Cue every element that should wait.** An element with no moment is on screen from the mount,
   which puts half the slide up before the voice arrives.
4. **Describe every cued element.** The transcript is written from those phrases, and a reveal
   without one is silent to a reader who cannot see it. A class change needs its own sentence beside
   it, and a departure that means something gets a sentence of its own.
5. **Make every reveal a filled shape.** A frame is compared on brightness alone, so a reveal has to
   change about 0.3 percent of the frame, which is roughly an eighty by eighty solid block, and it
   has to differ in brightness rather than only in colour. Text on white is usually too thin. A
   colour change of the same brightness counts as nothing.
6. **Keep a slide to four reveals.** Start a new slide for the next part of a derivation.
7. **Keep the picture inside the frame.** The stage is 1920 by 1080. Body text is 36 pixels or
   larger. The bottom fifteen percent is the caption band, so keep every cued element out of it.
   Scope every class name to its scene so two scenes cannot collide.
8. **Write a readable fallback under every equation.** The element's own text is what shows if the
   typesetter never loads, so it is correct mathematics with its own brackets, and it reads well out
   loud.
9. **Look at the slide.** Freeze each slide at each of its cues, open the pictures, and check for
   unrendered maths, overlapping elements, text running off the frame and text too small to read.

## Rules

- Never raise a limit, never exclude a cue from the reveal check, and never pass a flag that makes a
  check go green. A check that cannot see a reveal is telling you a viewer cannot either.
- Never load a font, a stylesheet, a highlighter or a typesetter from a network address. A recording
  must not depend on the network. Copy the asset into `deck/`.
- Never draw a product interface in HTML. Use a real screenshot, from a demo account, at twice the
  stage scale.
- A page edit changes the picture and not the words, so it costs nothing to re-voice. A script edit
  costs a take.

## Gotchas

- A code line sits on one physical line, because the element's own indentation is shown.
- A typed line reads as no change. Fade a whole container in on the cue instead.
- A staggered container spreads one cue over its children, and the whole spread has to stay under
  half a second or the cue can no longer be measured at all.
- A page that throws leaves every reveal unmeasured rather than failing one of them, so read the
  page's own warnings before believing a green run.

## Hand off

Hand off to **decktalk-cues** once every element that should wait carries a moment, so each one gets
its phrase. Tell it the new or changed cue ids, the section each belongs to, and the sentence each
reveal should land on. Hand off to **decktalk-fix** instead when a check reports a finding whose
cause is not a slide you just wrote.
