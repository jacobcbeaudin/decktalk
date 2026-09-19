<h1 align="center">DeckTalk</h1>

<p align="center"><b>Every picture lands on its word, and you edit the video like a doc.</b><br>
Before this, one wrong word meant editing, rendering and recording the whole thing again.</p>

<p align="center"><a href="https://decktalk.ai/#watch"><img src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/site/media/decktalk-demo-poster.jpg" alt="The first frame of the demo film. A blue ball rests in a bowl above three boxes named one, two, and three, and under each box is the second at which its word was spoken." width="100%"></a><br>
DeckTalk built this film from text files, and every reveal in it starts on its word. <a href="https://decktalk.ai/#watch">Watch it with the sound on at decktalk.ai</a>.</p>

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?style=flat-square&label=pypi&color=2c1fea" alt="PyPI"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/jacobcbeaudin/decktalk/ci.yml?style=flat-square&label=ci" alt="ci"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-black?style=flat-square&labelColor=0a0a0a&color=2c1fea" alt="Apache-2.0"></a>
</p>

<p align="center">
<a href="https://docs.decktalk.ai/quickstart">Quickstart</a> ·
<a href="https://docs.decktalk.ai">Docs</a> ·
<a href="https://docs.decktalk.ai/requirements">Requirements and costs</a> ·
<a href="https://docs.decktalk.ai/reference/card">Reference card for agents</a> ·
<a href="https://docs.decktalk.ai/changelog">Changelog</a><br>
Agents can read <a href="https://docs.decktalk.ai/llms.txt">llms.txt</a>.
</p>

## What it is

DeckTalk makes a narrated video from a markdown script and plain HTML slides. Your cloned voice reads the script and comes back with a time for every word, so each reveal starts on the word that introduces it. The script and the slides are text you keep in a repository, so you change a sentence and build again, the way you would change a doc or a codebase.

Four things are different from a video editor, a slide tool, or an animation library.

- **Every reveal is cut to the spoken word.** A cue names a phrase, not a second, so a reveal follows the narration wherever the voice puts it. `decktalk verify` measures every reveal in the finished video, and in the [sample below](#why-the-cuts-are-exact) each one lands within one frame of its word.
- **A changed sentence voices only its own section again.** DeckTalk caches every section's narration by its text and voice settings, so an edit spends credits only on the sections that changed. `build --only N` records only the section you name.
- **The whole video is text.** You write four files, and an agent can write them too. There is no timeline to drag and no project file a person cannot read.
- **Your coding agent already knows how to drive it.** `decktalk init` installs six skills into the project, one each for the script, the slides, the cues, the build, the fixes and a revision, so an agent writes the files, reads the JSON every command prints, and fixes what it finds. [The skills](https://docs.decktalk.ai/agents/skills) lists them.

## Who it is for

- **Technical tutorials.** A viewer watches a walkthrough whose commands and screenshots match the version she just installed. When the library changes, you change the sentence and build again.
- **Product demos.** A viewer sees the feature narrated over the screen it runs on, with every callout landing on the word that names it.
- **Lessons.** A viewer follows an explanation in which each idea appears as it is spoken, so nothing is read ahead.

Internal presentations and estimation walkthroughs run the same pipeline. Neither has a shipped example yet.

## Make your first video

You need Python 3.12 or later. These commands use `uv`, a Python package manager. If you use pipx, run `pipx install decktalk` instead of the first line.

```console
uv tool install decktalk
decktalk install
decktalk init my-lesson && cd my-lesson
decktalk build --no-voice
```

`decktalk install` downloads Chromium and ffmpeg one time per machine, and on Linux it asks for sudo. `decktalk init` writes the starter, a working three-section project with one equation, and nothing in it has to be deleted first. The build without voice needs no account, ends with `built`, and prints the path of `build/out/my-lesson.mp4`. Recording runs in real time, so the build takes about a minute and makes a 44 second video.

To hear it in your own voice, copy `.env.example` to `.env`, set your ElevenLabs API key and voice id, and run `decktalk build`. DeckTalk never prints the key. A voiced build of the starter sends about 420 characters, and `decktalk narrate --dry-run` prices that run before it starts. Every build writes the mp4, SRT and VTT captions, a chapter per section and a transcript page. [What spends credits](https://docs.decktalk.ai/requirements#what-spends-credits) lists the cost of every command.

The [quickstart](https://docs.decktalk.ai/quickstart) shows the output of each step and [what the starter shows](https://docs.decktalk.ai/quickstart#what-the-starter-shows). If you try DeckTalk on one section of something you teach, tell me in [Issues](https://github.com/jacobcbeaudin/decktalk/issues) what stopped you.

## What you write

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Four panels. Write shows a markdown script. Narrate runs narrate and align, and shows a tick for every word of &quot;A bowl. A ball. One. Two, three.&quot; with 1.25 over &quot;bowl&quot;. Record runs record, and shows a slide where a bowl draws on and a ball steps down it. Assemble runs assemble and verify, and shows one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

You write four files. One cue id per reveal, such as `1.1script`, ties the script, the cues, and the page together.

| File | What it holds |
|---|---|
| `script.md` | What the voice says, under one `## N. Title` heading per section. |
| `decktalk.toml` | The project file. It ties each section to a page or to a clip of your own. |
| `cues.json` | The phrase that each reveal starts on. |
| A page in `deck/` | The slides, as plain HTML. |

The `narrate` stage writes a words file with the start and end of every spoken word. If a cue phrase is not in the spoken words, the build stops and names the cue. [Your first deck](https://docs.decktalk.ai/guides/first-deck) writes one section in all four files.

## Why the cuts are exact

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-dark.svg">
  <img alt="A strip of recorded frames. Three magenta cover frames come first, and a line marks t=0 at the first clean frame. Under the strip, the narration &quot;A bowl. A ball. Watch it step down&quot; starts at t=0. Dashed leads join &quot;bowl&quot; and &quot;ball&quot; to the outlined frames where the bowl and then the ball appear." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-light.svg" width="100%">
</picture>

A browser does not start recording at a known time, so DeckTalk does not use a timer. The recorder covers the page in magenta until the narration starts. The first frame without magenta is narration t=0, on Linux, macOS, and Windows.

`decktalk verify` then measures every reveal in the finished video. This sample comes from a build without voice of the starter, and one frame lasts 40 ms.

<!-- sample: decktalk verify 1:1.1title 2:2.1code 3:3.1make, build without voice of the starter (cue table only) -->
```text
check                 cue       at   chg %   ctl %   offset     a/v  result
1:1.1title           0.70     0.70    1.87    0.00    +20ms   +16ms  changed
2:2.1code            8.93    23.41   10.79    0.00    -10ms   -34ms  changed
3:3.1make            8.21    38.13   10.85    0.00    -10ms    +8ms  changed
```

The offset column is the time from the cue time to the onset of the reveal, in milliseconds. [Verify](https://docs.decktalk.ai/reference/verify) defines every column and limit.

## Requirements and costs

- **Software.** DeckTalk needs Python 3.12 or later, on Linux, macOS, or Windows. `decktalk install` downloads the rest.
- **Accounts.** A build without voice needs no account. A voiced build needs an ElevenLabs API key and a voice id.
- **Cost.** Every ElevenLabs plan can call the API. The free plan has limits for a video you publish.

[Requirements and costs](https://docs.decktalk.ai/requirements) lists every download and every command that spends credits.

## How it compares

| Tool | Timing comes from | Slides are | After you edit one sentence |
|---|---|---|---|
| **DeckTalk** | the spoken words, one time per word | your HTML | DeckTalk voices one section again. `build --only N` records only that section. |
| Remotion, Motion Canvas | frame numbers or seconds in code, by default | code | You time the change again by hand and render again. |
| Manim | seconds in code, or bookmarks in the narration with manim-voiceover | code | With manim-voiceover, bookmarks follow the new text. You still render the scene again in Python. |
| Descript | a recording you made | your screen | Overdub voices the new words. The screen recording does not move with them. |
| Synthesia, HeyGen | the avatar's speech | their avatar and scenes | You generate the video again. |

The [FAQ](https://docs.decktalk.ai/help/faq#how-is-this-different-from-the-other-tools) has the full comparison. Every build also gives you these parts:

- **A free build without voice.** Every stage runs with no key, and a click marks each word.
- **Cached narration.** DeckTalk voices a section again only when it changes, and keeps a recording whose page, words and cues have not moved.
- **Captions, chapters and a transcript.** Every build writes SRT, VTT, chapters and a transcript page.
- **A soundscape.** Add [music, an ambience bed, and sound effects](https://docs.decktalk.ai/concepts/sound).
- **Loudness.** DeckTalk normalizes the mix to -16 LUFS.
- **Checked output.** `record` and `verify` catch bad recordings and late reveals.

## Status and support

DeckTalk is alpha, and a minor release can still break things. One person maintains it.

CI runs the unit tests and a full offline build on Linux for every push to `main` and every pull request. [CONTRIBUTING](https://github.com/jacobcbeaudin/decktalk/blob/main/CONTRIBUTING.md#what-ci-runs) lists the macOS and Windows runs.

- Report bugs and ask questions in [Issues](https://github.com/jacobcbeaudin/decktalk/issues).
- Show what you made in [Discussions](https://github.com/jacobcbeaudin/decktalk/discussions).
- Report a vulnerability privately. [SECURITY.md](https://github.com/jacobcbeaudin/decktalk/blob/main/SECURITY.md) explains how.
- The [changelog](https://docs.decktalk.ai/changelog) lists every release.

## Documentation

The docs are at **[docs.decktalk.ai](https://docs.decktalk.ai)**.

- **Install and build a first video:** [Quickstart](https://docs.decktalk.ai/quickstart)
- **Write your own section:** [Your first deck](https://docs.decktalk.ai/guides/first-deck)
- **Give an agent the whole contract:** [Reference card for agents](https://docs.decktalk.ai/reference/card)

## Contributing

To work on DeckTalk itself, start with [CONTRIBUTING.md](https://github.com/jacobcbeaudin/decktalk/blob/main/CONTRIBUTING.md).

<p align="center">Apache-2.0. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
