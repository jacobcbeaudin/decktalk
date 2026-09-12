<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
A markdown script, plain HTML slides, and an ElevenLabs voice in. One mp4 out, where every reveal lands on the word that introduces it.</p>

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?color=2c1fea&label=pypi" alt="PyPI"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
<a href="https://docs.decktalk.app"><img src="https://img.shields.io/badge/docs-docs.decktalk.app-2c1fea" alt="docs"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-black" alt="MIT"></a>
</p>

<p align="center">For lesson videos, product walkthroughs, and pre-recorded talks: anything you would otherwise re-record every time the script changes.</p>

<br>

```console
$ uv tool install decktalk && decktalk setup
$ decktalk init my-lesson && cd my-lesson
$ decktalk build --silent                    # placeholder narration, no key needed
$ cp .env.example .env && decktalk build     # your ElevenLabs voice
```

That is a finished video: `build/out/my-lesson.mp4`. The scaffold is a working deck: three
HTML scenes and one slot for a clip of your own. Narration needs an ElevenLabs key and a
voice id in `.env`; any voice works, and a clone of your own is the point.

Status: 0.1, alpha. Python 3.12+. Developed on macOS; Linux and Windows run in CI.
`decktalk setup` downloads headless Chromium and ffmpeg once per machine.

## Why

A narrated deck is three things that drift: what you say, what is on screen, and when.
Recording tools pin "when" to a timeline, so every script edit means re-recording or
re-scrubbing.

DeckTalk has no timeline. Narration comes back with a timestamp for every word, so a
reveal is cued to a phrase, not a second. Change a sentence and only that section
re-renders. The script is the edit.

It started as the pipeline behind a hackathon film whose script kept changing until the
deadline. This is that pipeline, made general.

Remotion and Motion Canvas time visuals by frame, in code; DeckTalk times them by spoken
word, in prose. Descript edits a recording; DeckTalk never makes one. Synthesia and HeyGen
render an avatar; DeckTalk renders your slides.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Write a script; your voice reads it with a time for every word; slides reveal on the words in Chromium; ffmpeg cuts one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

A cue names a phrase, and a slide names its cue:

```json
{ "step": "3.1eq", "on": "equation", "offset": 0.2 }
```

```html
<div data-cue="3.1eq" data-tex="\frac{d}{dx}\,x^2 = 2x">d/dx x² = 2x</div>
```

Slides are plain HTML you style yourself. One `decktalk-runtime.js` file tells a page
which step to show and when. Open a page in a browser to review it, freeze any step, or
screenshot every step.

## What you get

- **Frame-exact cuts.** Each recording carries a visible mark at the moment narration starts, so the cut is measured from the frames, not from a timer.
- **Cached narration.** Sections are hashed by text. Edits re-synthesize only what changed.
- **A soundscape.** Underscore ducked under speech, ambience beds, effects on cues, broadcast loudness. All optional.
- **Offline drafts.** `--silent` renders the whole film with placeholder narration and estimated word times.
- **Verified output.** Black or truncated recordings are caught before assembly; a cue phrase that cannot be found stops the build; `verify` proves each cue moved pixels.
- **Nothing to install by hand.** Chromium and ffmpeg arrive through Python packages. `decktalk setup` fetches both.

## Straight answers

- **Cost.** A ten-minute narration is roughly 9,000 characters, inside ElevenLabs' smallest paid tier. Sections are cached by text, so you pay per sentence changed, not per build.
- **Lock-in.** ElevenLabs is the only provider today because it returns word timestamps. The provider is one file; anything that returns word times can be added. `--silent` needs no provider at all.
- **Why not screen-record?** Because you will re-record it. Here a script edit rebuilds one section in the time it takes to synthesize one sentence.
- **Voice quality.** It is your own voice, cloned. Stability, similarity and speed are settings in `decktalk.toml`.
- **Generated music.** Off unless you configure it. Bring your own files or generate them.

## Docs

Everything else is at **[docs.decktalk.app](https://docs.decktalk.app)**: the project file, the page
contract, the CLI, configuration, and the Python API. To hack on DeckTalk itself, see
[CONTRIBUTING.md](CONTRIBUTING.md).

<p align="center">MIT. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
