# Seven stages became five

## Decision

The pipeline is `narrate`, `align`, `record`, `assemble`, `verify`. There is no `measure` stage and
no `check` stage. Finding narration t=0 in a recording belongs to `record`, and every read-only
judgement about a finished film belongs to `verify`.

## Why

`measure` and `check` were stages because they were written at different times, not because an
author ever wanted to run them separately. Both only ever ran attached to another stage, and both
read files the stage beside them had just written. Two commands that always run together are one
command with a confusing name.

Keeping them apart cost something real. A recording could be judged by `check` and then assembled
anyway, because nothing tied the judgement to the stage that owned the file. Folding them in means a
recording is judged where it is made, and `record`'s result carries what it found.

Five is also the number an author can hold. Each stage now answers one question: what does it say,
when does each picture land, what does the page look like, what is the film, and is the film right.

## What it rules out

- There is no command that measures a recording without recording it. `preflight` rehearses instead,
  and it freezes frames rather than recording them.
- `verify` never writes anything. A stage that wanted to repair what it measured would have to be a
  sixth stage, and none has been needed.

## What would change it

A stage that a user genuinely wants to run alone, on files another stage did not just write, would
earn its own name. That test has not been met by anything so far.
