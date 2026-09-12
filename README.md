<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
You write a markdown script and plain HTML slides. An ElevenLabs voice reads the script. DeckTalk produces one mp4 in which every reveal lands on the word that introduces it.</p>

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?color=2c1fea&label=pypi" alt="PyPI"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
<a href="https://docs.decktalk.app"><img src="https://img.shields.io/badge/docs-docs.decktalk.app-2c1fea" alt="docs"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-black" alt="Apache-2.0"></a>
</p>

<p align="center">It is built for lesson videos, product walkthroughs, and recorded talks. It suits anything you would otherwise re-record every time the script changes.</p>

<br>

```console
$ uv tool install decktalk && decktalk setup
$ decktalk init my-lesson && cd my-lesson
$ decktalk build --silent                    # placeholder narration, no key needed
$ cp .env.example .env && decktalk build     # your ElevenLabs voice
```

The last command writes a finished video to `build/out/my-lesson.mp4`. The scaffold is a
working deck with three HTML scenes and one slot for a clip of your own. Real narration
needs an ElevenLabs key and a voice id in `.env`. Any voice works. A clone of your own
voice is the point.

DeckTalk needs Python 3.12 or later. I develop it on macOS, and the test suite runs on
Linux, macOS, and Windows in CI. The `setup` command downloads headless Chromium and ffmpeg
once per machine.

## Why

A narrated deck is three things that drift apart: what you say, what is on screen, and
when each thing appears. Recording tools pin the timing to a timeline, so every edit to
the script means re-recording or re-scrubbing.

DeckTalk has no timeline. The narration comes back with a timestamp for every word, so a
reveal follows a phrase rather than a second. When you change a sentence, only that
section renders again. The script is the edit.

The pipeline began as the machinery behind a hackathon film whose script kept changing
until the deadline. This is that machinery, made general.

The nearest tools solve a different problem. Remotion and Motion Canvas time visuals by
frame, in code. DeckTalk times them by spoken word, in prose. Descript edits a recording,
and DeckTalk never makes one. Synthesia and HeyGen render an avatar, and DeckTalk renders
your slides.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Write a script; your voice reads it with a time for every word; slides reveal on the words in Chromium; ffmpeg cuts one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

A cue names a phrase in the script:

```json
{ "step": "3.1eq", "on": "equation", "offset": 0.2 }
```

A slide names the cue it waits for:

```html
<div data-cue="3.1eq" data-tex="\frac{d}{dx}\,x^2 = 2x">d/dx x² = 2x</div>
```

Slides are plain HTML pages that you style yourself. A single file, `decktalk-runtime.js`,
tells a page which step to show and when. You can open a page in a browser to review it,
freeze any step, or take a screenshot of every step.

## What you get

- **Frame-exact cuts.** Each recording carries a visible mark at the moment the narration starts. DeckTalk measures the cut from the frames rather than from a timer.
- **Cached narration.** DeckTalk hashes each section by its text and re-synthesizes only the sections that changed.
- **A soundscape.** You can add an underscore that ducks under speech, ambience beds, sound effects on cues, and broadcast loudness. Every part of it is optional.
- **Offline drafts.** The `--silent` flag renders the whole film with placeholder narration and estimated word times.
- **Verified output.** DeckTalk catches a black or truncated recording before assembly. It stops the build when it cannot find a cue phrase. The `verify` command proves that each cue changed the picture.
- **Nothing to install by hand.** Chromium and ffmpeg arrive through Python packages, and `decktalk setup` fetches both.

## Straight answers

- **What does it cost?** A ten-minute narration is roughly 9,000 characters, which fits inside the smallest paid ElevenLabs tier. Because DeckTalk caches sections by text, you pay for the sentences you change rather than for every build.
- **Am I locked into ElevenLabs?** It is the only provider today because it returns word timestamps. The provider lives in one file, and anything that returns word times can take its place. The `--silent` flag needs no provider at all.
- **Why not record the screen?** You would record it again after every edit. Here, an edit rebuilds one section in the time it takes to synthesize one sentence.
- **How does the voice sound?** It sounds like you, because it is your voice, cloned. Stability, similarity, and speed are settings in `decktalk.toml`.
- **Do I have to use generated music?** No. The soundscape stays off until you configure it, and you can bring your own files.

## Docs

The rest lives at **[docs.decktalk.app](https://docs.decktalk.app)**: the project file, the
page contract, the CLI, configuration, and the Python API. If you want to work on DeckTalk
itself, start with [CONTRIBUTING.md](CONTRIBUTING.md).

<p align="center">Apache-2.0. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
