# What an edit costs, and what it drags in

A take is keyed by the exact text a section sends to the voice. An edit that changes that text pays
for that section again. An edit that changes only the picture pays nothing and records again.

## The cost of each edit

| Edit | Re-voices | Records again | Notes |
| --- | --- | --- | --- |
| A word in a section's spoken text | That section | That section | The whole section's text is the unit, so a one-word change costs the section |
| A bracketed direction inside a paragraph | That section | That section | A bracket changes the text that is sent |
| An HTML comment in the script | That section | That section | The comment is sent to the voice, so remove it |
| A section heading's title | Nothing | Nothing | The heading names the take file and the chapter, not the spoken text |
| A section's `chapter` in `decktalk.toml` | Nothing | Nothing | It names the chapter and the slate |
| A slide, a style or an image on a page | Nothing | Every section that plays that page | A page change is free and still needs the picture again |
| A cue phrase in `cues.json` | Nothing | That section | The reveal moves, so the recording moves |
| An offset or an `occurrence` | Nothing | That section | |
| A `[[section]]` timing key | Nothing | That section | |
| The voice id, the model or any `[voice]` key | Every section | Every section | Never change one without the user's approval |
| A section number | Nothing | The sections involved | A take is named by its content hash, so it follows its text and not its number |
| Inserting a new first section | The new section only | The new section and every renumbered one | Every section gets the same lead, so the old opening keeps its take and lands the same way |

## The neighbours an edit drags in

When the build is limited with `--only`, name every section on this list and not only the one you
touched.

1. Every section whose `[[section]]` table names the scene you changed, because a recording is kept
   only while that section's own `[data-scene]` markup is unchanged. An edit outside every scene, to
   the head, a stylesheet, a script or the runtime file, reaches every scene, so it stales every
   section of that page. A section left out of `--only` is reported as `INCONSISTENT`.
2. The section after any section you changed, when its page reads the previous section's spoken words
   or opens on the previous section's last frame.
3. Every section that shows a screenshot you replaced.
4. Every section whose narration changed, because its length changed and its cues moved with it.

## Proving the scope

Run `decktalk narrate --dry-run --json` before spending anything. It sends nothing. Read
`narrate.sections[]`.

- A row whose `status` is `synthesize` will be paid for.
- A row whose `status` is `cached` will not, because the take of that exact text is already on disk.
- A row whose `status` is `unknown` could not be checked at all, because no voice was set up. Its
  characters are counted in `narrate.totals.characters_unchecked` and priced into
  `narrate.totals.most_it_can_cost`, so quote both figures rather than the certain one alone.
- When a row you did not touch plans as `synthesize`, stop. Either its text changed by accident, or
  a `[voice]` key changed, or the take cache is missing because this is a fresh clone or a new
  worktree and `[narration] cache_dir` does not point at a shared take directory.
- A project that has only ever built without voice is the exception. Every row there plans as
  `synthesize` with the reason `only a take without voice exists`, because no take has been paid for
  yet, and that is expected rather than a scope failure.

## Keeping the previous cut

Set `timestamped_copy = true` in the `[output]` table of `decktalk.toml` before a revision build. The
build then writes a second mp4 named with the date and time beside the plain one, so the cut the user
already approved survives the rebuild.

## The change note

Finish a revision with a note that lists, in this order, the sections changed, the claims updated
with their new sources and dates, the images replaced with their capture dates, the characters sent,
and every finding still open. The note is what makes the next revision cheap.
