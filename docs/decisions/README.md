# Decisions

One note per choice that the code cannot explain by itself. Each says what was decided, why, what it
rules out, and what would change it. These are engineering notes for whoever reads the tree. The
product documentation is at [docs.decktalk.ai](https://docs.decktalk.ai), and
[ARCHITECTURE.md](../../ARCHITECTURE.md) is the overview these notes hang from.

| Note | Decides |
|---|---|
| [Timing comes from the spoken words](timing-from-the-spoken-words.md) | Every time in the system is derived from word timestamps |
| [The recorder covers the page in magenta](the-magenta-cover.md) | How narration t=0 is found in a recording |
| [The pipeline is six stages](the-six-stages.md) | Why these six, why `soundscape` follows `record`, and why `check` is a command |
| [The speech seam is internal, and it asks a voice for one thing](the-provider-interface.md) | What DeckTalk needs from a voice, and what it refuses to need |
| [The skills are part of the product](the-skills-contract.md) | Why six skills ship in the wheel and a test holds them true |
| [No compatibility while DeckTalk is alpha](no-compatibility-while-alpha.md) | Why nothing migrates and no name is kept alive |
