# The skills are part of the product

## Decision

Six skills ship inside the wheel: script, slide, cues, build, fix and revise. `decktalk init` writes
them into the project's `.agents/skills/`, with `.claude/skills` as a link beside it.
`tests/test_skills.py` runs every command line a skill names through the real parser, and checks
every configuration key, attribute and JSON field it cites against the code.

## Why

An agent is a first-class user of this tool, not a bonus. The whole point of a video made from text
files is that something other than a person can edit it, and an agent that has to infer a CLI from
a help screen will get it wrong in ways that cost the author money, because `narrate` spends.

The skills are therefore held to the same standard as the code. A skill that names a flag that does
not exist is a bug that fails the suite, exactly like a test that lies. Documentation that cannot
drift is worth more than documentation that is merely good.

They live in the wheel, and a project gets its own copy, because an agent reads the project it is
working in. One generic folder with a harness-specific link means one set of files to keep true.

## What it rules out

- A skill may not name a tool that belongs to one harness, because only one harness can be tested
  here and the rest are documented as untested.
- There is no command that installs skills separately. They arrive with `decktalk init`, or with
  `--no-skills` they do not arrive at all.
- A project that wants newer skills is recreated or hand-copied. Nothing upgrades in place.

## What would change it

A plugin marketplace entry would let a harness install the skills without a project. It is a packaging
question rather than a change to what a skill says, and the test would still hold.
