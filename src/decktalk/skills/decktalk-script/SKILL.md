---
name: decktalk-script
description: Plan and write script.md for a DeckTalk narrated video, in an empty directory or in an existing project. Use when someone asks for a new narrated video, explainer, tutorial, product demo or lesson, adds a section, or changes what the voice says. It creates the project, writes the outline before the script, writes numbers and math the way the voice must say them, holds every unconfirmed claim as a placeholder, and fits each section to its time budget with narrate --dry-run. Do not use it for slide markup, which belongs to decktalk-slide, or for cue phrases, which belong to decktalk-cues.
compatibility: Requires the decktalk command on PATH and a writable project directory. Every check in this skill is text and JSON, so a model that cannot read an image can follow all of it.
---

# Write the script

The script is the spine of a DeckTalk video. The voice reads `script.md`, and every picture waits
for a word in it. Write the script first, and write it for the ear.

Run every command from the project directory, or add the project flag to each one. Read the `--json`
envelope and never the printed table. Exit code 0 means nothing was found, 1 means a finding with
`error` null, 2 means the command line was wrong, and 3 means DeckTalk could not run, in which case
stop and tell the user what `error.message` says.

## Steps

1. **Make the project when there is none.** In an empty directory run `decktalk init my-video --json`
   and read `written` for the files it made. It writes `decktalk.toml`, `script.md`, `cues.json`, a
   deck page under `deck/`, the runtime, the KaTeX copies, an `AGENTS.md` and the packaged skills.
   `decktalk init my-video --example lesson --json` writes a finished project to read instead of the
   three-section starter. In an existing project run `decktalk status --json` and read
   `status.sections` for the sections that already exist.
2. **Hold the rule that ties the four files together.** The section number in the `## N.` heading of
   `script.md`, the `number` of the matching `[[section]]` table in `decktalk.toml`, the `scene` that
   table names on the page, and the prefix of every cue id for that section all agree. Section 3
   plays scene 3 and owns the cues `3.1open` and `3.2result`.
3. **Ask before writing.** Ask the user for the audience, the one promise the video makes, the total
   length, and the one idea each section must leave behind. Ask what the viewer does next.
4. **Write the outline and show it.** Five to seven sections, one idea each, with a time budget in
   every heading, such as `## 1. The problem — 0:00 to 0:20`. Put the problem and the promise in the
   first thirty seconds. Show the outline to the user and wait for an answer before writing prose.
5. **Write `script.md` for the ear.** One idea per sentence. Name a thing before it appears. Write
   every number, symbol and formula as the voice must say it, and read
   `references/spoken-math.md` before writing any equation, version number or identifier.
6. **Place a placeholder for every unconfirmed claim.** A claim is any number, customer name,
   comparison, price, date, integration or superlative. Write it as `[CUSTOMER_COUNT]` in capitals so
   the voice refuses it, and record it in the project's claims ledger. Read
   `references/claims-ledger.md` for the ledger's columns and rules.
7. **Keep the file clean.** Write no HTML comments, because they are sent to the voice. Put spaces
   around a bracketed direction such as `[beat]` or `[pause 2]`. It may sit inside a paragraph, as
   the starter does, and it becomes a pause in the text that is sent. Any other bracketed note
   inside a paragraph is refused, because it would become a pause nobody asked for.
8. **Add or update the `[[section]]` table** in `decktalk.toml` for each new section, with the same
   `number`, its `page` and its `scene`. Leave every existing `number` alone.
9. **Fit each section to its budget.** Run `decktalk narrate --dry-run --json`. Read each row of
   `narrate.sections` for `estimated_seconds`, `characters_sent` and `placeholders`, and read
   `narrate.totals.estimated_cost` for what the whole run would cost. Cut sentences until every
   section fits its heading's budget. This command sends nothing and spends nothing, and a voiced run
   starts only after you show the author that figure and wait for them to approve it.
10. **Show the spoken forms.** End by printing a two-column table of each spoken phrase and the
    number, symbol or formula it stands for, and ask the author to confirm it. Also list every
    placeholder that is still open.

## Rules

- Short sections cost less to change. One idea per section keeps a later edit to one take.
- Never pass `--force`. It throws away paid takes.
- Never run a build without voice in a project that already holds voiced takes, because the command
  refuses it and the only way past is `--force`.
- Never change the voice id, the model or a `[voice]` key while writing the script, because each of
  those re-voices every section. `[voice] price_per_1000_characters` is the exception: it is what the
  plan charges, and setting it only makes the dry run quote a price.
- A change to a section's text re-voices that section. A change to a heading's title does not, as
  long as the section number stays.

## Gotchas

- A cue phrase must match the script word for word, so a script that says "2.3" cannot be cued by
  "version two point three". Write the words.
- The voice says what is written, so a respelled word such as "kubectl" becomes the spoken word, the
  cue phrase and the caption together.
- The first spoken section is the one place a take never moves. Adding a new first section re-voices
  the old one.
- `decktalk narrate --dry-run --json` prices the run at `[voice] price_per_1000_characters`. When
  that key is unset the cost keys are null, so quote characters instead and let the user price them.
- A digit or a symbol left in a spoken sentence is reported as `SPOKEN SYMBOL?`, because the voice
  may say it as something other than the word a cue phrase matches. Write the words.

## Hand off

The chain is decktalk-script, then decktalk-slide, then decktalk-cues, then decktalk-build, and
decktalk-fix whenever a command reports a finding.

Hand off to **decktalk-slide** once the outline is approved and `script.md` holds the sections, so
the pictures can be written for the words. Tell it the section numbers, the scene each one plays,
the cue id prefix for each section, and the sentence each reveal must land on. Hand off to
**decktalk-revise** instead when the project already has a finished video and the user is asking for
an update rather than a new script.
