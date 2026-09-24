# The skills are part of the product

## Decision

Six skills ship inside the wheel, under `src/decktalk/skills/`, and `template.SKILL_NAMES` lists them
in the order an author meets them.

| Skill | The craft it holds |
|---|---|
| `decktalk-script` | Writing narration for the ear, and spelling every number and symbol the way the voice must say it. |
| `decktalk-slide` | Writing a deck page whose reveals are large enough to be seen, cued, described and legible. |
| `decktalk-cues` | Choosing the spoken phrase each reveal waits for, and which occurrence to take when a phrase repeats. |
| `decktalk-build` | Deciding when a film is ready to be rendered, and never spending an author's money without an answer from them. |
| `decktalk-fix` | Reading a finding and making the smallest change that removes its cause. |
| `decktalk-revise` | Updating a film after a fact changed, re-voicing as few sections as possible. |

`decktalk init` copies all six into the project's `.agents/skills/`, with `.claude/skills` beside
them as a link, or as a copy on a platform that refuses links. `decktalk init --no-skills` leaves
them out. `tests/decktalk/template/test_template.py` holds them true.

## Why

An agent is a first-class user of this tool, not a bonus. The whole point of a video made from text
files is that something other than a person can edit it, and an agent that has to infer a command
line from a help screen will get it wrong in ways that cost the author money, because `narrate` and
`soundscape` both spend.

What a skill may hold follows from that. The CLI is the instruction set and it documents itself, so
a skill that repeated it would become a second and staler copy of it. Each skill therefore holds the
judgement a help screen cannot carry and points at `decktalk --help`, `decktalk schema` and
`decktalk status` for everything a machine can already answer. Every skill's `compatibility` line
says exactly that, and every one of them ends with a `Hand off` section naming the sibling that owns
the next question.

They live in the wheel, and a project gets its own copy, because an agent reads the project it is
working in. One generic folder with a harness-specific link beside it means one set of files to keep
true.

## The test that holds them

`tests/decktalk/template/test_template.py` reads the skills out of the package the way a reader of
the wheel reads them, and fails the build on any of the following.

- A skill names a command that is not one of the six stage verbs read from `Stage` plus the twelve
  beside them, or spells a whole command line, or spells a flag. That is
  `test_a_skill_names_only_commands_of_the_final_vocabulary`, and it is the guard that keeps a skill
  from going stale against the parser.
- Frontmatter carries a key outside `name`, `description`, `license`, `compatibility` and
  `metadata`, or a body runs past 200 lines.
- A skill has no `Hand off` section, or that section names no sibling.
- A reference file two skills both ship differs by one byte between them.
- A relative link leaves the skill's own folder, or points at a file that is not there.
- A skill names a vendor, a model or a harness. The list is checked by word, because a skill ships to
  whoever reads it.
- A skill writes a semicolon or a dash in its prose, which is the same house rule the documentation
  is held to.
- A skill carries a retired name.

The same file checks that every attribute `decktalk schema page` publishes is written by one of the
two packaged projects, so no knob ships untested, and that the `AGENTS.md` that `decktalk init`
writes names `decktalk --help` and `decktalk schema` and stays under 200 words.

## What it rules out

- A skill may not name a tool that belongs to one harness, because only one harness can be tested
  here and the rest are documented as untested.
- There is no command that installs skills separately. They arrive with `decktalk init`, or with
  `--no-skills` they do not arrive at all.
- A project that wants a newer set of skills is recreated or the folder is copied by hand. Nothing
  upgrades in place, which is the general rule in
  [the compatibility note](no-compatibility-while-alpha.md).
- A skill may not hold a table of flags, a list of settings keys or a copy of the page contract,
  because `decktalk schema` prints each of those and a copy would be the thing that drifts.

## What would change it

A plugin marketplace entry would let a harness install the skills without a project. That is a
packaging question rather than a change to what a skill says, and the test would still hold.
