# DeckTalk, the repository

This repository is the DeckTalk tool itself, and not a DeckTalk project. A project is what
`decktalk init` writes, and the `AGENTS.md` it writes there describes that shape instead.

`src/decktalk` is five layers deep, and a module imports only from a layer below its own or from
inside its own package. `tests/test_imports.py` enforces it, `ARCHITECTURE.md` explains it, and
`CONTRIBUTING.md` carries the generated module tree.
Run `uv run scripts/check.py` before calling anything done, which runs lint, types, every suite and
the generated-file checks. `--fast` runs lint, types and the unit tests alone, in a few seconds.
Never edit a generated file. Change its source and run its script, which `CONTRIBUTING.md` lists.
The six packaged skills live in `src/decktalk/skills/`, and `tests/test_skills.py` fails on a skill
that names a command, flag, key or JSON field the code does not have.
No check needs an ElevenLabs key, and no test may call a speech API.
Commit messages follow Conventional Commits, and the commit hook checks each one.
