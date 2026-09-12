<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
DeckTalk turns a markdown script and plain HTML slides into one narrated mp4 in which every reveal lands on the word that introduces it. Change a sentence and only that section renders again. The script is the edit.</p>

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?color=2c1fea&label=pypi" alt="PyPI"></a>
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/pyversions/decktalk?color=2c1fea" alt="Python versions"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
<a href="https://docs.decktalk.app"><img src="https://img.shields.io/badge/docs-docs.decktalk.app-2c1fea" alt="docs"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-black" alt="Apache-2.0"></a>
</p>

## The script is the edit

Lesson videos, product walkthroughs, recorded talks: anything you would otherwise
re-record every time the script changes. A narrated deck is three things that drift
apart. There is what you say, what is on screen, and when each thing appears. Recording
tools pin the timing to a timeline, so every edit to the script means re-recording or
re-scrubbing.

DeckTalk has no timeline. The narration comes back with a timestamp for every word, so a
reveal follows a phrase rather than a second. When you change a sentence, only that
section is synthesized and recorded again. The rest comes from the cache.

## Two minutes to a video

```console
$ uv tool install decktalk && decktalk setup   # Chromium and ffmpeg, once per machine
$ decktalk init my-lesson && cd my-lesson
$ decktalk build --silent                      # every stage, placeholder voice, no key
$ cp .env.example .env                         # add ELEVENLABS_API_KEY and a voice id
$ decktalk build                               # your voice, one mp4
```

The last command writes `build/out/<name>.mp4`, where the name comes from
`decktalk.toml` and defaults to the directory, so here `build/out/my-lesson.mp4`. The
scaffold is a working deck with three HTML scenes and one slot for a clip of your own,
which plays as a titled slate until you drop a file at `media/open.mp4`.

`uv` is a Python package manager, and `pipx install decktalk` works the same way.
ElevenLabs is the voice service, and a clone of your own voice is the point. The `.env`
file holds your key and voice id beside the project, and DeckTalk never prints either.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Write a script; your voice reads it with a time for every word; slides reveal on the words in Chromium; ffmpeg cuts one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

A project is four files that refer to each other by one number. The script has a section
for each heading:

```md
## 3. A curve and an equation
[Deck scene 3.]
Now a curve draws across the screen as I talk. And here is an equation: the derivative
of x squared is two x.
```

A cue names the phrase in that section that a visual lands on. Matching takes the first
occurrence and ignores case and punctuation:

```json
{ "sections": { "3": { "cues": [
  { "step": "3.1draw", "on": "curve draws" },
  { "step": "3.1eq",   "on": "equation", "offset": 0.2 } ] } } }
```

A slide is plain HTML that names the cue it waits for. One file, `decktalk-runtime.js`,
tells the page which step to show and when:

```html
<script src="decktalk-runtime.js"></script>
<script>
DeckTalk.scene(3, { steps: [{ id: "3.1", render: () => `
  <path class="curve" pathLength="1" data-cue="3.1draw" data-fx="draw" d="…"/>
  <div data-cue="3.1eq" data-tex="\frac{d}{dx}\,x^2 = 2x">d/dx x² = 2x</div>` }] });
</script>
```

The project file ties the section to the page, and a section can also be a clip of your
own with its own audio:

```toml
[[section]]
number = 3
title = "A curve and an equation"
page = "deck/index.html"
```

You can open a page in a browser to review it, freeze any step with `?step=3.1`, or take
a screenshot of every step. If a cue phrase is not in the spoken words, the build stops
and names it. Write numbers as they are spoken, so "two x" rather than `2x`.

## Why the cuts are exact

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-dark.svg">
  <img alt="A strip of recorded frames opens magenta while the page is covered. The first clean frame is narration t=0, and the frames in which the curve draws and the number appears line up with the words curve and number." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-light.svg" width="100%">
</picture>

A browser does not start recording at a known instant, so DeckTalk never trusts a timer.
The page is covered in magenta until the narration clock starts, and the first clean
frame in the recording is t=0 by construction. Every cue is a spoken word measured from
that same origin, so the reveal fires at the right second and the cut lands on the frame
that matches the narration, on Linux, macOS, and Windows alike.

The `verify` command closes the loop on the finished mp4. For each cue it compares the
frame just before with the frames just after, and it compares that change with a quiet
span before the cue, so a slow camera push does not count. This is the scaffold, built
with a silent placeholder voice and checked cue by cue:

```text
check                 cue       at   chg %   ctl %  result
2:2.1a               2.09    14.56    0.47    0.00  changed
2:2.2               12.96    25.43    2.82    0.04  changed
3:3.1draw            0.80    29.90    0.38    0.00  changed
3:3.1eq              5.80    34.90    0.96    0.02  changed
```

## Highlights

- **A free dry run.** `decktalk build --silent` runs every stage, including cue matching, recording, and verification, with placeholder narration and no API key. A full silent build of the scaffold takes under a minute on a laptop.
- **Cached narration.** DeckTalk hashes each section by its text and re-synthesizes only the sections that changed. Rebuilding one section of the scaffold takes about half a minute.
- **A soundscape.** An underscore that ducks under speech, ambience beds, sound effects on cues, and broadcast loudness. Every part is optional, and you can bring your own files or generate them from a prompt.
- **Verified output.** DeckTalk catches a black, truncated, or unaligned recording before assembly, stops the build when it cannot find a cue phrase, and proves that each named cue changed the picture.

## Under the hood

```text
build = narrate -> beats -> record -> measure -> check -> assemble -> verify
```

Each stage is a function in `src/decktalk/stages/` that takes a `Project`, writes a typed
artifact under `build/`, and raises a `DeckTalkError` subclass on failure. The CLI only
prints tables. Two seams are meant for you. The page contract lives in
`decktalk-runtime.js`, and any HTML that honors it is a slide. The voice lives behind a
two-method protocol, and anything that returns audio with word times can take the place
of ElevenLabs.

The file formats are stable already: `decktalk.toml`, `cues.json`, the build artifacts,
and the page contract. The Python names may move before 1.0. Everything an agent needs to
drive DeckTalk sits on one page at
[docs.decktalk.app/reference/card](https://docs.decktalk.app/reference/card).

## Cost, lock-in, voice

- **Cost.** You need an ElevenLabs plan with API access. The free tier has the API, but its audio carries a watermark and a non-commercial license, so the Starter plan is the practical floor. A ten-minute narration is roughly 9,000 characters, which fits inside that plan's monthly allowance, and an edit costs only the sentences you changed.
- **Lock-in.** ElevenLabs is the only provider today because it returns word timestamps. The provider is one module, and `--silent` needs no provider at all.
- **Voice.** Any ElevenLabs voice id works, including a clone of your own. Stability, similarity, style, and speed are settings in `decktalk.toml`.

## Requirements

DeckTalk needs Python 3.12 or later and runs on Linux, macOS, and Windows. The `setup`
command downloads headless Chromium and ffmpeg once per machine, so there is nothing to
install by hand. It is one person's project, at an early version, with a full offline
build running on all three platforms in CI.

## Docs

The rest lives at **[docs.decktalk.app](https://docs.decktalk.app)**: a quickstart, a
walkthrough of your first deck, the project file, the page contract, the CLI, every
setting, and the Python API. To work on DeckTalk itself, start with
[CONTRIBUTING.md](CONTRIBUTING.md).

<p align="center">Apache-2.0. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
