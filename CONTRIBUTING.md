# Contributing

Issues and pull requests are welcome. One person maintains DeckTalk, so expect a reply within about a week.

- Before a large change, open an issue, so that we agree on the design first.
- If you build something with DeckTalk, share a link in an issue.

## Setup

You need:

- [uv](https://docs.astral.sh/uv/). It installs Python and every dependency.
- bash, for `tests/smoke.sh`. On Windows, use Git Bash.

Run every command in this file from the repository root.

```console
uv sync --group dev
uv run decktalk setup            # headless Chromium, ffmpeg, and KaTeX, once per machine
uvx pre-commit install           # the lint hooks and the commit message hook
```

## Checks

Run these checks before you open a pull request.

```console
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src
uv run pytest -q                                 # unit tests
uv run pytest -q -m "browser or media"           # the runtime in Chromium, frame analysis in ffmpeg
bash tests/smoke.sh                              # scaffold a project and build it offline
uv run scripts/build_assets.py --check           # fails if assets/*.svg or docs/images are out of date
uv run scripts/build_config_reference.py --check # fails if docs/reference/configuration.mdx is out of date
```

No check needs an ElevenLabs key. After `decktalk setup`, no check needs the network. Do not add a check that calls the API.

### What CI runs

CI runs two jobs from `.github/workflows/ci.yml`.

- The `checks` job runs every check above except the browser tests, the smoke build, and `scripts/build_assets.py --check`. It runs on Linux with Python 3.12, 3.13, and 3.14.
- The `build` job runs `decktalk setup`, `decktalk doctor`, the browser and media tests, and the smoke build.

| Trigger | `checks` job | `build` job platforms |
|---|---|---|
| A push to `main` | Runs | Linux |
| A pull request | Runs | Linux |
| A `v*` tag that a person pushes | Runs | Linux, macOS, Windows |
| "Run workflow" in the Actions tab | Runs | The platforms you choose: all, ubuntu, macos, or windows |
| A tag that release-please creates | Does not run | Does not run |

- On macOS and Windows, the `build` job widens the sync limits. It sets `DECKTALK_VERIFY_MAX_OFFSET_FRAMES=4`, `DECKTALK_VERIFY_MAX_AV_FRAMES=5`, and `DECKTALK_ALIGN_STALL_MS=400`.
- Each `build` job uploads the smoke video, the shots, the sidecars, `beats.json`, and `timeline.json`. It uploads them even when the job fails.

## Layout

```text
src/decktalk/
  cli.py         the command line: its tables, --json output, and exit codes
  config.py      tuning settings: defaults, machine file, decktalk.toml, DECKTALK_* env, flags
  project.py     the decktalk.toml document, validated at load
  artifacts.py   typed build artifacts (manifest, timeline, beats, sidecar)
  scaffold.py    setup, doctor, and init: the downloads and the template copy
  status.py      what a project has built, read from disk for `decktalk status`
  verdicts.py    every verdict string, and which verdicts are certain
  errors.py      DeckTalkError and its subclasses, which the CLI maps to exit codes
  stages/        narrate, beats, preflight, record, measure (with check), assemble, verify, shots, soundscape, build
  media/         ffmpeg and Chromium (internal)
  providers/     the speech protocol and the ElevenLabs provider (internal)
  runtime/       decktalk-runtime.js, the page contract
  template/      what `decktalk init` writes
tests/
  test_units.py      config layering, project validation, script parsing, cue matching
  test_cli.py        exit codes, --json output, and flags, with stages replaced by fixed results
  test_runtime.py    drives decktalk-runtime.js in a real Chromium (-m browser)
  test_media.py      checks frame analysis against real ffmpeg on a synthetic video (-m media)
  test_preflight.py  preflight's frozen frames on the scaffold and on a synthetic page (-m browser, -m media)
  smoke.sh           an offline build of the scaffold, verified cue by cue
scripts/
  build_assets.py             generates assets/*.svg, docs/images, docs/logo, the favicon
  build_changelog.py          generates docs/changelog.mdx from CHANGELOG.md
  build_config_reference.py   generates docs/reference/configuration.mdx from config.py
docs/                          the Mintlify site at docs.decktalk.app
site/                          the landing page at decktalk.app
```

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed result, and raise `DeckTalkError` subclasses. The CLI prints tables from those results and maps errors to exit codes.

You can extend DeckTalk in two places:

- **Pages.** The page contract is in `runtime/decktalk-runtime.js`. [The page contract](https://docs.decktalk.app/concepts/page-contract) documents it.
- **Voices.** A speech provider implements the two-method protocol in `providers/speech.py`. DeckTalk has no plugin loading, so a new provider comes as a pull request.

## Roadmap

- A local text-to-speech provider with a forced aligner will let a project build with no API.
- A second slide template will offer a lighter visual style.
- The README will show a real demo video, built from the scaffold with a cloned voice.

## Writing docs

People and agents read these docs. Write so that neither has to guess.

- Put the most important fact first in each page, section, and paragraph.
- Write one idea per sentence. Aim for 20 words, and use 25 at most.
- Number every procedure. Give each step one action, and show its output.
- Put a condition before its action: "If the build stops, run `decktalk beats`."
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
| `docs/reference/configuration.mdx` | The comments in `src/decktalk/config.py` | `uv run scripts/build_config_reference.py` |
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
