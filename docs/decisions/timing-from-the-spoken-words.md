# Timing comes from the spoken words

## Decision

Every time DeckTalk computes is derived from the start and end times of spoken words. A cue names a
phrase from the narration, never a second. The speech provider returns a time for every word, and
that list is the only clock in the system.

## Why

The thing that makes a narrated video expensive to change is that a picture is pinned to a second.
Rewrite one sentence and every picture after it is wrong, so the fix is to re-cut the whole video by
hand. If a picture is pinned to a phrase instead, the same rewrite moves the pictures with it and
nobody touches a timeline.

This also makes the video checkable. `verify` knows which word a reveal was supposed to follow, so a
late reveal is a number in a table rather than something a person has to notice.

## What it rules out

- A speech provider with no word timestamps cannot be used, however good the voice is. This is the
  first question asked of any new provider.
- A cue cannot be placed between two words that are never spoken, so a picture that belongs to no
  phrase has to earn a phrase in the script.
- A section with no narration resolves no cues, and `align` reports each one rather than writing a
  cue list the recorder would then play blind.

## What would change it

A provider that returns phoneme or character timings rather than word timings would widen what a
cue may name. Nothing else would.
