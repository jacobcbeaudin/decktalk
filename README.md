# cuecut

Narrated presentation videos, cut to the word.

You write a script in markdown. ElevenLabs reads it in your voice and returns a
timestamp for every word. Your slides are plain HTML pages; each reveal names the
spoken phrase it should land on. cuecut records the pages with headless Chromium,
cuts every section to the narration frame-exactly, mixes an optional underscore and
ambience, normalizes loudness, and publishes one mp4. Change a sentence and only that
section re-renders.

It is the pipeline behind a six-minute hackathon film, pulled out of that repo and made
general: no company content, no hard-coded scene ids, one runtime file for the pages,
one command to build.

```
script.md ──narrate──▶ build/audio/NN-slug.mp3 + .words.json, narration.mp3, timeline.json   (ElevenLabs)
cues.json ──beats────▶ build/audio/beats.json         (which second each visual lands on)
deck/*.html ─record──▶ build/rec/NN-scene.webm        (Chromium, driven by ?beats=…)
             measure ▶ narration t=0 found in each recording (magenta marker)
             assemble▶ build/out/NN-section.mp4 ▶ build/out/<name>.mp4   (ffmpeg: cut, mix, loudnorm)
             verify  ▶ every section opens on a real frame; cues land
```

## Install

Everything is bundled: Playwright's Python package carries its own browser driver, and
`static-ffmpeg` carries ffmpeg and ffprobe for your platform (a system ffmpeg on PATH
is used when present).

```console
$ git clone <this repo> && cd cuecut
$ uv tool install -e .        # puts `cuecut` on your PATH (or use `uv run cuecut ...`)
$ cuecut setup                # fetches headless Chromium (~100 MB) and ffmpeg, once per machine
$ cuecut doctor               # confirms both are usable
```

Python 3.12+ and [uv](https://docs.astral.sh/uv/). No Node, no npm.

## Make a presentation

```console
$ cuecut init my-lesson && cd my-lesson
$ cp .env.example .env        # ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
$ cuecut build                # narrate → beats → record → measure → check → assemble → verify
$ open build/out/my-lesson.mp4
```

`cuecut build --silent` renders with silent placeholder narration and estimated word
times, so you can iterate on the visuals without spending credits or having a key.
The scaffold is a working three-scene deck: run it as-is first, then edit.

A project is a directory:

| Path | What |
|---|---|
| `script.md` | The narration. `## N. Title` sections are the cut points. `[Bracketed directions]` are not spoken. Write numbers and symbols the way you want them said. |
| `scenes.json` | The plan: which page and scene each section records, or which clip of yours plays; dips to black; the mix; the soundscape prompts. |
| `cues.json` | For each visual, the spoken phrase it lands on. `cuecut beats` reports any phrase it cannot find. |
| `deck/index.html` | Your slides. One HTML file, styled how you like, with `cuecut-runtime.js` included. Open it in a browser for an index of every scene and step; `?step=ID` freezes one. |
| `media/` | Your own clips (`media/open.mp4`), b-roll, the underscore markers. |
| `build/` | Everything generated. Git-ignored. |

### The page contract

`cuecut-runtime.js` is the only thing a page needs. It reads `?scene=N&beats=id@s,…&t0=S`
from the recorder and fires each cue id at the right second. You declare scenes and steps;
markup names its cue:

```html
<script src="cuecut-runtime.js"></script>
<script>
Cuecut.scene(2, { name: "Three lines", camera: "push", steps: [
  { id: "2.1", hold: 12, render: () => `
      <p class="line" data-cue="2.1a">Write the script in markdown.</p>
      <p class="line" data-cue="2.1b">Narrate it in your own voice.</p>
      <div class="tile" data-cue="2.1c" data-count>3 steps</div>` },
  { id: "2.2", hold: 6, render: () => `<p class="hero">One take.</p>` },
]});
Cuecut.on("2.1c", () => console.log("a cue can also run code"));
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
| `cuecut init DIR` | Scaffold a project with a working example deck. |
| `cuecut setup` / `doctor` | Fetch Chromium and ffmpeg / report what is installed. |
| `cuecut narrate [--dry-run] [--silent] [--only N] [--force]` | Script to per-section audio with word timestamps, plus the continuous track and timeline. Cached by text hash. |
| `cuecut beats` | Resolve every cue phrase to a second. |
| `cuecut soundscape [--dry-run]` | Ambience, one-shot sfx and an underscore from the prompts in `scenes.json` (ElevenLabs). |
| `cuecut broll --prompt "…" --name NAME` | A text-to-video clip (Veo via Gemini, or Kling via fal.ai) into `media/broll/`. |
| `cuecut record [--only N]` | Record the pages. `cuecut measure` then finds narration t=0 in each. `cuecut check` flags black or truncated recordings. |
| `cuecut assemble [--preset veryfast] [--nomix] [--strict]` | Cut, concatenate, mix, normalize, publish. |
| `cuecut verify [SEC:CUE …]` | Every section opens on a real frame; the picture changes at the named cues. The cue check is coarse (mean frame difference), so use it on big reveals and `cuecut shots --section` on small ones. |
| `cuecut shots [--section N --at S …]` | One PNG per step of every page, or frames from a section as it plays with its real cues. Useful for review, and for showing an AI reviewer the frames. |
| `cuecut build [--silent] [--only N]` | The whole pipeline in order. |
| `cuecut status` | The timeline and what is built. |
| `cuecut runtime` | Copy the packaged runtime over the project's copy after upgrading cuecut. |

Every project command takes `--project DIR` (default: the current directory) and reads
`.env` from the project. Keys are never printed.

## Costs and models

Narration uses `eleven_multilingual_v2` with word timestamps at one credit per character;
a six-minute script is about 6,000 credits per full take, and the hash cache means an
edit re-synthesizes only the sections whose text changed. Sound effects bill 40 credits
per second and music about 900 per minute. ElevenLabs' Creator plan covers a day of
iteration comfortably. `voice_settings` and `model` can be overridden in `scenes.json`.

## Development

```console
$ uv sync --group dev
$ uv run ruff check src tests && uv run ruff format --check src tests
$ uv run pytest -q
$ bash tests/smoke.sh          # scaffolds a project and builds it offline, ~1 min
```

## License

MIT.
