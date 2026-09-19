# Every code, its cause and its smallest fix

A verdict is matched by its `code` and printed by its `label`. Nothing parses a label.

## Certain findings

| Code | Label | Usual cause | Smallest fix |
| --- | --- | --- | --- |
| `PAGE_ERROR` | PAGE ERROR | The page threw while recording, most often a backtick, a `${` or a single backslash inside a `render` template literal | Escape the character, then run `decktalk screenshots --page deck/index.html --json` and read the error the page prints |
| `STALLED` | STALLED | The page stopped drawing, usually an animation that waits for something that never arrives | Remove the wait, or resolve the readiness promise the page set |
| `TRUNCATED` | TRUNCATED | The recording is shorter than the narration it must cover | Raise `record_margin_seconds` on the section, or shorten the narration |
| `NO_COVER` | NO COVER | The recorder never saw its start cover, so t=0 could not be found | Confirm the page loads the runtime, and record the section again |
| `BLACK` | BLACK | The section opens on a frame with nothing drawn | Give the first slide an element that shows at the mount, or check that the scene number matches the section |
| `SPEECH_AT_CUT` | SPEECH AT CUT | The cut lands while the voice is still speaking | Raise the section's `tail_seconds` or `min_tail_seconds` in `[narration]`, or end the section's last sentence earlier |
| `POP_AT_CUT` | POP AT CUT | A section that sets `seamless` does not open on the previous picture | Make the first slide match the previous section's last frame, or drop `seamless` |
| `OFF_CUE` | OFF CUE | The reveal landed outside the allowed distance from its word | Read `offset_ms`. A negative value means an earlier reveal was still moving, so shorten its `data-duration`. A positive value means the phrase is late in the sentence, so move it earlier |
| `NO_CHANGE` | NO CHANGE | The checker saw no difference across the cue, usually a reveal that is too small, a typed line, or a colour change of the same brightness | Make the reveal larger, dim what surrounds it, or fade a whole container in on the cue |
| `UNRESOLVED` | UNRESOLVED | The phrase in `cues.json` is not in that section's narration word for word | Copy the phrase from `narrate.sections[].request.text` in `decktalk narrate --dry-run --json` |
| `UNKNOWN_CUE` | UNKNOWN CUE | `cues.json` names a cue id that the page never carries | Add the `data-cue` to the element, or remove the entry |
| `UNCUED_ELEMENT` | UNCUED ELEMENT | An element carries a `data-cue` that `cues.json` never names, so it appears at the mount instead of on a word | Add the entry to `cues.json`, or drop the attribute |
| `KATEX_ERROR` | KATEX ERROR | KaTeX refused a `data-tex` value, which shows in red | Inside a `render` template literal write every backslash twice, and inside a `<template data-slide>` write it once |
| `KATEX_NOT_LOADED` | KATEX NOT LOADED | The page has `data-tex` and `window.katex` never appeared | Copy the two local KaTeX tags into the page head, above the runtime |
| `MISSING` | MISSING | A file the command needs is not there | Write the file, or run the stage that writes it |
| `UNREADABLE` | UNREADABLE | A file is there and will not parse | Repair the file, and never delete the author's work |
| `INCONSISTENT` | INCONSISTENT | Two files parse and contradict each other, such as a section in one and not the other | Reconcile them, keeping the section numbers |
| `INCONSISTENT` | INCONSISTENT | A section is stale and `--only` did not name it, so the film keeps the recording made before the page changed | Add that section to `--only`, or tell the user the film keeps the older recording and get an answer |
| `PLACEHOLDER` | PLACEHOLDER | A section still holds a `[CAPITAL]` placeholder, which a voiced run would read out word for word | Write the real words in `script.md`, or pass `--allow-placeholders` to voice the section as it stands |

## Uncertain findings

An uncertain finding fails only under `--strict`. Decide each one with the user.

| Code | Label | Usual cause | Smallest fix |
| --- | --- | --- | --- |
| `BLACK_UNSURE` | BLACK? | A very dark frame, which may be a dark slide by design | Look at the frame, and lighten the slide when it is not deliberate |
| `SLATE` | SLATE? | A clip section played a titled slate, because the clip file is not there | Put the clip at the path the section names, or accept the slate |
| `SPOKEN_SYMBOL` | SPOKEN SYMBOL? | A spoken section still holds a digit or a symbol the voice may say as something other than the word a cue matches | Write the number or the symbol as words in `script.md` |
| `THIN_CHANGE` | THIN CHANGE? | The reveal passed, and it changed under about 0.3 percent of the frame, which is three times the floor | Make the element larger, add a second cued element beside it, or dim the rest |
| `SHORT_SECTION` | SHORT SECTION? | The section's speech ends before the `min_seconds` its visuals need, so the last reveal has no time to read | Write more narration for that section, or lower its `min_seconds` in `cues.json` |
| `LOUDNESS_MISS` | LOUDNESS MISS? | The normalized film still sits off the integrated target or above the true-peak ceiling | Look for one section far louder or quieter than the rest, and check `[mix.loudness]` |

## Passing verdicts

`CHANGED`, `QUIET`, `OK`, `SKIPPED` and `NOTE` are never findings. A `NOTE` row carries information,
such as a phrase that occurs more than once, and it deserves an answer even though it does not fail.

## Skip reasons

A skipped row measured nothing, so it does not fail an exit code. Read these yourself.

| Reason | What it means | What to do |
| --- | --- | --- |
| `NO_CATALOG` | The page never built its catalog, which means the page did not run | Run `decktalk screenshots --page deck/index.html --json` and read the page error |
| `NO_SLIDE` | No slide on the scene owns that cue id | Give the cue an id that starts with its slide's id, or list it in the slide's `owns` |
| `NO_CUES` | The section has no cues at all | Add cues, or accept a section with no reveals |
| `CLIP` | The section plays a video file rather than a page | Nothing, because a clip has no reveals |
| `OPTED_OUT` | The cue sets `"verify": false` | Confirm with the user that this is still deliberate |
| `AT_SECTION_START` | The cue lands at the very start of the section, so there is no earlier frame | Move the phrase later in the sentence |
| `SECTION_NOT_ASSEMBLED` | The section is not in the final video | Build that section before verifying it |
| `REFERENCE_CLAMPED` | No frame fits before the cue, usually inside a fade-in | Move the cue later, or shorten the dip |
| `TOO_CLOSE_TO_END` | The cue sits too near the end of the video to measure | Move the phrase earlier |
| `NO_CLICK` | A build without voice gave no audio landmark to measure against | Measure this cue on the voiced build |

## Errors, which are not findings

An envelope with `error` not null and exit code 3 means DeckTalk could not run. `CONFIG` means a
file will not parse or a key is unknown. `MISSING_INPUT` means a file the command needs is absent.
`PROVIDER` means the speech service refused or the key is not set. `TOOL` means the browser or
ffmpeg failed. `INTERNAL` means a bug, which is worth reporting with `decktalk doctor --report`,
whose block carries versions and paths and no secret. Stop on each of these and tell the user.

`error.hint` is the smallest next action, and `error.path` and `error.line` name the file and the
line when the raiser knew them. Read all four before changing anything.
