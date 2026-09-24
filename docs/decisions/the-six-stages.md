# The pipeline is six stages

## Decision

`PIPELINE` in `src/decktalk/pipeline.py` declares six stages in run order: `narrate`, `cue`,
`record`, `soundscape`, `assemble`, `verify`. Each row names the stage, the artifacts it reads, the
artifacts it writes and one sentence saying why it sits where it does. Nothing else in the tree
declares the order.

Two things follow from that declaration and are worth stating on their own. `soundscape` runs after
`record` rather than beside `narrate`. `check` is a command and not a stage.

## Why these six

Each stage answers one question an author can hold in mind, and each one hands the next a file it
can name.

| Stage | The question it answers | What it leaves behind |
|---|---|---|
| `narrate` | What does the film say, and when is each word said? | `build/narrate/takes.json` |
| `cue` | Which second on its section clock does each cue phrase fall on? | `build/cue-times.json` |
| `record` | What does the page look like while those seconds pass? | `build/recordings/` |
| `soundscape` | What does the film sound like under the voice? | `build/soundscape/` |
| `assemble` | What is the film? | `build/final/` |
| `verify` | Is the finished film in step with the clock the others promised? | nothing |

The split is by artifact rather than by activity. Two steps are one stage when the second only ever
reads what the first has just written, because two commands that always run together are one command
with a confusing name. Finding narration t=0 in a recording is the clearest case: it belongs to
`record`, which owns the file it reads, so no run can judge a recording and then assemble it anyway.

`PIPELINE` is read rather than repeated, which is the reason the table above is the only one. The
precondition check, the validation of `--from`, `--to` and `--skip`, the hint a `NOT_BUILT` error
carries and the next step `status` reports are four readings of those six rows.

## Why `soundscape` sits after `record`

`soundscape` reads `build/narrate/takes.json` and nothing the recorder writes, so it could have run
second. It runs fourth so that a run stopped at `record` is the unpaid draft loop.

`narrate` and `soundscape` are the two stages that spend money. `narrate` stops spending with
`--no-voice`, which writes placeholder takes with a real word clock, and `soundscape` is simply not
reached when a run ends at the recorder. So `decktalk build --no-voice --to record` writes takes,
resolves every cue and records every page for nothing, and it is the loop an author lives in while
the words and the pictures are still moving. Had `soundscape` run second, the same loop would either
have to skip a stage by name or buy audio on every pass.

A run stopped there still reports the stages it did not reach. `build --json` carries a row per
stage with an `outcome`, and the three after `record` come back `skipped`, so a caller reads what
was left undone rather than inferring it from a shorter list.

## Why `check` is a command rather than a stage

`check` judges `script.md`, `cues.json` and the deck pages, freezes a frame either side of every cue,
draws `build/storyboard.html` over those frames, and prices what a voiced build would cost. It does
all of that without producing a single artifact any stage reads. `Artifact` names five files and
directories, and nothing `check` writes is one of them.

That is the line. A stage is a step the next step consumes. `check` is a judgement an author or an
agent consumes, run as often as they like, in any order, before anything has been built. It takes
`--no-pages` to judge the written files with no browser at all and name the codes it could not
reach, and `--no-frames` to keep the browser and drop the freeze comparison, neither of which would
mean anything for a step the pipeline had to run.

`verify` stays a stage for the mirror reason: it is the last thing a `build` does, it reads the film
`assemble` just wrote, and a run that produced a film nobody measured is not a finished run. It
writes nothing, and that is allowed, because the pipeline row that names its reads is what puts it
in the order.

## What it rules out

- There is no stage that measures a recording without recording it, and no command that rehearses
  the pipeline. `check` and `storyboard` answer the questions a rehearsal was for, and they answer
  them from frozen frames rather than from a recording.
- `verify` never writes. A stage that wanted to repair what it measured would have to be a seventh
  stage, and none has been needed.
- A command that judges may not be reachable through `--from`, `--to` or `--skip`, because those
  three flags take a member of `Stage` and nothing else.

## What would change it

A step that a user genuinely wants to run alone, on files another step did not just write, and whose
output a later step reads, would earn a row in `PIPELINE`. A judgement, however expensive, would
not.
