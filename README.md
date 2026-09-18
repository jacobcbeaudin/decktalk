<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
DeckTalk makes a narrated video from a markdown script and HTML slides.
Each reveal starts on the word that introduces it.
If you change a sentence, DeckTalk voices only that section again.</p>

<p align="center">DeckTalk is for lecturers, course authors, developer advocates, and the agents that help them.</p>

<!-- demo: the video waits for the founder's approval. GitHub strips a <video> tag, so do
     not paste one.
     1. Upload the mp4 through GitHub's editor.
     2. Put the user-attachments URL on its own line in place of the hero picture at the top.
     3. Move the caption below under that line, and uncomment it.
     4. Under "Make your first video", change "The scaffold is a lesson in nine sections, two of them optional clips" to
        "The scaffold is the project that made the demo video above".
<p align="center">DeckTalk built this video from the project that <code>decktalk init</code> writes, in a cloned voice. Turn the sound on, and each reveal starts on its word.</p>
-->

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

## Make your first video

You need Python 3.12 or later. These steps use `uv`, a Python package manager. If you use pipx, run `pipx install decktalk` in step 1 instead.

1. Install DeckTalk.

   ```console
   uv tool install decktalk
   ```

   uv ends with `Installed 1 executable: decktalk`.

2. Download Chromium, ffmpeg, and KaTeX. You do this one time per machine.

   ```console
   decktalk setup
   ```

   On Linux, this step asks for sudo. The last line is `setup complete`.

3. Create the scaffold, a complete example project.

   ```console
   decktalk init my-lesson
   ```

4. Go into the project.

   ```console
   cd my-lesson
   ```

5. Build the video without an account.

   ```console
   decktalk build --silent
   ```

   The build ends with `built` and the path of `build/out/my-lesson.mp4`. The build took 224.4 seconds on a MacBook Pro with Apple M5 Pro and 64 GB memory, because recording runs in real time.

6. Copy the example settings file.

   ```console
   cp .env.example .env
   ```

7. In `.env`, set your ElevenLabs API key and voice id. DeckTalk never prints the key.

8. Build the video with your voice.

   ```console
   decktalk build
   ```

   This build spends ElevenLabs credits for every section. It writes the video, SRT and VTT captions, and one chapter per section. [What spends credits](https://docs.decktalk.ai/requirements#what-spends-credits) lists the cost.

The scaffold is a lesson in nine sections, two of them optional clips. The [quickstart](https://docs.decktalk.ai/quickstart) shows the output of each step and [what the example video shows](https://docs.decktalk.ai/quickstart#what-the-example-video-shows).

## What you write

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="Four panels. Write shows a markdown script. Narrate runs narrate and beats, and shows a tick for every word of &quot;A bowl. A ball. One. Two, three.&quot; with 1.25 over &quot;bowl&quot;. Record runs record, measure, and check, and shows a slide where a bowl draws on and a ball steps down it. Assemble runs assemble and verify, and shows one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

You write four files. One cue id per reveal, such as `1.1bowl`, ties the script, the cues, and the page together.

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

`decktalk verify` then measures every reveal in the finished video. This sample comes from a silent build of the scaffold.

<!-- sample: decktalk verify 1:1.1bowl 3:3.4name 8:7.1checked, silent build of the scaffold (cue table only) -->
```text
check                 cue       at   chg %   ctl %   offset     a/v  result
1:1.1bowl            1.25     1.25    0.93    0.00    +30ms   +31ms  changed
3:3.4name           84.72   123.32    2.76    0.00     +0ms    -4ms  changed
8:7.1checked        10.03   238.31    0.83    0.00    +10ms    +3ms  changed
```

The offset column is the time from the cue time to the onset of the reveal, in milliseconds. [Verify](https://docs.decktalk.ai/reference/verify) defines every column and limit.

## Requirements and costs

- **Software.** DeckTalk needs Python 3.12 or later, on Linux, macOS, or Windows. `decktalk setup` downloads the rest.
- **Accounts.** A silent build needs no account. A voiced build needs an ElevenLabs API key and a voice id.
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

- **A free silent build.** Every stage runs with no key, and a click marks each word.
- **Cached narration.** DeckTalk voices a section again only when it changes.
- **Captions and chapters.** Every build writes SRT, VTT, and chapters.
- **A soundscape.** Add [an underscore, an ambience bed, and sound effects](https://docs.decktalk.ai/concepts/sound).
- **Loudness.** DeckTalk normalizes the mix to -16 LUFS.
- **Checked output.** `check` and `verify` catch bad recordings and late reveals.

## Status and support

DeckTalk is alpha, and a minor release can still break things. One person maintains it.

CI runs the unit tests and a full offline build on Linux for every push to `main` and every pull request. [CONTRIBUTING](https://github.com/jacobcbeaudin/decktalk/blob/main/CONTRIBUTING.md#what-ci-runs) lists the macOS and Windows runs.

- Report bugs and ask questions in [Issues](https://github.com/jacobcbeaudin/decktalk/issues).
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
