---
name: decktalk-slide
description: Write or change a DeckTalk deck page, a slide, an equation, a code sample or a screenshot so that every reveal is visible, cued and legible. Use when a narrated video needs a new slide or scene, when maths or code has to appear on screen, when a picture must be added to an existing section, or when a reveal is too small, overlapping or off frame. It covers the markup scene wrapper, data-cue, data-reveal, data-describe and data-tex, the KaTeX tags, the reveal budget, and reading the PNG files that screenshots writes. Do not use it for what the voice says, which belongs to decktalk-script.
compatibility: Requires the decktalk command on PATH and a browser installed by decktalk install. Steps 8 and 9 read PNG files, so a model that cannot read an image should ask the user to look at the frames instead.
metadata:
  ends_with: Markup that obeys the page contract, checked against the PNGs that `decktalk screenshots` writes.
---

# Write the slide

A DeckTalk page is one HTML file under `deck/` that declares scenes. A section of the script names a
page and a scene, a `[data-scene]` wrapper holding one `<template data-slide>` per slide is a whole
scene, and each element that carries a `data-cue` waits for its spoken word. Write the pictures for
the words that already exist, in markup rather than in code.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table. Exit code 0 means nothing was found, 1 means a finding with
`error` null, 2 means the command line was wrong, and 3 means DeckTalk could not run, in which case
stop and tell the user what `error.message` says.

## Steps

1. **Confirm the tools.** Run `decktalk doctor --json` and stop when any row of `doctor.components`
   has `ok` false and `optional` false. Run `decktalk install` when the browser or ffmpeg is missing.
2. **Start from the simplest page that works.** Copy the markup scene wrapper from
   `references/slide-patterns.md`, or from the deck page the project was created with. Never start
   from a page that draws with a frame clock, a canvas or a hand-written colour ramp, and write no
   `render` function, no `enter` handler, no `on` handler and no animation code unless the user asks
   for one by name.
3. **Declare the scene with the section's number.** A section that sets `scene = 3` needs
   `<div data-scene="3">` on the page it names, and every cue id on that scene begins with `3.`.
4. **Give each slide an id that prefixes its cues.** Slide `3.1` owns `3.1open` and `3.1result`
   without any list. Add `data-owns` only for a cue id that does not start with the slide's id.
5. **Cue every element that should wait, and describe it.** An element with no `data-cue` and no
   `data-delay` appears the moment its slide mounts, which puts half the slide on screen before the
   voice arrives. Give every cued element a `data-describe` sentence, because the transcript page is
   written from those sentences. Keep a slide to four reveals, start a new slide for the next part of
   a derivation, and choose the reveal effect from `references/slide-patterns.md`.
6. **Load KaTeX before the runtime** on any page that uses `data-tex`, by copying the two local tags
   from the deck page the project was created with. In markup a backslash is written once, and inside a
   `render` template literal it is written twice. Write a plain-text fallback that is correct
   mathematics with its own brackets, because it is what shows if KaTeX never loads.
7. **Keep the picture inside the frame.** The stage is 1920 by 1080. Keep body text at 36 pixels or
   larger. Keep every cued element out of the bottom fifteen percent of the frame, where the captions
   sit. Scope every class name to its scene so two scenes cannot collide.
8. **Look at the slide.** Run `decktalk screenshots --slide 3.1 --json`, then
   `decktalk screenshots --slide 3.1 --after 3.1open --json` for each cue, and read every path listed
   under `written`. Open each PNG and check for red TeX, plain-text mathematics, overlapping
   elements, text that runs off the frame, and text too small to read.
9. **Prove that each reveal is visible.** Run `decktalk preflight --json` and read
   `preflight.cues[]`. A row with `verdict.code` of `NO_CHANGE` or `THIN_CHANGE` is a reveal the
   checker could not see. Make the reveal larger, dim what surrounds it, or move it, and run the
   command again. A reveal that changes less than about 0.1 percent of the frame reads as no change,
   and one under about 0.3 percent reads as a thin change, so design for 0.3 percent or more. The
   comparison is on brightness alone, so a colour change of the same brightness counts as nothing.

## Rules

- Never raise a `[verify]` limit, never set `"verify": false` to silence a reveal, and never pass
  `--force`, `--exit-zero` or any `--allow-` flag to make a check pass.
- Never load a font, a stylesheet, a highlighter or KaTeX from a network address. A recording must
  not depend on the network. Copy the asset into `deck/`.
- Never draw a product user interface in HTML. Use a real screenshot.
- A page edit changes the picture and not the words, so it costs nothing to re-record. A script edit
  costs a take.

## Gotchas

- A broken page makes every preflight row skip with `NO_CATALOG` rather than fail, so read
  `preflight.cues[]` yourself. In markup there is nothing to escape. A backtick, a `${` or a single
  backslash only breaks a page inside a `render` template literal.
- A code line must sit on one physical line, because the element's own indentation is shown.
- A typewriter effect on a code line reads as no change to the checker. Fade a whole container in on
  the cue instead.
- The seconds in a slide's `data-preview` drive the browser preview only. They never change the
  video.
- `decktalk serve --open` serves the project over http and opens the first page, which is how you
  look at a slide in a real browser rather than in a PNG.
- A page that changed is only re-recorded for the sections that play it, so name every such section
  when the build is limited with `--only`.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off to **decktalk-cues** once every element that should wait carries a `data-cue`, so each id
gets its phrase in `cues.json`. Tell it the new or changed cue ids, the section each one belongs to,
and the sentence in `script.md` that each reveal should land on. Hand off to **decktalk-fix** instead
when `decktalk preflight --json` exits 1 and the cause is not a slide you just wrote.
