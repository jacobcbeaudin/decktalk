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

## The thesis this release is shaped by

The command line is the instruction set. The settings and the page attributes are the knobs, each
one named, documented, ranged and defaulted. The agent is the implementer. A surface an agent cannot
discover, read and act on from the command line and the schemas alone is a surface that fails the
thesis, so every published name has exactly one home in the code and every rendering of it is
generated from that home.

Three commands make the whole instruction set readable without running a stage. `decktalk --help`
gives the tree, the global flags and the exit codes. `decktalk schema` gives every command, every
flag with its type and default, every error code, every finding code, every event and every page
attribute as one JSON object. `decktalk schema settings` gives every knob with its default, its safe
range, its unit and its hazard, and `decktalk config explain KEY` gives one of them whole.

## The four files an author writes

| File | Holds | Read by |
|---|---|---|
| `script.md` | What the voice says, one `## N.` heading per section | `narrate` |
| `decktalk.toml` | One `[[section]]` table per section, naming its page and scene, and the tuning | every stage |
| `cues.json` | Which spoken phrase each moment on the page waits for | `cue` |
| `deck/*.html` | The pictures, as scenes of slides whose elements declare their moments | `record` |

Everything under `build/` is generated and git-ignored. A project is recreated from the four files
and the credits it spends.

## The six stages

Each stage is one command, one package and one result class. They always run in this order, and each
reads what the ones before it wrote. `src/decktalk/pipeline.py` declares the order, what each stage
reads, what it writes, and why it sits where it does, and every other part of the system reads that
one table.

| Stage | One job | Writes |
|---|---|---|
| `narrate` | Voice each section and get a time for every word. | `build/narrate/takes.json`, one take and one words file per section |
| `cue` | Turn each cue phrase into a second on that section's clock. | `build/cue-times.json` |
| `record` | Record each page section in headless Chromium and find narration t=0 in the frames. | `build/recordings/` |
| `soundscape` | Generate the music, the ambience bed and the effects. | `build/soundscape/` |
| `assemble` | Cut each section to its span, join them, mix the sound and publish. | `build/final/<name>.mp4`, captions, chapters, transcript, poster |
| `verify` | Measure the finished film against what it was supposed to be. | nothing |

`soundscape` sits after `record` because it spends, and stopping at `record` is therefore the draft
loop that costs nothing. `assemble` consumes what all four before it wrote, which is why a stage that
spends money and writes an artifact has to be reachable by `--from`, `--to`, `--skip`, the spend gate
and the event stream rather than hidden inside another stage.

Six more calls report on a project, cut a piece out of it or serve it: `status`, `check`, `words`,
`storyboard`, `serve` and `clip`. `check` judges without producing anything and prices what a build
would cost, so an author sees the findings and the money before a single second is bought.
`storyboard` freezes every slide at every cue onto one page, which is the checkpoint a person looks
at before credits are spent.

A take is named by a hash of its text, its voice, its model and its settings, so an edit voices only
the sections whose words changed. A recording is kept when its page, its words, its cues and every
local file it loads are unchanged, and when the motion settings that shaped it have not moved. Paid
takes are the only expensive thing in the tree, and caching them by content is what makes the tenth
edit cheap.

`tests/contract/test_imports.py` names every import that points sideways between stages, each with
the reason it exists, so an exception is designed rather than acquired. There are twelve of them and
every one is either `build` and `check` calling the stages they orchestrate, or a stage asking its
neighbour for a rule that neighbour owns.

## The five layers

`src/decktalk` is five layers deep. A module may import from a strictly lower rank, or from inside
its own package, and from nothing else.

| Layer | Holds | Knows about |
|---|---|---|
| vocabulary | `pipeline`, `findings`, `errors`, `locate`, `secret`, `page` | nothing but each other |
| models | `results`, `events`, `catalog`, `tomlmap`, `settings`, `explain` | the words, and no project |
| leaves | `toolchain`, `captions`, `speech`, `media`, `pagescan`, `template`, `artifacts` | one job each, and no project |
| sdk | `inputs`, `machine`, `stages`, `project` | a project, and the stages it drives |
| cli | `cli`, `__init__`, `__main__` | every call, and nothing a call does not return |

The layers are wide enough to be ranked among themselves, so one table gives every top-level module
one rank and one comparison enforces both the layer and the order inside it. The walk reads the AST
rather than the imports Python happens to run, so an import inside a function body counts exactly as
much as one at the top of a file, and a target that no longer exists fails rather than passing by
being unrankable. `project` ranks above `stages` because it calls a stage by name through
`import_module`, which is a string no AST walk can see, and declaring the rank the code really has is
what keeps that one edge honest.

`scripts/build_contributing.py` writes the module tree in `CONTRIBUTING.md` from the same table, so
the documentation of the shape and the enforcement of it come from one source.

The rule buys two things. A speech provider is built from a `VoiceContext` and never from a project,
so the speech boundary sits in the leaves and a second provider would touch nothing above it. And
the command line renders from the result objects alone, so it imports no stage and knows no result's
shape.

## What every call returns

Every call returns a frozen Pydantic model. Four keys are reserved on every one of them: `schema`,
which is 2 and is the shape version, `ok`, `findings` and `error`. Two more are declared by the
results that earn them: `run` on every result whose command opens a run, and `written` on every
result that writes a file, carrying the project-relative paths that run wrote. There is no wrapper
object and no nesting, because a flat object is the one shape a caller can dispatch on without
learning a second contract. `tests/contract/test_results.py` holds one table of command, callable,
result and driver, and the test is total in both directions.

A finding is a diagnostic in the shape a linter made familiar: a code a caller dispatches on, one
sentence with the measured number written into it, a certainty of `certain` or `uncertain`, a
location whose `where` names the object judged, the stage that raised it, a docs URL, and often a
fix. A fix is an edit, a setting or a command, each with an applicability that says whether it may be
applied without asking, and `Project.apply(finding)` applies it. No code spells its own certainty,
because a closed enum publishes each value with its own sentence where an adjective in a code name
publishes nothing.

`--fail-on certain|any|never` names a threshold rather than a field value, and `--allow CODE` carries
on past one code.

Errors are the other thing entirely. An error means DeckTalk could not run, so nothing was judged.
There are nine codes and seven classes, one mapping table, and a test that exactly `USAGE` and
`INTERNAL` have no raiser outside `cli/`. `USAGE` and `APPROVAL` exit 2, `CANCELLED` exits 130, the
rest exit 3, a finding at or above the threshold exits 1, and a clean run exits 0.

## One stream of progress

Every top-level call opens a run and writes to one event stream. The stream lives on the machine
rather than on a project, because installing a toolchain and reporting on a machine hold no project
and a project-only stream would leave `--events` silent on the two commands that download two
hundred megabytes. A project's `events` is that stream filtered to the runs the project opened.

There are eleven event names and the discriminator is `event`. The library mints `event`, `time`,
`seq` and `run` onto every line, and `run.start` carries the path the lines are being appended to, so
the stream and the file can never disagree. Skip and fail are not event names: `stage.done` and
`section.done` carry an `outcome`, because three names for one moment forces three branches where one
field read will do.

Nothing in the library prints. The command line subscribes and renders, `--events` puts the same
lines on stderr as they happen, and every run appends `build/events/<run>.jsonl`.

## The knobs

`settings.py` publishes every knob with its default, its safe range, its unit, its scope, its nature,
the judgements it moves and its environment name. The published range is the safe range and the
loader refuses a value outside it, naming the file and the line that wrote it, because a published
bound you can cross into nonsense is worse than no bound at all. Five layers can set a key and each
overrides the ones before it, and `decktalk config explain KEY` prints all five with the winner
marked.

No flag duplicates a settings key. `--set table.key=value` is the fifth layer, it is repeatable, it
writes nothing, it is validated by the same loader with the same refusal, and the loader routes each
pair to its own scope, so the command line never has to know which layer a key belongs to.

A number that is deliberately not a knob is published too, with the formula that derives it, so
`no magic numbers` is a rule with three doors rather than a habit. `tests/contract/test_numbers.py`
holds it against a per-file baseline that only ever shrinks.

## The page is the second knob surface

An element has four moments and every moment names a cue local to its slide, which the runtime
qualifies into a wire id. `src/decktalk/runtime/src/contract.ts` is the one home of every attribute,
its values, its range, its default, its warning code and its motion span, and `src/decktalk/page.py`
is generated from it, so Python and the page cannot disagree about a name or a bound.

An attribute survives only when its value can change a judgement or be named by a finding. The rows
that do neither are a closed list in the registry, each carrying the sentence that says why, and a
test counts the list.

`decktalk-runtime.js` and `decktalk-probe.js` are compiled from the same sources by a pinned esbuild,
committed, and held to their sources by `scripts/build_runtime.py --check`. `contract.json` is
committed as the intermediate, so everything downstream of it is pure Python and that half of the
check runs on three platforms.

## Why the cuts land

Chromium begins recording at a moment nobody can predict. The recorder covers the page in magenta
until the page says it is ready, and the first frame after the cover is narration t=0. The cover
holds something that always moves, so frames keep coming while a still page waits. Everything after
that is arithmetic on frames, and no part of it reads a wall clock.

## Two readers, one product

An author reads the tables the command line prints on a terminal. An agent reads the JSON, the
schemas and the help, and the six packaged skills carry only the craft a command line cannot say:
writing for the ear, spoken symbols, cue phrases, slide patterns, reading a finding and revising a
film. Everything mechanical is generated, so a page cannot describe a flag that is not there.

The decision notes in [`docs/decisions/`](docs/decisions/) give the reasoning behind this and the
other choices that are not obvious from the code.
