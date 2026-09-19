# A speech provider is a two-method protocol

## Decision

`SpeechProvider` is a protocol with two methods, and a provider is built from a `VoiceContext`
rather than from a project. A provider receives one section of text with its settings and returns
audio plus a start and an end time for every word. It knows nothing about `decktalk.toml`, the build
directory or any stage.

## Why

The one thing DeckTalk needs from a voice is word timings, because the cut is made on words. Every
other thing a speech service offers is a setting to pass through. Writing the boundary that narrowly
means a new provider is one file and its test, and it means the caching, the pricing and the cue
resolution above it never have to learn a provider's shape.

Building from a `VoiceContext` rather than a project is what keeps `speech/` in the leaves layer. A
provider that took a `Project` would drag the whole project model below the model layer, and the
layering test would refuse it.

## What it rules out

- A provider with no word timings cannot be added behind this protocol. It would be a different
  feature, not a second provider.
- DeckTalk loads no plugins and reads no entry points. A provider arrives as a pull request and is
  registered by name in `speech/__init__.py`, so what a build can call is what a reader can see.
- A provider cannot reach the build directory, so it cannot cache on its own. Caching is one rule,
  above the boundary, keyed by a hash of the text, the voice, the model and the settings.

## What would change it

A local speech model with word timings would be the first provider that is not an HTTP service. The
protocol was written to accept one, and the registry is the only place it would be named.
