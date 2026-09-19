# Architecture

DeckTalk turns four text files into one mp4 in which every picture lands on the word that
introduces it. This note says how the code is arranged and why, for someone about to change it.
[CONTRIBUTING.md](CONTRIBUTING.md) has the commands and the generated module tree.
[docs.decktalk.ai](https://docs.decktalk.ai) documents the product.

## The one idea

Narration comes back from the speech provider with a start and an end time for every word. Every
other time in the system is derived from those word times. A cue names a phrase rather than a
second, so a reveal follows the voice wherever the voice puts it, and a rewritten sentence moves
every picture after it without anyone editing a timeline.

That is the whole design. Each decision below follows from it.

## The four files an author writes

| File | Holds | Read by |
|---|---|---|
| `script.md` | What the voice says, one `## N.` heading per section | `narrate` |
| `decktalk.toml` | One `[[section]]` table per section, naming its page and scene, and the tuning | every stage |
| `cues.json` | Which spoken phrase each picture waits for | `align` |
| `deck/*.html` | The pictures, as scenes of slides with `data-cue` on every element that waits | `record` |

Everything else under `build/` is generated and git-ignored. A project is recreated from the four
files and the credits it spends.

## The five stages

Each stage is one command, one module and one result class. They always run in this order, and each
reads what the ones before it wrote.

| Stage | One job | Writes |
|---|---|---|
| `narrate` | Voice each section and get a time for every word. | `build/narration/<hash>.mp3`, `<hash>.words.json`, `takes.json` |
| `align` | Turn each cue phrase into a second on that section's clock. | `build/cue-times.json` |
| `record` | Record each page section in headless Chromium and find narration t=0 in the frames. | `build/recordings/NN.webm` and its log |
| `assemble` | Cut each section to its span, join them, mix the sound and publish. | `build/out/<name>.mp4`, captions, chapters, transcript, poster |
| `verify` | Measure the finished film against what it was supposed to be. | nothing |

`build` runs all five. `preflight` rehearses `narrate` and `align` and freezes what `verify` would
measure, so an author sees the cost and the reveals before a single second is paid for. Two more
stages reach sideways for one thing each, because `record` owns the URL a page is opened at and the
rule that decides a recording is stale: `screenshots` opens that URL and `status` asks that rule.
Those four are the only stages that import another stage, and `tests/test_imports.py` names each of
the eleven edges with the reason it exists, so an exception is designed rather than acquired.

A take is named by a hash of its text, voice, model and settings, so an edit voices only the
sections whose words changed, and a recording is kept when its page, its words, its cues and every
local file it loads are unchanged. Paid takes are the only expensive thing in the tree, and caching
them by content is what makes the tenth edit cheap.

## The five layers

`src/decktalk` is five layers deep. A module may import from a layer below its own, or from inside
its own package, and from nothing else.

| Layer | Holds | Knows about |
|---|---|---|
| vocabulary | `errors.py`, `secret.py`, `verdicts.py` | nothing |
| leaves | `jsonio`, `pagescan`, `tomlmap`, `toolchain`, `settings`, `artifacts`, `captions`, `media`, `speech` | the layer below, and one job each |
| model | `model/` | a project, as everything above reads it |
| stages | `stages/`, `scaffold/` | one project and one command |
| CLI | `cli/`, `__init__.py`, `__main__.py` | every stage, and nothing a stage does not return |

`tests/test_imports.py` reads every import in the tree as an AST and fails the suite on any other
edge, and `scripts/build_contributing.py` writes the module tree in `CONTRIBUTING.md` from the same
table. The documentation of the shape and the enforcement of it come from one source, so neither can
drift.

The rule buys two things. A speech provider is built from a `VoiceContext` and never from a project,
so adding one touches nothing above `speech/`. And the CLI prints one table or one JSON envelope
from the result objects alone, so it imports no stage and knows no result's shape.

## What every command returns

A stage function takes a `Project`, logs its progress, and returns a typed result that satisfies
`StageResult`: a tally of what it judged, and the same thing as JSON-ready data. The CLI turns that
pair into one envelope and one exit code.

Every judgement carries a `Verdict`, which is a code, a label and whether it is certain. A certain
finding exits 1. An uncertain one exits 1 only under `--strict`. `findings.items[]` is the one place
a reader looks for rows, and the CLI lifts those rows out of the result's own payload rather than
being handed them, so a command that grows a row gets it in the envelope without the envelope
knowing anything about that command. `tests/test_results.py` drives every result through its real
stage and holds each to that rule.

Exit codes are 0 for nothing found, 1 for a finding, 2 for a command line the parser refused, 3 for
DeckTalk itself failing, and 130 for an interrupt.

## Two readers, one product

An author reads the tables the CLI prints. An agent reads the JSON envelope, and the six skills
under `src/decktalk/skills` tell it how. The skills ship in the wheel, `decktalk init` writes them
into `.agents/skills/` with a `.claude/skills` link beside them, and `tests/test_skills.py` runs
every command line they name through the real parser and checks every key and JSON field they cite
against the code. A skill that names something that does not exist fails the suite.

## Why the cuts land

Chromium begins recording at a moment nobody can predict. The recorder covers the page in magenta
until the page says it is ready, and the first frame after the cover is narration t=0. The cover
holds something that always moves, so frames keep coming while a still page waits. Everything after
that is arithmetic on frames, and no part of the alignment reads a wall clock.

The decision notes in [`docs/decisions/`](docs/decisions/) give the reasoning behind this and the
other choices that are not obvious from the code.
