<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
DeckTalk turns a markdown script and plain HTML slides into one narrated video in which every reveal lands on the word that introduces it. Change a sentence and only that section renders again. The script is the edit.</p>

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?label=pypi&color=2c1fea" alt="PyPI"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-black" alt="Apache-2.0"></a>
</p>

<p align="center">
<a href="https://docs.decktalk.app/quickstart">Quickstart</a> ·
<a href="https://docs.decktalk.app">Docs</a> ·
<a href="https://docs.decktalk.app/reference/card">Reference card for agents</a> ·
<a href="https://docs.decktalk.app/changelog">Changelog</a>
</p>

## Two minutes to a first video

```console
$ uv tool install decktalk && decktalk setup   # Chromium, ffmpeg, and KaTeX, once per machine
$ decktalk init my-lesson && cd my-lesson
$ decktalk build --silent                      # every stage, placeholder voice, no key
$ cp .env.example .env                         # add ELEVENLABS_API_KEY and a voice id
$ decktalk build                               # your voice, one mp4
```

The silent build needs no account and takes under a minute on a laptop. The last command
writes `build/out/my-lesson.mp4`, with captions and chapter markers beside it. The
scaffold is a short lesson: a title, three labeled lines, a derivation that reveals line
by line, and a prompt before-and-after. `uv` is a Python package manager, and `pipx
install decktalk` works the same way. The `.env` file holds your ElevenLabs key and voice
id beside the project, and DeckTalk never prints either.

## The script is the edit

A narrated deck is three things that drift apart: what you say, what is on screen, and
when each thing appears. Recording tools pin the timing to a timeline, so every edit to
the script means re-recording or re-scrubbing. DeckTalk has no timeline. The narration
comes back with a timestamp for every word, so a reveal follows a phrase rather than a
second. Change a sentence and only that section is synthesized and recorded again.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Write a script; your voice reads it with a time for every word; slides reveal on the words in Chromium; ffmpeg cuts one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

A project is four files that refer to each other by one number. The script has a heading
per section, and the words under it are what the voice says:

```md
## 3. The derivative of x squared
[beat]
Start from the definition. And here is the answer: only two x is left.
```

A cue names the phrase in that section that a visual lands on:

```json
{ "sections": { "3": { "cues": [
  { "cue": "3.1a", "on": "definition" },
  { "cue": "3.1c", "on": "Only two x", "offset": 0.2 } ] } } }
```

A slide is plain HTML that names the cue it waits for. One file, `decktalk-runtime.js`,
tells the page which step to show and when, and KaTeX typesets the math:

```html
<div class="eq" data-cue="3.1a" data-display data-tex="\\frac{d}{dx}\\,x^2 = \\lim_{h \\to 0} \\frac{(x+h)^2 - x^2}{h}">…</div>
<div class="eq" data-cue="3.1c" data-display data-fx="pop" data-tex="\\frac{d}{dx}\\,x^2 = 2x">…</div>
```

The project file ties the section to the page, or to a clip of your own with its own
audio. If a cue phrase is not in the spoken words, the build stops and names it.
[Your first deck](https://docs.decktalk.app/guides/first-deck) writes one section from
scratch, and [Writing for the ear](https://docs.decktalk.app/guides/writing-for-the-ear)
is the craft: say it, then show it, and give every reveal a noun to land on.

## Why the cuts are exact

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-dark.svg">
  <img alt="A strip of recorded frames opens magenta while the page is covered. The first clean frame is narration t=0, and the frames in which the curve draws and the number appears line up with the words curve and number." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-light.svg" width="100%">
</picture>

A browser does not start recording at a known instant, so DeckTalk never trusts a timer.
The page is covered in magenta until the narration clock starts, and the first clean
frame in the recording is t=0 by construction, on Linux, macOS, and Windows alike. The
`verify` command then measures, in the finished video, how long after its word each
reveal began. This is the scaffold, built with a silent placeholder voice:

```text
check                 cue       at   chg %   ctl %   offset  result
2:2.1a               2.30    15.46    0.60    0.00    +20ms  changed
3:3.1a               9.74    44.82    0.42    0.00    +20ms  changed
3:3.1c              31.27    66.35    0.33    0.00    +10ms  changed
4:4.2reply          36.92   109.48    3.08    0.00    +40ms  changed
```

[How it works](https://docs.decktalk.app/concepts/how-it-works) has the whole chain,
from the word timestamps to the frame.

## Highlights

- **A free dry run.** `decktalk build --silent` runs every stage, including cue matching, recording, and verification, with placeholder narration and no API key.
- **Cached narration.** DeckTalk hashes each section by its text and re-synthesizes only the sections that changed.
- **Captions and chapters.** Every build writes SRT and VTT captions from the word timestamps and muxes a chapter per section into the mp4.
- **A soundscape.** An underscore that ducks under speech, ambience beds, sound effects on cues, and streaming loudness at -16 LUFS. Every part is optional, and you can bring your own files or generate them from a prompt.
- **Verified output.** DeckTalk catches a black, truncated, or unaligned recording before assembly, stops the build when it cannot find a cue phrase, and reports how late each reveal landed.

## Cost, lock-in, voice

- **Cost.** You need an ElevenLabs plan with API access. The free tier's audio carries a watermark and a non-commercial license, so the Starter plan is the practical floor. A ten-minute narration is roughly 9,000 characters, which fits inside that plan's monthly allowance, and an edit costs only the sentences you changed.
- **Lock-in.** ElevenLabs is the only speech provider today because it returns word timestamps. The provider is one module behind a two-method protocol, and `--silent` needs no provider at all.
- **Voice.** Any ElevenLabs voice id works, including a clone of your own. Stability, similarity, style, and speed are settings in `decktalk.toml`.

| Tool | Timing comes from | Slides are | Cost of editing one sentence |
|---|---|---|---|
| **DeckTalk** | the spoken words, one timestamp each | your HTML | one section re-synthesized and re-recorded |
| Remotion, Motion Canvas, Manim | frame numbers or seconds in code | code | you re-time by hand |
| Descript | a recording you made | your screen | you re-record the clip |
| Synthesia, HeyGen | the avatar's speech | their templates | you regenerate the video |

The [FAQ](https://docs.decktalk.app/help/faq) has the full comparison.

## Requirements

DeckTalk needs Python 3.12 or later and runs on Linux, macOS, and Windows. The `setup`
command downloads headless Chromium, ffmpeg, and KaTeX once per machine, so there is
nothing to install by hand. It is one person's project at an early version, with a full
offline build running on all three platforms in CI.

## Docs

Everything else lives at **[docs.decktalk.app](https://docs.decktalk.app)**: the
quickstart, your first deck, writing for the ear, slide recipes, the project file, the
page contract, the CLI, every setting, and the Python API. Agents can start from the
[one-page reference](https://docs.decktalk.app/reference/card). To work on DeckTalk
itself, start with [CONTRIBUTING.md](CONTRIBUTING.md).

<p align="center">Apache-2.0. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
