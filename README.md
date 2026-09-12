# DeckTalk

Narrated presentation videos, cut to the word. [decktalk.app](https://decktalk.app)

You write a script in markdown. ElevenLabs reads it in your voice and returns a
timestamp for every word. Your slides are plain HTML pages; each reveal names the
spoken phrase it should land on. DeckTalk records the pages with headless Chromium,
cuts every section to the narration frame-exactly, mixes an optional underscore and
ambience, normalizes loudness, and publishes one mp4. Change a sentence and only that
section re-renders.

```
script.md ───narrate──▶ build/audio/NN-slug.mp3 + .words.json, narration.mp3, timeline.json  (ElevenLabs)
cues.json ───beats────▶ build/audio/beats.json          (which second each visual lands on)
deck/*.html ─record───▶ build/rec/NN-scene.webm         (Chromium, driven by ?beats=…)
             measure ─▶ narration t=0 found in each recording (magenta marker)
             assemble ▶ build/out/NN-section.mp4 ▶ build/out/<name>.mp4  (ffmpeg: cut, mix, loudnorm)
             verify ──▶ every section opens on a real frame; every cue lands
```

## Install

Everything is bundled. Playwright's Python package carries its own browser driver and
`static-ffmpeg` carries ffmpeg and ffprobe for your platform (a system ffmpeg on PATH is
used when present). Python 3.12+; no Node, no npm.

```console
$ uv tool install decktalk      # or: pipx install decktalk, or pip install decktalk
$ decktalk setup                # fetches headless Chromium (~100 MB) and ffmpeg, once per machine
$ decktalk doctor               # confirms both are usable
```

From a checkout: `uv tool install -e .` or `uv run decktalk …`.

## Make a presentation

```console
$ decktalk init my-lesson && cd my-lesson
$ cp .env.example .env          # ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
$ decktalk build                # narrate → beats → record → measure → check → assemble → verify
$ open build/out/my-lesson.mp4
```

`decktalk build --silent` renders with silent placeholder narration and estimated word
times, so you can iterate on the visuals with no key and no credits. The scaffold is a
working three-scene deck: run it as-is first, then edit.

A project is a directory:

| Path | What |
|---|---|
| `decktalk.toml` | The plan: one `[[section]]` per script section, pointing at a page and scene or at your own clip; voice settings; transitions; the mix; soundscape prompts; optional tool tuning. Reference: [docs/project-file.md](docs/project-file.md). |
| `script.md` | The narration. `## N. Title` sections are the cut points. `[Bracketed directions]` are not spoken. Write numbers and symbols the way you want them said. |
| `cues.json` | For each visual, the spoken phrase it lands on. `decktalk beats` reports any phrase it cannot find. |
| `deck/index.html` | Your slides. One HTML file, styled how you like, with `decktalk-runtime.js` included. Open it in a browser for an index of every scene and step; `?step=ID` freezes one. |
| `media/` | Your own clips, b-roll, the underscore markers. |
| `build/` | Everything generated. Git-ignored. |

### The page contract

`decktalk-runtime.js` is the only thing a page needs. It reads `?scene=N&beats=id@s,…&t0=S`
from the recorder and fires each cue id at the right second. You declare scenes and steps;
markup names its cue:

```html
<script src="decktalk-runtime.js"></script>
<script>
DeckTalk.scene(2, { name: "Three lines", camera: "push", steps: [
  { id: "2.1", hold: 12, render: () => `
      <p class="line" data-cue="2.1a">Write the script in markdown.</p>
      <p class="line" data-cue="2.1b">Narrate it in your own voice.</p>
      <div class="tile" data-cue="2.1c" data-count>3 steps</div>` },
  { id: "2.2", hold: 6, render: () => `<p class="hero">One take.</p>` },
]});
DeckTalk.on("2.1c", () => console.log("a cue can also run code"));
</script>
```

and `cues.json` says when:

```json
{ "sections": { "2": { "cues": [
  { "step": "2.1a", "on": "First" },
  { "step": "2.1b", "on": "Second" },
  { "step": "2.1c", "on": "Three steps" },
  { "step": "2.2",  "on": "One take" }
]}}}
```

`data-cue` reveals on its cue; `data-at` reveals seconds after the step mounts; `data-fx`
picks the animation (`rise`, `fade`, `draw`, `drop`, `pop`, `dim`, `none`); `data-count`
counts a number up; `data-type` types text; `data-tex` typesets with KaTeX when it is on
the page. Steps mount at their earliest cue and the last one holds. Without `?beats=` the
page autoplays on its `hold` seconds, so you can review it in a browser. The full contract
is in [docs/contract.md](docs/contract.md).

## Commands

| Command | Does |
|---|---|
| `decktalk init DIR` | Scaffold a project with a working example deck. |
| `decktalk setup` / `doctor` | Fetch Chromium and ffmpeg / report what is installed. |
| `decktalk narrate [--dry-run] [--silent] [--only N] [--force]` | Script to per-section audio with word timestamps, plus the continuous track and timeline. Cached by text hash, so an edit re-synthesizes only the sections whose text changed. |
| `decktalk beats` | Resolve every cue phrase to a second. |
| `decktalk soundscape [--dry-run]` | Ambience, one-shot sfx and an underscore from the prompts in `decktalk.toml` (ElevenLabs). |
| `decktalk record [--only N]` | Record the pages. `decktalk measure` then finds narration t=0 in each. `decktalk check` flags black or truncated recordings. |
| `decktalk assemble [--preset veryfast] [--nomix] [--strict]` | Cut, concatenate, mix, normalize, publish. |
| `decktalk verify [SEC:CUE …]` | Every section opens on a real frame, and the picture changes at the named cues. Counts the share of pixels that change inside the section, so it sees a thin line or one line of text. |
| `decktalk shots [--section N --at S …]` | One PNG per step of every page, or frames from a section as it plays with its real cues. Good for review, and for showing an AI reviewer the frames. |
| `decktalk build [--silent] [--only N]` | The whole pipeline in order. |
| `decktalk status` | The timeline and what is built. |
| `decktalk runtime` | Copy the packaged runtime over the project's copy after upgrading DeckTalk. |

Every project command takes `--project DIR` (default: the current directory) and reads
`.env` from the project. Keys are never printed. `-v` shows every ffmpeg command line.

## Configuration

Three layers, lowest to highest precedence:

1. Defaults in the code (`decktalk.config`), one dataclass per concern: `video`,
   `narration`, `record`, `align`, `audio`, `verify`, `elevenlabs`.
2. Tables of the same names in the project's `decktalk.toml`, for example
   `[video] preset = "veryfast"`.
3. Environment variables `DECKTALK_<SECTION>_<FIELD>`, for example
   `DECKTALK_VIDEO_PRESET=veryfast`, and a few CLI flags such as `--preset` for one run.

Content that changes per presentation (sections, voice, mix levels, soundscape prompts)
lives in `decktalk.toml` too, but as the document rather than tuning. Secrets live only
in `.env`.

## Python API

The CLI is a thin layer over a small stable API:

```python
import decktalk

project = decktalk.Project.load("my-lesson")       # validated decktalk.toml + settings
project.settings.video.preset = "veryfast"
result = decktalk.build(project, silent=True)       # or narrate(), resolve_beats(), record(), ...
print(result.assembly.final, result.verification.ok)
```

Stage functions return typed results and raise `decktalk.DeckTalkError` subclasses
(`ConfigError`, `MissingInputError`, `ProviderError`, `ToolError`) instead of exiting.
Progress goes to the `decktalk` logger. Modules under `decktalk.media` and
`decktalk.providers`, and names starting with an underscore, are internal. Until 1.0 the
Python names may move; the file formats, build artifacts and the page contract are treated
as stable already.

## Costs and models

Narration uses `eleven_multilingual_v2` with word timestamps at one credit per character;
a six-minute script is about 6,000 credits per full take. Sound effects bill per second
and music per minute. ElevenLabs' Creator plan covers a day of iteration comfortably.
The voice model and settings are per project in `decktalk.toml` `[voice]`.

## Development

```console
$ uv sync --group dev
$ uv run ruff check src tests && uv run ruff format --check src tests && uv run ty check src
$ uv run pytest -q                 # unit tests; add -m browser for the Chromium runtime tests
$ bash tests/smoke.sh              # scaffolds a project and builds it offline, ~1 min
```

## License

MIT.
