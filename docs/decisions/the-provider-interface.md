# The speech seam is internal, and it asks a voice for one thing

## Decision

ElevenLabs is the only speech provider DeckTalk has. The seam above it is internal: `SpeechProvider`,
`SpeechRequest` and `VoiceContext` live in `src/decktalk/speech/`, none of them is exported from
`decktalk`, and no result model carries a provider. `PROVIDERS` in `speech/__init__.py` is a written
out mapping of one name to one factory, and `[voice] provider` may name a key of it.

A provider is built from a `VoiceContext`, which carries five values and no project: `secrets`,
`api_base`, `context_chars`, `speech_timeout_seconds` and `sound_timeout_seconds`. The one thing
DeckTalk asks of a voice is audio with a start and an end time for every word.

## Why

The cut is made on words, so word times are the whole requirement. Everything else a speech service
offers is a setting passed through. Writing the boundary that narrowly means the caching, the
pricing and the cue resolution above it never have to learn a provider's shape.

The seam is internal because a second provider is not a promise DeckTalk is ready to make. What the
seam is kept for is the test it buys: a stage test runs the real `narrate` against a provider that
spends nothing, and that is worth a protocol on its own.

Building from a `VoiceContext` rather than from a `Project` is what keeps `speech/` in the leaves
layer. A provider that took a `Project` would drag the whole project model below the model layer,
and the layering test would refuse it. It also means a provider is built in a test from four numbers
and a source of secrets, which is what makes that stage test cheap enough to keep in the fast suite.

The key is a `Secret` and `[elevenlabs] api_base` must be an https URL on an ElevenLabs host unless
`DECKTALK_ALLOW_ANY_API_BASE` is set, because the environment is the machine's own and a project
file is not, so a file alone can never redirect a credential. The voice id is not a secret. It names
which voice reads the script, the way a model name names which model answers, and it travels in the
request and in the take hash.

## What it rules out

- A provider with no word timings cannot sit behind this protocol. It would be a different feature,
  not a second provider, and [the timing note](timing-from-the-spoken-words.md) says why.
- No published type names a provider. `decktalk.__all__` carries no `SpeechProvider`, no
  `SpeechRequest` and no `VoiceContext`, and no result field holds one, so nothing a caller reads
  back from a run can be dispatched on a provider's identity.
- DeckTalk loads no plugins and reads no entry points. A provider arrives as a pull request and is
  named in `PROVIDERS`, so what a build can call is exactly what a reader can see.
- A provider cannot reach the build directory, so it cannot cache on its own. Caching is one rule
  above the boundary, keyed by a hash of the text, the voice, the model and the settings.
- A provider is never handed a path, a stage or a settings object. It receives a `SpeechRequest` and
  answers with bytes and a list of `Word`.

## What would change it

A local speech model with word timings would be the first provider that is not an HTTP service. The
protocol was written to accept one, and `PROVIDERS` is the only place it would be named. Publishing
the seam is a separate decision, and it would only be worth making once there were two providers
worth choosing between.
