# Cue phrases

## The shape of the file

```json
{
  "sections": {
    "3": {
      "min_seconds": 25,
      "cues": [
        { "cue": "3.1:bowl", "on": "a bowl" },
        { "cue": "3.1:ball", "on": "the ball", "offset": 0.15 },
        { "cue": "3.2:zero", "on": "zero", "occurrence": 2 },
        { "cue": "3.3:aside", "on": "one more thing", "verify": false }
      ]
    }
  }
}
```

| Key | What it means |
| --- | --- |
| `cue` | The wire id, which is a slide id, a colon, and the local name the page writes |
| `on` | The phrase from that section's narration that the moment lands on |
| `offset` | Seconds added to the match, positive for later and negative for earlier |
| `occurrence` | Which match to use when the phrase repeats, counting from one |
| `case_sensitive` | Whether the match respects case, and false is the default |
| `verify` | Set false to leave the cue out of the reveal check, which needs the author's agreement |
| `min_seconds` | The shortest the section may run, whatever the narration measures |

Two values of `on` name the section's own edges rather than a word. `"$start"` is zero seconds, and
`"$end"` is the end of the speech.

## Reading a wire id

The page writes a local name inside a slide, and the runtime joins the two. `data-in="bowl"` inside
`<template data-slide="3.1">` is `3.1:bowl`, and that is what this file carries. A slide may also
list a local name it serves from a handler, and those rows look exactly the same here.

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
- A phrase that spans a sentence boundary still matches, and the reveal then lands on a pause. Keep a
  phrase inside one sentence.
- A possessive is part of its word, so `project` does not match `project's`. Quote the phrase from
  the script rather than retyping it.
- A phrase in digits never matches a script written in words, and the script is always written in
  words.

## Repeated phrases

A phrase that occurs more than once is reported with every second at which it occurs. Answer it by
setting `occurrence`, or better by choosing a phrase that occurs once. Never leave it unanswered,
because the first match is usually too early.

## Offsets

- Nothing, for a reveal that should land on the word.
- A tenth to a fifth of a second, for a reveal that points at something the voice has just named, so
  the eye follows the ear.
- A small negative offset, for text the viewer reads along with the voice, so the line is there as
  the reading starts.
- Never an offset above half a second. A reveal that needs more than that belongs on a different
  word.

## Pacing

| Measurement | What it means |
| --- | --- |
| A gap under two seconds | Two reveals fight for one moment. Merge them, or move one to the next slide. |
| A gap over twenty seconds | The picture is still while the voice talks. Add a reveal, or split the slide. |
| A section averaging under five seconds a cue | The section is crammed. Split it, or cut reveals. |

A lesson reads best at one reveal every ten to fifteen seconds. A product demo can run faster. Show
the numbers to the author and let the author decide.

## What never fixes a late reveal

- Changing how long a slide rests in the preview. No recording reads that.
- Raising a limit in the project file.
- Leaving a cue out of the reveal check when it is genuinely late.
- Passing a flag that allows an unresolved or unknown cue through.

A late reveal is fixed by changing the phrase, the offset, or the effect that is still moving when
the next reveal begins.
