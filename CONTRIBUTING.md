# Contributing

Issues and pull requests are welcome. One person maintains DeckTalk, so expect a reply within about a week.

- Open an issue before a pull request, so that your change lands where the current design puts it and so
  that we agree on that design first.
- If you build something with DeckTalk, share a link in an issue.

## Setup

You need:

- [uv](https://docs.astral.sh/uv/). It installs Python and every dependency.
- [Node.js](https://nodejs.org/), for `npx`. It runs Biome, the JavaScript linter and formatter.

Run every command in this file from the repository root.

```console
uv sync --group dev
uv run decktalk install            # headless Chromium and ffmpeg, once per machine
uvx pre-commit install           # the lint hooks and the commit message hook
```

## Checks

One command runs every check a pull request must pass: lint, types, every test suite but the scaffold build,
and the generated-file checks. On its first run it may download headless Chromium and ffmpeg through
`decktalk install`, once per machine.

```console
uv run scripts/check.py          # everything CI runs on a pull request, in about three minutes
uv run scripts/check.py --fast   # lint, types, and the unit tests alone, in a few seconds
```

The steps, for running one on its own:

```console
uv lock --check                                  # the lockfile matches pyproject.toml
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run ty check src
npx --yes @biomejs/biome@2.5.13 ci .             # lint and format check for JavaScript
uv run pytest -q                                 # unit tests
uv run pytest -q -m "browser or media"           # the runtime in Chromium, frame analysis in ffmpeg
uv run pytest -q -m e2e                          # the pipeline test: an offline build of tests/e2e/fixture
uv run --with "fonttools[woff]>=4.50" python scripts/build_assets.py --check
                                                 # fails if assets/*.svg or docs/images are out of date
uv run scripts/build_settings_reference.py --check # fails if docs/reference/configuration.mdx is out of date
uv run scripts/build_changelog.py --check        # fails if docs/changelog.mdx is out of date
```

A bare `pytest` runs the unit tests alone. The `browser`, `media`, `e2e`, and `scaffold` markers select the
slower suites, and `--strict-markers` rejects a marker that is not registered in `pyproject.toml`.

| Marker | Needs | Time | What it covers |
|---|---|---|---|
| none | nothing | 1 s | config, project validation, script parsing, cue matching, the CLI with stages faked |
| `browser` | Chromium | 1 min | `decktalk-runtime.js` in a real page, preflight's frozen frames |
| `media` | ffmpeg | 10 s | frame analysis and loudness on synthetic video |
| `e2e` | Chromium, ffmpeg | 1 min | `tests/e2e/test_pipeline.py`: a build without voice of the five-section fixture, checked property by property |
| `scaffold` | Chromium, ffmpeg | 20 min | reserved for the full scaffold build |

The pipeline test builds `tests/e2e/fixture` with the network blocked, then asserts the build, the recording
checks, every cue, the seam, the merged chapter, the slate, the captions, the B-roll sound, preflight,
screenshots, status, the `narrate --dry-run` plan, cue resolution on uneven word timestamps, and a rebuild of one
section with `--only`. It fails on `OFF CUE` on Linux. On macOS and Windows it reports `OFF CUE` and asserts
the wider limits of four offset frames and five a/v frames, because the hosted runners there present frames
late. Pass `--gate-timing` to fail on `OFF CUE` everywhere. The test has a 180-second timeout.

No check needs an ElevenLabs key, and no ElevenLabs key is ever a CI secret. After `decktalk install`, no check
needs the network. Do not add a check that calls the API.

### What CI runs

CI runs four jobs from `.github/workflows/ci.yml` on every pull request and push to `main`.

- The `checks` job runs `uv lock --check`, ruff, ty, the unit tests, and the generated-file checks for the
  configuration reference and the changelog. It runs on Linux with Python 3.12, 3.13, and 3.14.
- The `lint` job runs Biome on JavaScript, once on Linux.
- The `e2e` job runs `decktalk install`, `decktalk doctor`, and every test suite but the scaffold build with
  coverage, on Linux. Coverage must stay at or above the floor in `pyproject.toml`, and the report goes to the
  job summary. When a test fails, the job uploads the pipeline project's `verify.json`, recording logs, screenshots, and mp4.
- The `cross-platform` job runs the browser, media, and pipeline suites on macOS and Windows. It runs on pushes
  to `main` and from the Actions tab, and `release.yml` runs it before `publish`, so a platform regression stops
  a release. It never runs on the tag that release-please creates, because that tag triggers no workflow.

Every action is pinned to a commit SHA with its version in a comment, and every workflow grants `contents: read`
at the top and more only per job. `release.yml` and the `pypi` environment are names registered with PyPI's
trusted publisher, so neither may be renamed.

## Layout

The order is the import order, from the vocabulary layer through the leaves, the model, the stages
and the command line. A module imports from a layer below its own or from inside its own package,
and `tests/test_imports.py` fails the suite on any other edge.

```text
src/decktalk/
  errors.py      DeckTalkError and its subclasses, which library callers catch
  secret.py      a value that may be used and never shown, such as the key read from .env
  verdicts.py    the Verdict enum, Finding, Findings, SkipReason and the StageResult protocol
  jsonio.py      the atomic JSON writer, the dataclass walker, and paths relative to the root
  tomlmap.py     one mapping loader: located errors, key hints, and dataclass trees
  settings.py    tuning settings: defaults, machine file, decktalk.toml, DECKTALK_* env, flags
  toolchain/     what ships in the wheel and what is fetched per machine: cache, ffmpeg, assets
  artifacts/     one module per build artifact: words, takes, timeline, cue times, recording log
  captions/      caption layout, and the srt, vtt and chapter files
  media/         ffmpeg, audio, frames, the encoder and Chromium (internal)
  speech/        the speech protocol, the provider registry, and ElevenLabs (internal)
  model/         one project: the decktalk.toml document, the build paths, .env, the script and cues
  cli.py         the command line: its tables, --json output, and exit codes
  report.py      the tables the CLI prints from a stage result (internal)
  scaffold.py    install, doctor, and init: the downloads and the template copy
  status.py      what a project has built, read from disk for `decktalk status`
  stages/        narrate, align, preflight, record, measure (with check), assemble, verify, screenshots, clip (with words),
                 soundscape, build
  runtime/       decktalk-runtime.js, the page contract
  katex/         the pinned KaTeX release the pages typeset with
  template/      what `decktalk init` writes
tests/
  test_imports.py    the layers: no import points up or sideways, and every stage returns a result
  unit/              one test file per module, at the path mirroring it under src/decktalk/
  test_units.py      config layering, project validation, script parsing, cue matching
  test_cli.py        exit codes, --json output, and flags, with stages replaced by fixed results
  test_runtime.py    drives decktalk-runtime.js in a real Chromium (-m browser)
  test_media.py      checks frame analysis against real ffmpeg on a synthetic video (-m media)
  test_preflight.py  preflight's frozen frames on the scaffold and on a synthetic page (-m browser, -m media)
  conftest.py        the --gate-timing option
  e2e/test_pipeline.py  an offline build of tests/e2e/fixture, checked property by property (-m e2e)
  e2e/fixture/       the five-section still deck: a shared chapter, a seam, a B-roll clip, a held
                     page with an equation, and a missing optional clip
scripts/
  check.py                    every check a pull request must pass, in one command
  build_assets.py             generates assets/*.svg, docs/images, docs/logo, the favicon
  build_changelog.py          generates docs/changelog.mdx from CHANGELOG.md
  build_settings_reference.py generates docs/reference/configuration.mdx from settings.py
docs/                          the Mintlify site at docs.decktalk.app
site/                          the landing page at decktalk.app
```

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed result, and raise `DeckTalkError` subclasses. The CLI prints tables from those results and maps errors to exit codes.

You can extend DeckTalk in two places:

- **Pages.** The page contract is in `runtime/decktalk-runtime.js`. [The page contract](https://docs.decktalk.app/concepts/page-contract) documents it.
- **Voices.** A speech provider implements the two-method protocol in `speech/__init__.py`. DeckTalk has no plugin loading, so a new provider comes as a pull request.

## Roadmap

- A local speech provider with word timings will let a project build with no API.
- A second slide template will offer a lighter visual style.
- The README will show a real demo video, built from the scaffold with a cloned voice.

## Writing docs

People and agents read these docs. Write so that neither has to guess.

- Put the most important fact first in each page, section, and paragraph.
- Write one idea per sentence. Aim for 20 words, and use 25 at most.
- Number every procedure. Give each step one action, and show its output.
- Put a condition before its action: "If the build stops, run `decktalk align`."
- Use active voice and present tense.
- Use the [glossary](https://docs.decktalk.app/reference/glossary) term for each thing. Never use a synonym.
- Write complete sentences. In reference tables, a short phrase is fine.
- Do not use semicolons, em-dashes, idioms, or version-specific wording.
- State each fact on one page, and link to it from the others.
- Copy output samples from a real build. Never edit their numbers by hand.
- Change generated pages through their sources.
- Use American spelling.

Two pages are generated. Do not edit them by hand.

| Page | Source | Command |
|---|---|---|
| `docs/reference/configuration.mdx` | The tuning fields in `src/decktalk/settings.py` | `uv run scripts/build_settings_reference.py` |
| `docs/changelog.mdx` | `CHANGELOG.md` | `uv run scripts/build_changelog.py` |

## Commits and releases

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org). The commit message hook checks each message. The `pr-title` workflow checks pull request titles, because a squash merge uses the title as the commit message.

| Commit | Bump while the version is below 1.0 | Bump from 1.0 |
|---|---|---|
| `fix:` | patch | patch |
| `feat:` | minor | minor |
| `feat!:` or a `BREAKING CHANGE:` footer | minor, because `bump-minor-pre-major` is set | major |

release-please keeps a release pull request open against `main`. The pull request does three things:

- It bumps the version in `pyproject.toml` and `uv.lock`.
- It writes `CHANGELOG.md`.
- It picks the bump from the commits since the last release.

To release:

1. Merge the release pull request. release-please creates the tag and the GitHub release.
2. Wait for the release workflow. It checks that the tag matches the version, runs the unit checks on Linux, and builds the wheel.
3. Check that the workflow passed its wheel test. The test runs `decktalk init` from the built wheel in a clean environment.
4. Check PyPI. The workflow publishes through trusted publishing and attaches the files to the GitHub release.

The release workflow also regenerates `docs/changelog.mdx`. To force a version, put `Release-As: 1.0.0` in a commit body.
