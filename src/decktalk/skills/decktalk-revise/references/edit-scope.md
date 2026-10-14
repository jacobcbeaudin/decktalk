# What an edit costs, and what it drags in

A take is keyed by the exact text a turn sends to its voice, and a section one voice reads is one
turn. An edit that changes that text pays for that turn again. An edit that changes only the picture pays nothing and records again.

## The cost of each edit

| Edit | Re-voices | Records again | Notes |
| --- | --- | --- | --- |
| A word in a section's spoken text | The turn it sits in | That section | A turn's whole text is the unit, so a one-word change costs the turn |
| A bracketed direction inside a paragraph | That section | That section | A bracket changes the text that is sent |
| An HTML comment in the script | That section | That section | `narrate` refuses a comment in the spoken text, because the voice would read it out, so remove it |
| A section heading's title | Nothing | Nothing | The take is named by the spoken text, not the heading. The heading names the chapter when the project file names none |
| A section's chapter in the project file | Nothing | Nothing | It names the chapter and the slate |
| A slide, a style or an image on a page | Nothing | Every section that plays that page | A page change is free and still needs the picture again |
| A cue phrase | Nothing | That section | The reveal moves, so the recording moves |
| An offset or an occurrence | Nothing | That section | |
| A timing key on a section | Nothing | That section | |
| The voice, the model or any voice setting | Every turn that voice reads | Every section that voice reads in | Never change one without an answer |
| Adding a turn, with a `[voice NAME]` line | The new turn, and the turn it splits | That section | The turns beside it keep their takes |
| Changing one voice's id, model or setting | Every turn that voice reads | Every section that voice reads in | Other voices keep their takes |
| Changing `[narration] voice` | Every turn of a section that names no voice | Those sections | A section that names its voices keeps them |
| Renaming a voice, with every `[voice NAME]` line that names it | Nothing | Nothing | A voice's name is never part of a take's digest |
| A `[voice NAME]` line naming the voice already reading | Nothing | Nothing | It starts no new turn, though a section that names a voice must name one before every turn |
| A section number | Nothing | The sections involved | A take is named by its own text, so it follows its words and not its number |
| Inserting a new first section | The new section only | The new section and every renumbered one | Every section gets the same lead, so the old opening keeps its take and lands the same way |

## The neighbours an edit drags in

When a rebuild is limited to some sections, name every section on this list and not only the one you
touched.

1. Every section whose table names the scene you changed, because a recording is kept only while that
   section's own scene markup is unchanged. An edit outside every scene, to the head, a stylesheet or
   a script of the page's own, reaches every scene, so it stales every section of that page. A
   section left out is reported as inconsistent and the film keeps the older recording.
2. The section after any section you changed, when its page reads the previous section's spoken words
   or opens on the previous section's last frame.
3. Every section that shows an image you replaced.
4. Every section whose narration changed, because its length changed and its cues moved with it.

## Proving the scope

Before anything is spent, read which sections would be voiced.

- A section whose text is unchanged is already on disk and costs nothing.
- A section that could not be checked at all is priced at its worst case, so quote both figures rather
  than the certain one alone.
- When a section you did not touch would be voiced, stop. Either its text changed by accident, or a
  voice setting changed, or the takes directory is missing from this checkout.
- A project that has only ever been built on placeholders is the exception. Every section there is
  new, because no take has been voiced yet, and that is expected rather than a scope failure.

## Keeping the previous film

Ask for a dated second copy of the film before a revision build, so the film the author already
approved survives the rebuild.

## The change note

One short note at the end of a revision: the sections changed, the claims updated, the images
replaced, what was spent, and every finding still open. It is what makes the next revision cheap,
because it says what was already checked and when.
