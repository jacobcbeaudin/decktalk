---
name: decktalk-script
description: Write or rework the narration of a DeckTalk video so that it reads well out loud. Use when someone asks for a new narrated video, explainer, tutorial, product demo or lesson, adds a section, or changes what the voice says. It covers the outline before the prose, one idea per sentence, writing every number and symbol the way the voice must say it, holding an unconfirmed claim as a placeholder, and keeping a section small enough that a later edit costs one take. Do not use it for slide markup, which belongs to decktalk-slide, or for cue phrases, which belong to decktalk-cues.
compatibility: This is craft knowledge and nothing here needs a tool. Run `decktalk --help` for the commands and `decktalk schema` for their JSON.
metadata:
  ends_with: A script written for the ear, and a table of every spoken phrase beside the symbol it stands for.
---

# Write for the ear

The script is the spine. The voice reads `script.md` word for word, the word timings come from that
same text, and every picture waits for a phrase in it. So the script is written first, and it is
written to be heard rather than to be read.

## What the file is

A `## N. Title` heading opens section N, and one section is one cut of the film. Bracketed
directions are not spoken: `[beat]` is a short pause and `[pause 4]` is a four second pause. A
section's number, the scene its `[[section]]` table names, and the slide ids of its cues all agree,
so section 3 plays scene 3 and its cues are `3.1:open` and `3.2:result`.

## The craft

1. **Ask before writing.** The audience, the one promise the film makes, the length, the one idea
   each section leaves behind, and what the viewer does next. Write nothing until those are answered.
2. **Outline first, and show it.** Five to seven sections, one idea each, with a time budget in the
   heading, such as `## 1. The problem - 0:00 to 0:20`. Put the problem and the promise in the first
   thirty seconds. Wait for an answer before writing prose.
3. **One idea per sentence.** Name a thing before it appears. Say the consequence before the
   mechanism. A sentence a listener has to re-read is a sentence they cannot re-read.
4. **Write every number and symbol as the voice says it.** "Forty one", not "41". "Version two point
   three", not "v2.3". Read `references/spoken-math.md` before writing any equation, version number
   or identifier, because a symbol left on the page is read as a guess, cued by a phrase that cannot
   match, and shown in the captions as that guess.
5. **Hold every unconfirmed claim as a placeholder.** A number, a customer name, a comparison, a
   price, a date, an integration or a superlative is a claim. Write it in capitals, such as
   `[CUSTOMER_COUNT]`, so the voice refuses it, and record it in the project's claims ledger.
   `references/claims-ledger.md` is what the ledger holds.
6. **Keep the file clean.** No HTML comments, because they are sent to the voice. A bracketed
   direction sits with a space either side of it, and any other bracketed note inside a paragraph
   becomes a pause nobody asked for.
7. **Fit each section to its budget.** Cut sentences until the estimate matches the heading. A short
   section is cheap to change later, because the whole section's text is the unit a take is named by.
8. **Show the spoken forms.** End with a two-column table of each spoken phrase and the number,
   symbol or formula it stands for, and every placeholder still open, and ask for confirmation.

## What an edit costs

- A change to a section's spoken text re-voices that section, and nothing else.
- A change to a heading's title does not, as long as the section number stays.
- A change to the voice, the model or a `[voice]` key re-voices everything. Never make one without
  an answer from the author.
- A take is named by its own text, so renumbering a section keeps its take. Adding a first section
  pays for that section alone.

## Gotchas

- A cue phrase is matched against these words, so a script that writes "2.3" cannot be cued by
  "version two point three". Write the words.
- A respelling meant to fix the voice becomes the spoken word, the cue phrase and the caption
  together. Say so when you respell a name.
- A digit or a symbol left in a spoken sentence is reported as a finding, because the voice may say
  it as something other than the word a cue phrase matches.

## Hand off

Hand off to **decktalk-slide** once the outline is approved and the sections are written, so the
pictures can be written for the words. Tell it the section numbers, the scene each one plays and the
sentence each reveal must land on. Hand off to **decktalk-revise** instead when the project already
has a finished film and the ask is an update rather than a new script.
