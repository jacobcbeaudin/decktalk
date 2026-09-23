# Contributing

DeckTalk is alpha and one person maintains it. He designs and reviews every change, and an agent
writes much of the code under that review, with the checks below as the proof. The command line, the
configuration keys and the Python names are still moving, so a pull request written today may not
apply by the time it is read. Please open an issue before writing one.

Three things help most right now.

- **Open an issue.** A bug report, a question or an idea is welcome now, and it lands in the design
  before the code is written.
- **Share what you built.** Put a link in [Discussions](https://github.com/jacobcbeaudin/decktalk/discussions).
- **Say what was confusing.** The docs are part of the product, and a page that lost you is a bug.

Expect a reply within about a week.

## Setup

You need two tools.

- [uv](https://docs.astral.sh/uv/). It installs Python and every dependency.
- [Node.js](https://nodejs.org/), for `npx`. It runs Biome, the JavaScript linter and formatter.

Run every command in this file from the repository root.

```console
git clone https://github.com/jacobcbeaudin/decktalk.git
cd decktalk
uv sync --group dev           # Python 3.12 and every dependency, into .venv
uv run decktalk install       # headless Chromium and ffmpeg up front, once per machine, into ~/.cache
uvx pre-commit install        # the lint hooks and the commit message hook
```

`uv run decktalk` runs the working tree's CLI from `.venv` and is what every command below uses.
`uv tool install .` instead puts that same CLI on PATH as a bare `decktalk`, which is what the
packaged skills and the docs assume, so install it that way before trying a skill by hand.

`decktalk install` downloads about 200 MB and is the only step that needs the network after the
sync. It is not a step you have to take: a command that needs Chromium or ffmpeg fetches it, so the
checks would fetch both by themselves, inside whichever test ran first. Taking it here puts the
download where you can see it, and on Linux it is also what installs Chromium's system libraries,
which is the one thing here that asks for a password. Every check below runs offline once it has
finished. `uv run decktalk doctor` prints what it found and where, and it fetches nothing.

You need no ElevenLabs key. No check calls the speech API, and no key is a CI secret.

## Checks

One command runs every check a pull request must pass.

```console
uv run scripts/check.py          # everything CI runs on a pull request, in about three minutes
uv run scripts/check.py --fast   # lint, types and the unit tests alone, in a few seconds
```

It prints each step with its time and stops at the first failure. Its first run may download
Chromium and ffmpeg, through the `decktalk install` step it starts with.

The steps, for running one on its own:

```console
uv lock --check                                    # the lockfile matches pyproject.toml
uv run ruff check src tests scripts                # lint
uv run ruff format --check src tests scripts       # formatting
uv run ty check src                                # types
npx --yes @biomejs/biome@2.5.13 ci .               # lint and format check for JavaScript
uv run pytest -q -m "not scaffold" --cov           # every suite but the scaffold build
uv run scripts/build_cli_reference.py --check      # each generated page against its source
uv run scripts/build_settings_reference.py --check
uv run scripts/build_outbound_reference.py --check
uv run scripts/build_skills_list.py --check
uv run scripts/build_contributing.py --check
uv run scripts/build_changelog.py --check
uv run scripts/check_docs_links.py --check         # every internal link and the navigation
uv run --with "fonttools[woff]>=4.50" python scripts/build_assets.py --check
                                                   # assets/*.svg, docs/images and the favicon
```

### The test suites

A bare `pytest` runs the unit tests alone, in about a second. The four markers select the slower
suites, and `--strict-markers` rejects a marker that `pyproject.toml` does not register.

| Marker | Needs | Time | What it covers |
|---|---|---|---|
| none | nothing | 1 s | settings layering, project validation, script parsing, cue matching, every CLI handler with the stages faked |
| `browser` | Chromium | 1 min | `decktalk-runtime.js` and `decktalk-probe.js` in a real page, and preflight's frozen frames |
| `media` | ffmpeg | 10 s | frame analysis and loudness on synthetic video |
| `e2e` | Chromium, ffmpeg | 1 min | `tests/e2e/test_pipeline.py`, a build without voice of the five-section fixture, checked property by property |
| `scaffold` | Chromium, ffmpeg | 5 min | every project `decktalk init` writes, recorded and verified without a voice |

```console
uv run pytest -q -m browser
uv run pytest -q -m "browser or media"
uv run pytest -q -m e2e
```

The pipeline test builds `tests/e2e/fixture` with the network blocked, then asserts the recording
checks, every cue, the seam, the merged chapter, the slate, the captions, the clip's sound,
preflight, the screenshots, the progress log, `status`, the `narrate --dry-run` plan, cue resolution
on uneven word timestamps, and a rebuild of one section with `--only`. It has a 180-second timeout.

It fails on `OFF CUE` on Linux. On macOS and Windows it reports `OFF CUE` and asserts the wider
limits of four offset frames and five a/v frames, because the hosted runners there present frames
late. That is why a passing suite on a Mac is not a passing suite in CI. Pass `--gate-timing` to
fail on `OFF CUE` everywhere.

Coverage runs with the whole suite and must stay at or above the floor in `pyproject.toml`.

### What CI runs

`.github/workflows/ci.yml` runs six jobs. Four run on every pull request and push to `main`; the two
heavy ones run only on `main` and from the Actions tab.

| Job | Platform | What it runs |
|---|---|---|
| `checks` | Linux, Python 3.12, 3.13 and 3.14 | `uv lock --check`, ruff, ty and the unit tests |
| `docs` | Linux | every generated page against its source, and every internal link |
| `lint` | Linux | Biome over the JavaScript, and `shellcheck -s sh` over `site/install.sh` |
| `e2e` | Linux | `decktalk install`, `decktalk doctor` and every suite but the scaffold build, with coverage |
| `cross-platform` | macOS and Windows | the browser, media and pipeline suites |
| `installer` | Linux containers | `site/install.sh` installed for real on Debian, Ubuntu and Fedora, refused on Alpine |

The `e2e` job puts the coverage report in the job summary, and on a failure it uploads the pipeline
project's `verify.json`, its recording logs, its screenshots and its mp4.

The `installer` job runs on the same rule as `cross-platform`, and additionally on any pull request
that touches `site/install.sh`. It never runs `decktalk install`, which fetches Chromium and ffmpeg:
the installer's job is to put the `decktalk` command on a machine, and the tools it records with
arrive later, on the first build or on an `install` of your own.

The `cross-platform` job runs on pushes to `main` and from the Actions tab, and `release.yml` runs it
before `publish`, so a platform regression stops a release. It never runs on the tag release-please
creates, because that tag triggers no workflow. Pull requests stay on Linux, which keeps them to
about five minutes.

Every action is pinned to a commit SHA with its version in a comment, and every workflow grants
`contents: read` at the top and more only per job. The name `release.yml` and the environment name
`pypi` are registered with PyPI's trusted publisher, so neither may be renamed.

## Layout

`src/decktalk` is five layers deep. A module may import from a layer below its own, or from inside
its own package, and from nothing else. `tests/test_imports.py` reads every import in the tree and
fails the suite on any other edge, and the tree below is generated from the same table, so what you
read here is what the suite enforces.

<!-- layout:start -->
<!-- Generated by scripts/build_contributing.py from src/decktalk. Do not edit by hand. -->

```text
src/decktalk/
  vocabulary             the words every layer above shares
    errors.py            The exceptions DeckTalk raises on purpose, and the closed list of codes the CLI reports them by.
    pipeline.py          The vocabulary of a run: the five stages in the order they run, the events a run records, and the closed values its files carry.
    secret.py            A value that may be used and never shown: an API key, and every other value read from `.env`.
    verdicts.py          The shared vocabulary of judgement: verdicts, findings, skip reasons, and the stage result protocol.
  leaves                 one job each, and no knowledge of a project
    jsonio.py            Reading and writing the JSON files DeckTalk owns, and the one walker each way between JSON and a dataclass.
    pagescan.py          The static page scan: what the measured catalog says about a slide, without looking at a picture.
    tomlmap.py           One loader from a mapping to typed values, with located errors and "did you mean" hints.
    toolchain/           What DeckTalk fetches or ships for one machine, and where it keeps it.
      assets.py          What ships inside the wheel: the page runtime, the pinned KaTeX release, and the projects.
      cache.py           The per-user cache directory, which is where every tool DeckTalk fetches for a machine lives.
      chromium_fetch.py  The headless Chromium Playwright manages: whether this machine has it, and fetching it.
      ffmpeg_fetch.py    The pinned ffmpeg build: one fixed URL per platform, verified against its SHA-256 before it is opened.
    settings.py          Tool tuning: one dataclass per concern, composed into Settings.
    artifacts/           The typed build artifacts and their JSON files under `build/`.
      cue_times.py       `CueTimes` and `CueTime`, every resolved cue as an object.
      cuts.py            `Cuts` and `Cut`, the cut list: where every section sits in the finished film.
      progress.py        `ProgressRow`, one line of the log a build keeps of itself.
      recordings.py      `RecordingLog`, everything `record` did for one section and everything it judged about the result.
      takes.py           `Takes` and `Take`, the index of what the voice recorded, and the narration clock it makes.
      words.py           `Word` and its file, the time base everything else shares.
    captions/            Captions, chapters and the files they are written to.
      files.py           The caption, chapter and transcript files `assemble` writes beside the final mp4.
      layout.py          Words become caption cues: where each cue starts and ends, and how its one or two lines break.
    media/               Everything DeckTalk drives to make a picture and a sound: ffmpeg, ffprobe and headless Chromium.
      audio.py           Audio work on top of ffmpeg, so nothing above this module spells an audio filter by hand.
      browser.py         Headless Chromium through Playwright: recording a page, taking screenshots and drawing slates.
      encode.py          The settings and the tags every output shares, so each file DeckTalk writes is made the same way.
      ffmpeg.py          Finding ffmpeg and ffprobe for this machine, running them, and probing what they read.
      frames.py          Frame statistics on top of ffmpeg: luma, single frames, and changed-pixel comparisons.
      origin.py          The local origin every page is opened at, its request routing, and the server `decktalk serve` runs.
    speech/              The speech boundary, which is anything that reads text aloud and says when each word was spoken.
      elevenlabs.py      ElevenLabs, the default speech provider: text read aloud with a time for every word.
      http.py            Minimal HTTP helpers on urllib, so a speech provider needs no HTTP dependency.
  model                  one project, as everything above reads it
    model/               The model of one project: everything DeckTalk knows before a stage runs.
      cues.py            `cues.json` parsed, and the phrase matching that resolves a cue against a section's words.
      document.py        The `decktalk.toml` document: the frozen tables that say what this presentation is.
      env.py             The project's secrets: `.env` read once, and never printed.
      markers.py         `media/markers.json` parsed into typed `Marker` rows, which shape the music under the video.
      project.py         A DeckTalk project, which is a directory holding decktalk.toml, a script, cues, pages and media.
      script.py          `script.md` parsed into the sections the voice reads.
      timeline.py        Where the narration plays in the final film: the narration clock placed on the film's clock.
      workspace.py       Every path under `build/`, named once.
  stages                 one module per command, and the scaffold beside them
    scaffold/            Writing a project, installing the machine's tools, and reporting on both.
      doctor.py          One row per component a build needs, and whether it is there, and the same report as a block.
      examples.py        The projects packaged in the wheel, which `decktalk init --example NAME` writes.
      init.py            Writing a project into a directory.
      install.py         Fetching the tools a machine needs to record and encode, before anything asks for them.
      skills.py          The six packaged skills, and where a project keeps them.
    stages/              The pipeline, one module per command.
      build.py           The whole pipeline in order: narrate, align, record, assemble, verify.
      clip.py            Clips and word times taken from a built section.
      screenshots.py     Screenshots for review: one PNG per slide, or frames from a section as it plays.
      soundscape.py      Beds and one-shots from ElevenLabs: ambience, sfx, and music, from decktalk.toml [soundscape].
      status.py          What a project's four input files say, and what it has built so far, read from disk.
      align/             Stage 2: cue phrases become narration timestamps (cues.json + words -> build/cue-times.json).
        pages.py         The two-way check of `cues.json` against the pages that play its cues.
      assemble/          Stage 4: the recordings, the narration, the clips and the soundscape become one mp4.
        cut.py           Every section becomes one silent mp4, and the cut list records where each one plays.
        loudness.py      EBU R128 loudness: measure, apply one gain, limit the true peaks, and measure again.
        mix.py           The whole soundtrack as one ffmpeg filter graph, one `MixInput` per layer.
        publish.py       Everything a viewer receives beside the picture: captions, chapters, the transcript and the poster.
      narrate/           Stage 1: `script.md` becomes one take per section, indexed by content hash.
        plan.py          The take plan: what a run would voice, what it already has, and what it would cost.
        script_rules.py  The rules a script must obey before any of it is paid for.
        takes.py         Writing one take, placing it, and joining every take into one narration track.
      preflight/         Preflight: what a voiced build would spend and show, with no credits and no recording.
        freeze.py        Which frozen states of a page each cue is measured between, with no browser and no file.
        scan.py          Rendering the frozen frames into build/preflight and comparing them.
      record/            Stage 3: record each page section with headless Chromium, find narration t=0, and check the result.
        capture.py       The URL a page section is opened at, and the capture that records it.
        checks.py        What one finished recording is judged on, before anything is assembled from it.
        start.py         Where narration t=0 sits in a recording.
      verify/            Stage 5: the one read-only checker, over the finished mp4 and the logs that made it.
        measure.py       The ffmpeg calls behind the cue plan: the probes, the onset scan and the click search.
        plan.py          The arithmetic behind a cue check, with no ffmpeg, no file and no project.
        seams.py         The three checks that read the shape of the film rather than one cue: starts, cuts and seams.
  CLI                    the command line, and the package's public surface
    cli/                 The `decktalk` command line: parse, dispatch, and map what happened to one exit code.
      authoring.py       The commands an author runs around a build: what is there, what it would do, and what it cuts.
      dispatch.py        The table that maps a command name to the handler that runs it.
      envelope.py        The one `--json` envelope, and the exit policy behind it.
      machine.py         The commands that act on a machine rather than on a project: `init`, `install` and `doctor`.
      options.py         One typed options dataclass per command, read from the parsed arguments by field name.
      output.py          The tables and the leading summary the CLI prints, read from the stage results themselves.
      parser.py          The command table, the shared flag groups, and the parser both are built into.
      schema.py          The `--json` envelope as types: one frozen dataclass per payload and per row, and the reader.
      video.py           The commands that make the video: the stages in order, and `build`, which runs them all.
    __init__.py          DeckTalk: narrated presentation videos, cut to the word.
    __main__.py          `python -m decktalk` runs the CLI.
  packaged data          what ships in the wheel and holds no Python
    katex/               The pinned KaTeX release the pages typeset with, copied into a project by `decktalk init`
    runtime/             decktalk-runtime.js, the page contract every deck loads, and decktalk-probe.js, the recorder's own
    skills/              The six packaged skills a project keeps in .agents/skills/
    template/            The starter, the examples and the AGENTS.md that `decktalk init` writes
```
<!-- layout:end -->

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed result and
raise `DeckTalkError` subclasses. The CLI prints one table or one JSON envelope from those results,
and maps a finding to exit 1, a refused command line to exit 2 and an error to exit 3.
`decktalk.__all__` is the whole supported Python API.

The rest of the repository:

```text
tests/            one test file per module, mirroring src/decktalk under tests/unit/
scripts/          check.py, the page generators and the asset generator
docs/             the Mintlify site at docs.decktalk.ai
docs/decisions/   one note per choice the code cannot explain, for readers of the tree
site/             the landing page at decktalk.ai
assets/           the generated graphics the README and the site use
```

`docs/decisions/` holds plain Markdown and sits outside `docs/docs.json`, so it is read on GitHub
and never published to the site. [ARCHITECTURE.md](ARCHITECTURE.md) is the overview those notes hang
from, and it is where to start if you are changing the shape of the package rather than one module.

The test for `src/decktalk/stages/verify/plan.py` is `tests/unit/stages/verify/test_plan.py`. The
suites that need a browser, ffmpeg or the whole pipeline sit at the top of `tests/` instead, one
file per suite.

You can extend DeckTalk in two places.

- **Pages.** The page contract is `runtime/decktalk-runtime.js`, and
  [The page contract](https://docs.decktalk.ai/concepts/page-contract) documents it.
- **Voices.** A speech provider implements the two-method protocol in `speech/__init__.py`. DeckTalk
  loads no plugins, so a new provider comes as a pull request.

## Writing docs

People and agents read these docs. Write so that neither has to guess.

- Put the most important fact first in each page, section and paragraph.
- Write one idea per sentence. Aim for 20 words, and use 25 at most.
- Number every procedure. Give each step one action, and show its output.
- Put a condition before its action: "If the build stops, run `decktalk align`."
- Use active voice and present tense.
- Use the [glossary](https://docs.decktalk.ai/reference/glossary) term for each thing. Never use a synonym.
- Write complete sentences. In a reference table, a short phrase is fine.
- Do not use semicolons, em-dashes, idioms or version-specific wording.
- State each fact on one page, and link to it from the others.
- Write an internal link from the site root, as `/reference/cli`, and link a heading by its anchor.
- Copy output samples from a real build. Never edit their numbers by hand.
- Use American spelling.

Six pages are generated from the code. Do not edit them by hand. Change the source and run the
script, which is what CI checks.

| Page | Source | Command |
|---|---|---|
| `docs/reference/cli.mdx` | The command table in `src/decktalk/cli/parser.py` | `uv run scripts/build_cli_reference.py` |
| `docs/reference/configuration.mdx` | The tuning dataclasses in `src/decktalk/settings.py` | `uv run scripts/build_settings_reference.py` |
| `docs/reference/what-leaves-your-machine.mdx` | `docs/data/outbound.toml` | `uv run scripts/build_outbound_reference.py` |
| The table in `docs/agents/skills.mdx` | The front matter of each `src/decktalk/skills/*/SKILL.md` | `uv run scripts/build_skills_list.py` |
| The module tree in `CONTRIBUTING.md` | The docstrings in `src/decktalk`, in the order `tests/test_imports.py` enforces | `uv run scripts/build_contributing.py` |
| `docs/changelog.mdx` | `CHANGELOG.md`, which release-please writes | `uv run scripts/build_changelog.py` |

`uv run scripts/check_docs_links.py` checks the rest of the site: every internal link resolves,
every page sits in exactly one navigation group in `docs/docs.json`, and every redirect points at a
page that exists. It fetches nothing.

`uv run --with "fonttools[woff]>=4.50" python scripts/build_assets.py` draws every graphic under
`assets/` and `docs/images/` from one source. It drives Playwright itself rather than going through
DeckTalk, so it needs the Chromium that `decktalk install` fetched and fetches none of its own.

## Commits and releases

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org). The commit
message hook checks each message, and the `pr-title` workflow checks pull request titles, because a
squash merge uses the title as the commit message.

```text
feat(cli): add --only to record
fix(align): match a phrase that spans two lines
docs: state the reveal floor in pixels
```

| Commit | Bump while the version is below 1.0 | Bump from 1.0 |
|---|---|---|
| `fix:` | patch | patch |
| `feat:` | minor | minor |
| `feat!:` or a `BREAKING CHANGE:` footer | minor, because `bump-minor-pre-major` is set | major |

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml` and `uv.lock`, writes `CHANGELOG.md`, and picks the bump from the commits since the
last release.

The version answers for the wheel, and the wheel is `src/decktalk` alone, so `exclude-paths` in
`release-please-config.json` lists the directories that ship to nobody: `site`, `docs`, `assets`,
`scripts`, `tests` and `.github`. A commit confined to those is read as no change and bumps nothing.
A commit that touches one of them and `src/decktalk` as well still counts in full, under its own
type, because release-please drops a commit only when every file in it sits under an excluded path.
The option matches directory prefixes only, so a file at the repository root such as `README.md` or
`biome.json` cannot be excluded, and a `feat:` that edits one bumps the minor version even when the
rest of the commit is site work.

To release:

1. Merge the release pull request. release-please creates the tag and the GitHub release.
2. Wait for the release workflow. It checks that the tag matches the version, runs the unit checks
   on Linux, and builds the wheel.
3. Check that the workflow passed its wheel test. The test runs `decktalk init` from the built wheel
   in a clean environment.
4. Check PyPI. The workflow publishes through trusted publishing and attaches the files to the
   GitHub release.

The release workflow also regenerates `docs/changelog.mdx`. To force a version, put
`Release-As: 1.0.0` in a commit body.
