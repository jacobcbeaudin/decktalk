# Contributing

Issues and pull requests are welcome. This is a one-person project, so expect a reply
within about a week, and open an issue before a large change so the design is agreed
first. If you build something with DeckTalk, a link in an issue is welcome too.

## Setup

```console
uv sync --group dev
uv run decktalk setup            # headless Chromium and ffmpeg, once
```

## Checks

```console
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src
uv run pytest -q                                # unit tests
uv run pytest -q -m browser                     # the page runtime, in a real Chromium
bash tests/smoke.sh                             # scaffold a project and build it offline
uv run scripts/build_assets.py --check          # graphics are generated; regenerate, do not hand-edit
uv run scripts/build_config_reference.py --check # so is docs/reference/configuration.mdx
```

No check needs an ElevenLabs key or network access after `decktalk setup`. Do not add a
check that calls the API.

CI runs the same checks, with the unit checks on Python 3.12 to 3.14. Linux is the canary:
every push and pull request runs the browser tests and the smoke build there. A tag runs
them on macOS and Windows as well, and the Actions tab can run any platform on demand
through "Run workflow". Each run uploads its smoke video as an artifact.

## Layout

```
src/decktalk/
  config.py      tuning dataclasses; defaults -> decktalk.toml tables -> DECKTALK_* env
  project.py     the decktalk.toml document, validated at load
  artifacts.py   typed build artifacts (manifest, timeline, beats, sidecar)
  stages/        narrate, beats, record, measure (with check), assemble, verify, shots, soundscape, build
  media/         ffmpeg and Chromium (internal)
  providers/     the speech protocol and the ElevenLabs provider (internal)
  runtime/       decktalk-runtime.js, the page contract
  template/      what `decktalk init` writes
tests/
  test_units.py      config layering, project validation, script parsing, cue matching
  test_runtime.py    drives decktalk-runtime.js in a real Chromium (-m browser)
  smoke.sh           an offline build of the scaffold, verified cue by cue
scripts/
  build_assets.py             generates assets/*.svg, docs/images, docs/logo, the favicon
  build_config_reference.py   generates docs/reference/configuration.mdx from config.py
docs/                          the Mintlify site at docs.decktalk.app
site/                          the landing page at decktalk.app
```

Stage functions take a `Project`, log progress to the `decktalk` logger, return a typed
result, and raise `DeckTalkError` subclasses. The CLI is a thin layer that prints tables
and maps errors to exit codes.

Two seams are meant for extension. The page contract lives in `runtime/decktalk-runtime.js`
and is documented at [docs.decktalk.app/concepts/page-contract](https://docs.decktalk.app/concepts/page-contract).
The voice lives behind `providers/speech.py`, a two-method protocol. There is no plugin
loading yet, so a new provider is a pull request.

## Roadmap

- A local text-to-speech provider paired with a forced aligner, so a project can build with no API.
- A second slide template with a lighter visual style.
- A real demo video in the README, built from the scaffold with a cloned voice.

## Prose

Documentation and comments use complete sentences with subjects. Avoid fragments,
semicolons, and version-specific wording that goes stale.

## Commits and releases

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org): `fix:`
bumps the patch version, `feat:` the minor, `feat!:` or a `BREAKING CHANGE:` footer the major
(minor while 0.x). `pre-commit install` adds a hook that checks the message. The `pr-title`
workflow checks pull request titles, because a squash merge turns the title into the commit.

release-please keeps a release pull request open against `main`. It bumps the version in
`pyproject.toml` and `uv.lock`, writes `CHANGELOG.md`, and picks the bump from the commits
landed since the last release. Merging that PR creates the tag and the GitHub release. The
release workflow then builds, smoke-tests the wheel, and publishes to PyPI through trusted
publishing. To force a version, put `Release-As: 1.0.0` in a commit body.
