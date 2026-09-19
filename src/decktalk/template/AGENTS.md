# __TITLE__

A DeckTalk project: four files agree, and `decktalk build` turns them into a narrated video.

- `script.md` is what the voice says. `## N. Title` starts section N.
- `decktalk.toml` lists one `[[section]]` per script section, each naming a page and a scene.
- `cues.json` says which spoken phrase each visual lands on. A cue id starts with its slide id.
- `deck/` holds the pages. A slide is a `<template data-slide>` inside a `[data-scene]` wrapper, and reveals are attributes: `data-cue`, `data-reveal`, `data-tex`.
- The section number, the scene and the cue id prefix are the same number. Keep the three in agreement.

Build without voice first with `decktalk build --no-voice`, which spends nothing and needs no key. Ask before a voiced run, and never pass `--force`, `--exit-zero` or an `--allow-*` flag to make a check go green. Every command takes `--json` and prints one envelope on stdout. The six DeckTalk skills are in `.agents/skills/`.
