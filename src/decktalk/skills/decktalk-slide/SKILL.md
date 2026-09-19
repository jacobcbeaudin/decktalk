---
name: decktalk-slide
description: Write or change a page, a slide, an equation, a code sample or a screenshot. Use when a page or a slide has to be added or changed.
---

# decktalk-slide

## What this skill does

Write or change a page under `deck/`. A slide is a `<template data-slide="N.M">` inside a
`<div data-scene="N">` wrapper, and every reveal is an attribute: `data-cue`, `data-reveal`,
`data-duration`, `data-tex` and `data-describe`. A reveal has to change about 0.3 percent of the
frame, compared on brightness alone, so reveal a filled shape rather than text on the page
background.

Read `AGENTS.md` at the project root first. A project is four files that agree: `script.md`,
`decktalk.toml`, `cues.json` and the pages under `deck/`, and the section number, the scene and the
cue id prefix carry the same number.

Never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green, and ask before
a voiced run, because it spends money. Build without voice with `decktalk build --no-voice`.

End on `decktalk preflight --json`. Until `cues.json` names them, each new reveal is reported as an
unknown cue and the command exits 1, and that list is what to hand to `decktalk-cues` for the phrase
each new reveal lands on.
