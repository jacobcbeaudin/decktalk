# Cue phrases

## The shape of the file

```json
{
  "sections": {
    "3": {
      "min_seconds": 25,
      "cues": [
        { "cue": "3.1bowl", "on": "bowl" },
        { "cue": "3.1ball", "on": "a ball", "offset": 0.15 },
        { "cue": "3.2zero", "on": "zero", "occurrence": 2 },
        { "cue": "3.3aside", "on": "one more thing", "verify": false }
      ]
    }
  }
}
```

| Key | What it means |
| --- | --- |
| `cue` | The id, which must equal a `data-cue` value on the page that the section plays |
| `on` | The phrase from that section's narration that the reveal lands on |
| `offset` | Seconds added to the match, positive for later and negative for earlier |
| `occurrence` | Which match to use when the phrase repeats, counting from one |
| `case_sensitive` | Whether the match respects case, and false is the default |
| `verify` | Set false to exclude the cue from the reveal check, which needs the author's agreement |
| `min_seconds` | The shortest the section may run, whatever the narration measures |

Two values of `on` name the section's own edges rather than a word. `"$start"` is zero seconds, and
`"$end"` is the end of the speech.

## Choosing the phrase

A good phrase starts on the word that names the thing that appears, is two or three words long, and
occurs once in its section.

| Instead of | Write |
| --- | --- |
| `"here"` | `"the inbox"` |
| `"and then"` | `"the second step"` |
| `"x"` | `"x squared"` |
| `"two"` | `"two knobs"` |
| `"it"` | the noun the sentence used before "it" |

- A single common word such as "the", "one" or "it" matches early and lands on the wrong sentence.
- A phrase that spans a sentence boundary still matches, but the reveal then lands on a pause. Keep a
  phrase inside one sentence.
- A possessive is part of its word, so `project` does not match `project's`. Quote the phrase from
  the script rather than retyping it.
- A phrase in digits never matches a script written in words, and the script is always written in
  words.

## Repeated phrases

`decktalk align --json` puts a row in `align.sections[].notes` when a phrase occurs more than once,
naming every second at which it occurs. Read that row and set `occurrence` to the match that belongs
to the reveal. Never leave the note unanswered, because the first match is usually too early.

## Offsets

- Nothing, for a reveal that should land on the word.
- `0.1` to `0.2`, for a reveal that points at something the voice has just named, so the eye follows
  the ear.
- A small negative offset, for text the viewer reads along with the voice, so the line is there as
  the reading starts.
- Never an offset above half a second. A reveal that needs more than that belongs on a different
  word.

## Pacing

Compute the gap between neighbouring cues from the seconds in `align.sections[].cues`.

| Measurement | What it means |
| --- | --- |
| A gap under two seconds | Two reveals fight for one moment. Merge them, or move one to the next slide. |
| A gap over twenty seconds | The picture is still while the voice talks. Add a reveal, or split the slide. |
| A section averaging under five seconds per cue | The section is crammed. Split it, or cut reveals. |

A lesson reads best at one reveal every ten to fifteen seconds. A product demo can run faster. Show
the numbers to the user and let the user decide.

## What never fixes a late reveal

- Editing the seconds in a slide's `preview` object. Those drive the browser preview only.
- Raising a `[verify]` limit in `decktalk.toml`.
- Setting `"verify": false` on a cue that is genuinely late.
- Passing `--allow-unresolved-cues` or `--allow-unknown-cues`.

A late reveal is fixed by changing the phrase, the offset or the animation that is still moving when
the next reveal begins.
