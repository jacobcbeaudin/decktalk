# DeckTalk

**Every picture lands on its word, and you edit the video like a doc.**

DeckTalk turns a markdown script and plain HTML slides into one narrated mp4, voiced by ElevenLabs in a stock voice or a clone of your own. It is for people who make technical tutorials, product demos and lessons, and for the coding agents that help them.

[![PyPI version](https://img.shields.io/pypi/v/decktalk?style=flat-square&label=pypi&labelColor=0a0a0a&color=f2b441)](https://pypi.org/project/decktalk/)
[![Python versions](https://img.shields.io/pypi/pyversions/decktalk?style=flat-square&label=python&labelColor=0a0a0a&color=f2b441)](https://pypi.org/project/decktalk/)
[![CI on main](https://img.shields.io/github/actions/workflow/status/jacobcbeaudin/decktalk/ci.yml?branch=main&style=flat-square&label=ci&labelColor=0a0a0a)](https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml?query=branch%3Amain)
[![License Apache-2.0](https://img.shields.io/pypi/l/decktalk?style=flat-square&label=license&labelColor=0a0a0a&color=f2b441)](https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE)

<!-- x-release-please-start-version -->

[![A frame of the film Halfway. On a dark city map, a white route runs from 14 Elm Street to a pin at Cafe Meridian, and a teal route runs on to 9 Harbour Road, each leg labelled 20 min.](https://raw.githubusercontent.com/jacobcbeaudin/decktalk/v0.5.0-rc2/assets/halfway-frame.webp)](https://decktalk.ai/films/halfway)

<!-- x-release-please-end -->

DeckTalk built the film [Halfway](https://decktalk.ai/films/halfway) from text files, and every reveal in it starts on its word. Watch it with the sound on, then build your own below.

## How it works

1. You write what the voice says in `script.md` and the pictures as plain HTML slides.
2. `cues.json` names the spoken phrase each picture waits for, so you never type a timestamp.
3. Your ElevenLabs voice reads the script, and every word comes back with a start and an end.
4. Headless Chromium records the slides against those times, and ffmpeg cuts one mp4 with captions and chapters beside it.
5. `decktalk verify` measures every reveal in the finished film against its word.

Edit one sentence and only its section is voiced and recorded again, so a fix costs a few cents of speech and not the whole film.

<!-- x-release-please-start-version -->

![The edit cycle. One sentence changes in script.md, and only its section is voiced and recorded again, while every other section comes back from the cache.](https://raw.githubusercontent.com/jacobcbeaudin/decktalk/v0.5.0-rc2/assets/edit-cycle.svg)

<!-- x-release-please-end -->

## Make your first video

<!-- x-release-please-start-version -->

```console
uv tool install decktalk==0.5.0-rc2
decktalk init my-lesson && cd my-lesson
decktalk build --no-voice
```

The first line pins 0.5.0-rc2, the release this page describes, and a plain `uv tool install decktalk` installs the latest final release.

The same block runs on Windows, and `pipx install decktalk==0.5.0-rc2` works in place of uv.

On Linux and macOS without uv, `curl -LsSf https://decktalk.ai/install.sh | DECKTALK_VERSION=0.5.0-rc2 sh` puts uv on the machine first, and uv brings its own Python. [Read the script](https://github.com/jacobcbeaudin/decktalk/blob/main/install.sh) before you run it.

<!-- x-release-please-end -->

`decktalk init` writes a starter of three sections that already builds. The build without voice needs no account and spends nothing. It makes the whole film, captions and chapters included, with a click on every word where the voice would be, so the cues, the cuts and the pacing all run as they will when voiced. It prints this line.

```text
       Built build/final/my-lesson.mp4, $0.00, nothing found
```

The first build downloads Chromium and ffmpeg, one time per machine, and says so as it goes. Recording runs in real time, so the build takes a little longer than the fifty seconds of film it makes.

To hear your own voice, copy `.env.example` to `.env`, fill in your ElevenLabs API key and voice id, and run `decktalk build --spend --max-cost 1`. `decktalk check` prices the run before anything is bought, and `decktalk storyboard` puts every slide at every cue on one page for a look first. The [quickstart](https://docs.decktalk.ai/quickstart) walks each step with its output.

## A short tour

### The four files you write

| File | What it holds |
|---|---|
| `script.md` | What the voice says, under one `## N. Title` heading per section. |
| `decktalk.toml` | The project file, which ties each section to a page or to a clip of your own. |
| `cues.json` | The spoken phrase each moment on the page waits for. |
| A page in `deck/` | The slides, as plain HTML with no JavaScript of your own. |

One wire id ties them together. In the starter, the slide `1.1` declares a moment called `title`, and `cues.json` says that moment waits for the words "This is DeckTalk".

```html
<template data-slide="1.1" data-hold="10" data-describe="the opening title and the count of files">
  <div class="title" data-in="title" data-describe="the title, This is DeckTalk">This is DeckTalk</div>
</template>
```

```json
{ "cue": "1.1:title", "on": "This is DeckTalk" }
```

`data-describe` names the picture for the transcript. No attribute on the page writes a second. [Your first deck](https://docs.decktalk.ai/guides/first-deck) writes one section across all four files, and [the page contract](https://docs.decktalk.ai/concepts/page-contract) lists every attribute.

### Using DeckTalk with an agent

The command line is the instruction set, settings and page attributes are the knobs, and the agent is the implementer.

`decktalk init` writes an `AGENTS.md` and six skills into the project. The skills live in `.agents/skills/`, which Codex, Cursor, Gemini CLI and most other agents read, and `.claude/skills` links to that folder for Claude Code. [Install the skills](https://docs.decktalk.ai/agents/install) lists every agent and the folders it reads.

Open your agent in the project and paste this.

```text
Read AGENTS.md, then make a three-section lesson on binary search.
Rehearse with decktalk build --no-voice, show me the storyboard,
and ask me before any run that spends money.
```

The agent reads `decktalk --help` for the commands, `decktalk schema build` for one command's flags and result, and `decktalk config explain KEY` for one setting. Every command prints one JSON object under `--json`, and `--events` streams progress as JSON lines. Exit 0 means nothing was found, 1 a finding, 2 a refused command line and 3 that DeckTalk could not run. The [reference card](https://docs.decktalk.ai/reference/card) puts the whole contract on one page.

Without a terminal, a voiced build refuses to spend unless `--spend` is passed, so an agent left alone cannot buy speech by accident. This is the whole answer from `decktalk --json build` in the starter, which exits 2.

```json
{
  "schema": 2,
  "ok": false,
  "findings": [],
  "error": {
    "code": "APPROVAL",
    "message": "voicing 3 sections costs up to $0.14, and no terminal is here to approve it.",
    "hint": "Run decktalk build --spend to approve that spend, or decktalk build --no-voice to finish with placeholder narration.",
    "location": null,
    "docs": "https://docs.decktalk.ai/reference/errors/APPROVAL"
  }
}
```

### Drive it from Python

The command line is the first client of a library. `decktalk.open` returns a `Project` with a method for each of the six stages and for `build`, `check`, `status`, `words`, `storyboard`, `clip` and `serve`. Each method returns the same object its command prints, and nothing in the library prints.

```python
import decktalk

project = decktalk.open("my-lesson")
project.events.subscribe(lambda event: print(event.event, event.run))

result = project.build(voice=decktalk.Voicing.PLACEHOLDER)
print(result.ok, result.film)
for finding in result.findings:
    print(finding.code.name, finding.message, finding.location.where)
```

A voiced call takes `voice=decktalk.Voicing.PAID` and `max_cost`, and the library refuses before the first paid request when the estimate is above the cap. [The Python API](https://docs.decktalk.ai/reference/python-api) documents every call.

### What a build writes

A build writes everything under `build/`, and `decktalk status` reads it back. The film lands in `build/final/` with what goes with it.

```text
my-lesson.mp4              the film
my-lesson.srt, .vtt        captions
my-lesson.chapters.txt     one chapter per section
my-lesson-transcript.html  a transcript page
my-lesson-poster.png       a poster frame
cuts.json                  where every section sits in the film
```

The takes are kept under `build/narrate/`, named by their content hash, and every run's events are kept under `build/events/`. [Build artifacts](https://docs.decktalk.ai/reference/artifacts) lists every path.

## Requirements and costs

- **Software.** The one-line installer runs on Linux and macOS and brings its own Python. On Windows, install with uv or pipx, which needs Python 3.12 or later. The first build downloads Chromium and ffmpeg, and `decktalk install` fetches them up front for a Docker layer, a CI cache or an offline machine.
- **Accounts.** A build without voice needs no account. A voiced build needs an ElevenLabs API key and a voice id, and DeckTalk never prints the key.
- **Cost.** Only a voiced build spends money, at your own ElevenLabs rate per character. A section whose text and voice have not changed is not voiced again.

[Requirements and costs](https://docs.decktalk.ai/requirements) lists every download and every command that spends credits.

## Learn more

The docs are at [docs.decktalk.ai](https://docs.decktalk.ai), and agents can read them whole at [llms.txt](https://docs.decktalk.ai/llms.txt).

- [How it works](https://docs.decktalk.ai/concepts/how-it-works) walks the six stages and why they run in that order.
- [Cues](https://docs.decktalk.ai/concepts/cues) explains how a phrase becomes a second.
- [Sound](https://docs.decktalk.ai/concepts/sound) adds music, an ambience bed and effects on a cue.
- [The CLI reference](https://docs.decktalk.ai/reference/cli) lists every command and flag.
- [Configuration](https://docs.decktalk.ai/reference/configuration) lists every setting with its range and default.
- [Verify](https://docs.decktalk.ai/reference/verify) defines every measurement the finished film is judged by.
- [The FAQ](https://docs.decktalk.ai/help/faq#how-is-this-different-from-the-other-tools) compares DeckTalk with Remotion, Manim, Descript and Synthesia.
- [ARCHITECTURE.md](https://github.com/jacobcbeaudin/decktalk/blob/main/ARCHITECTURE.md) explains how the code is built.

## Status and support

DeckTalk is alpha, a minor release can still break things, and one person maintains it. CI runs every check on Linux, macOS and Windows. Every release is published from CI by trusted publishing, and every file on PyPI carries a provenance attestation.

- Report bugs, ask questions and show what you made in [Issues](https://github.com/jacobcbeaudin/decktalk/issues).
- Report a vulnerability privately, as [SECURITY.md](https://github.com/jacobcbeaudin/decktalk/blob/main/SECURITY.md) explains.
- Read what changed in the [changelog](https://github.com/jacobcbeaudin/decktalk/blob/main/CHANGELOG.md).
- Work on DeckTalk itself by starting with [CONTRIBUTING.md](https://github.com/jacobcbeaudin/decktalk/blob/main/CONTRIBUTING.md).

DeckTalk is licensed under Apache-2.0 and made by [Jacob Beaudin](https://github.com/jacobcbeaudin).
