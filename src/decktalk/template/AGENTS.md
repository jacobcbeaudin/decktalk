# __TITLE__

A DeckTalk project: `script.md` is what the voice says, `decktalk.toml` lists one `[[section]]` per
script section, `cues.json` says which spoken phrase each moment lands on, and `deck/` holds the
pages. The section number, the scene its `[[section]]` names and the slide ids of its cues agree.

`decktalk --help` is the instruction set. `decktalk schema` prints every command, flag, finding and
result, `decktalk schema settings` and `decktalk schema page` print the two knob tables, and
`decktalk config explain KEY` explains one knob. Every command takes `--json` and prints one object.
`decktalk status` says where the project stands.

The craft a command line cannot teach is in `.agents/skills/`: writing for the ear, spoken math, cue
phrases, slide patterns, reading a finding, and the scope of a revision.
