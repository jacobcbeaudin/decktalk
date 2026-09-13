<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-dark.svg">
  <img alt="A playhead moves along a spoken sentence, one tick per word. The slide reacts on exactly the right words." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/hero-light.svg" width="100%">
</picture>

<h1 align="center">DeckTalk</h1>

<p align="center"><b>Narrated presentations, cut to the word.</b><br>
DeckTalk turns a markdown script and plain HTML slides into one narrated video in which every reveal lands on the word that introduces it. Change a sentence, and only that section is voiced again.</p>

<p align="center">DeckTalk is for anyone who explains things with slides and a script: lecturers, course authors, developer advocates, and agents that write both.</p>

<!-- demo: the video waits for the founder's approval. GitHub strips a <video> tag, so do
     not paste one. Upload the mp4 through GitHub's editor, put the user-attachments URL it
     returns on its own line in place of the hero picture at the top, move the caption below
     under that line, and uncomment it. Then change "the DeckTalk demo" under the quickstart
     to "the demo video above".
<p align="center">DeckTalk built this video from the project that <code>decktalk init</code> writes, in a cloned voice. Turn the sound on, because every reveal lands on the word that introduces it.</p>
-->

```console
$ uv tool install decktalk
```

<p align="center">
<a href="https://pypi.org/project/decktalk/"><img src="https://img.shields.io/pypi/v/decktalk?style=flat-square&label=pypi&color=2c1fea" alt="PyPI"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/jacobcbeaudin/decktalk/ci.yml?style=flat-square&label=ci" alt="ci"></a>
<a href="https://github.com/jacobcbeaudin/decktalk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-black?style=flat-square&labelColor=0a0a0a&color=2c1fea" alt="Apache-2.0"></a>
</p>

<p align="center">
<a href="https://docs.decktalk.app/quickstart">Quickstart</a> ·
<a href="https://docs.decktalk.app">Docs</a> ·
<a href="https://docs.decktalk.app/requirements">Requirements and costs</a> ·
<a href="https://docs.decktalk.app/reference/card">Reference card for agents</a> ·
<a href="https://docs.decktalk.app/changelog">Changelog</a><br>
Agents can read <a href="https://docs.decktalk.app/llms.txt">llms.txt</a>.
</p>

## From install to a first video

```console
$ uv tool install decktalk && decktalk setup   # Chromium, ffmpeg, and KaTeX, once per machine
$ decktalk init my-lesson && cd my-lesson
$ decktalk build --silent                      # every stage, placeholder voice, no key
$ cp .env.example .env                         # add ELEVENLABS_API_KEY and a voice id
$ decktalk build                               # your voice, one mp4
```

The scaffold that `decktalk init` writes is the DeckTalk demo, so your voiced build
reproduces it in your own voice. The scaffold's music comes from `decktalk soundscape`,
which spends ElevenLabs credits, and the video is complete without it.

The silent build needs no account and takes a few minutes on a laptop, because the
recording runs in real time. The last command writes `build/out/my-lesson.mp4` with a
chapter per section, and SRT and VTT captions beside it. The demo has five sections. A
cold open performs its own sentence. The second section shows three files and a voice tied
by one id. A gradient descent lesson on a loss surface shows a learning rate that zigzags,
one that flies out, one that crawls, and one that settles at the minimum. The fourth
section changes one word in the third, and only the third section is voiced again. A close
ends on decktalk.app. `uv` is a Python package manager, and `pipx install decktalk` works
the same way. The `.env` file holds your ElevenLabs key and voice id beside the project,
and DeckTalk never prints either.

## Update your video the way you update a doc

DeckTalk builds the video from text you keep, so a changed sentence changes the video, and
nothing is recorded again by hand.

A narrated deck is three things that drift apart: what you say, what is on screen, and
when each thing appears. Recording tools pin the timing to a timeline, so every edit to
the script means recording again or scrubbing again. DeckTalk has no timeline. The
narration comes back with a timestamp for every word, so a reveal follows a phrase rather
than a second. Change a sentence, and only that section is voiced again. A plain build
records every section again in real time, and `decktalk build --only N` records only that
section.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark-stacked.svg">
  <source media="(max-width: 640px)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light-stacked.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-dark.svg">
  <img alt="A script goes in, the voice reads it with a time for every word, the slides reveal on those words in Chromium, and ffmpeg cuts one mp4." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/how-it-works-light.svg" width="100%">
</picture>

A project is three files you write, the script, the cues, and the page, and one the voice
returns, the words file with a time for every word. One cue id per reveal, such as `3.1eq`,
ties them together. The script has a heading per section, and the words under it are what
the voice says. A bracketed direction such as `[beat]` is a short pause and is not spoken:

```md
## 3. Gradient descent

[beat] Take a step, and the learning rate, eta, scales its length. [beat]
```

A cue names the phrase in that section that a visual lands on:

```json
{ "sections": { "3": { "cues": [
  { "cue": "3.1eq", "on": "learning rate" } ] } } }
```

A slide is plain HTML that names the cue it waits for. One file, `decktalk-runtime.js`,
tells the page which step to show and when, and KaTeX typesets the math:

```html
<span class="chip" data-cue="3.1eq" data-tex="\eta = 2">η = 2</span>
```

The project file ties the section to the page, or to a clip of your own with its own
audio. If a cue phrase is not in the spoken words, the build stops and names it.
[Your first deck](https://docs.decktalk.app/guides/first-deck) writes one section from
scratch, and [Writing for the ear](https://docs.decktalk.app/guides/writing-for-the-ear)
is the craft: say it, then show it, and give every reveal a word to land on.

## Why the cuts are exact

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-dark.svg">
  <img alt="A strip of recorded frames opens magenta while the page is covered. The first clean frame is narration t=0, and the frames in which the curve draws and the number appears line up with the words curve and number." src="https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/assets/alignment-light.svg" width="100%">
</picture>

A browser does not start recording at a known instant, so DeckTalk never trusts a timer.
The page is covered in magenta until the narration clock starts, and the first clean
frame in the recording is t=0 by construction, on Linux, macOS, and Windows alike. The
`decktalk verify` command then measures every reveal in the finished video. Its `offset`
column is the signed time in milliseconds from each cue's time, which is its word's start
plus the cue's `offset`, to the first frame of its reveal. The `a/v` column is the
`offset` minus the click's distance from the cued word's start, so it is zero when the
picture begins the cue's `offset` after the click. This is the scaffold, built with a
silent placeholder voice and checked with
`decktalk verify 1:1.1curve 1:1.1number 3:3.1eq 4:4.1s3`:

```text
check                 cue       at   chg %   ctl %   offset     a/v  result
1:1.1curve           1.18     1.18    0.19    0.00    -60ms   -62ms  changed
1:1.1number          3.09     3.09    1.18    0.14    -50ms   -53ms  changed
3:3.1eq             41.71    88.71    0.39    0.00    -30ms   -32ms  changed
4:4.1s3              6.20   125.36    0.34    0.00    -40ms   -34ms  changed
```

[How it works](https://docs.decktalk.app/concepts/how-it-works) has the whole chain,
from the word timestamps to the frame.

## Requirements and costs

DeckTalk needs Python 3.12 or later and runs on Linux, macOS, and Windows. The
`decktalk setup` command downloads headless Chromium into Playwright's cache, ffmpeg into
the Python environment, and KaTeX into a per-user cache, so there is nothing to install by
hand. On Linux it also installs Chromium's system libraries. That step asks for sudo, or
for su on a system without sudo, unless you run it as root. A silent build needs no
account. A voiced build needs an ElevenLabs API key and a voice id.

- **Cost.** Every ElevenLabs plan, including the free plan, can call the API. The free plan has no commercial license, requires "elevenlabs.io" or "11.ai" in the title of content you publish, and cannot use Voice Library voices through the API. A ten-minute narration is roughly 9,000 characters. An edit costs the characters of each section whose text changed, because the cache key is the whole section's text. A silent build after a voiced one empties the narration cache, so the next voiced build synthesizes every section again. The `decktalk soundscape` command calls the ElevenLabs sound and music endpoints and spends credits too.
- **Lock-in.** ElevenLabs is the built-in speech provider because it returns word timestamps. The provider is one module behind a two-method protocol, and `--silent` needs no provider at all.
- **Voice.** Any ElevenLabs voice id works, including a clone of your own. Stability, similarity, style, and speed are settings in `decktalk.toml`.

[Requirements and costs](https://docs.decktalk.app/requirements) lists which commands
spend credits and where every download goes.

## How it compares

| Tool | Timing comes from | Slides are | Cost of editing one sentence |
|---|---|---|---|
| **DeckTalk** | the spoken words, one timestamp each | your HTML | one section voiced again, and `build --only N` records just that section |
| Remotion, Motion Canvas | frame numbers or seconds in code, by default | code | you re-time by hand and re-render |
| Manim | seconds in code, or bookmarks in the narration with manim-voiceover | code | with manim-voiceover, bookmarks in the text re-sync, and you still re-render the scene in Python |
| Descript | a recording you made | your screen | Overdub regenerates the words, and the screen recording does not move with them |
| Synthesia, HeyGen | the avatar's speech | their avatar and scenes | you regenerate the video |

The [FAQ](https://docs.decktalk.app/help/faq) has the full comparison. Beyond the cut,
every build adds the parts a recording tool leaves to you:

- **A free dry run.** `decktalk build --silent` runs every stage, including cue matching, recording, and verification, with placeholder narration and no API key.
- **Cached narration.** DeckTalk caches each section's narration by a hash of its text, voice, model, and settings.
- **Captions and chapters.** Every build writes SRT and VTT captions from the word timestamps and muxes a chapter per section into the mp4.
- **A soundscape.** The mix adds an underscore that ducks under speech, ambience beds, sound effects on cues, and a loudness target of -16 LUFS, which is common for spoken audio. Every part is optional, and you can bring your own files or generate them from a prompt.
- **Verified output.** DeckTalk catches a black, truncated, or unaligned recording before assembly and stops the build when it cannot find a cue phrase. The `decktalk verify` command reports how far from its cue time each reveal landed.

## Status and support

DeckTalk is alpha, and a 0.x minor release may break things. DeckTalk is maintained by one
person. Every push to main and every pull request runs the unit tests and a full offline
build on Linux, and the same build runs on macOS and Windows on demand. Report bugs and
ask questions in [Issues](https://github.com/jacobcbeaudin/decktalk/issues). Report a
vulnerability privately as [SECURITY.md](https://github.com/jacobcbeaudin/decktalk/blob/main/SECURITY.md)
describes, because private vulnerability reporting is enabled. The
[changelog](https://docs.decktalk.app/changelog) lists every release.

## Contributing

Everything else lives at **[docs.decktalk.app](https://docs.decktalk.app)**: the
quickstart, your first deck, writing for the ear, slide recipes, the project file, the
page contract, the CLI, every setting, and the Python API. Agents can start from the
[Reference card for agents](https://docs.decktalk.app/reference/card). To work on
DeckTalk itself, start with [CONTRIBUTING.md](https://github.com/jacobcbeaudin/decktalk/blob/main/CONTRIBUTING.md).

<p align="center">Apache-2.0. Made by <a href="https://github.com/jacobcbeaudin">Jacob Beaudin</a>.</p>
