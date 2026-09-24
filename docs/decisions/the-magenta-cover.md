# The recorder covers the page in magenta

## Decision

`decktalk-probe.js` covers the whole viewport in magenta from the page's first paint until the
narration clock starts, and the recorder reads the first clean frame after that run of covered
frames as narration t=0. The cover carries a keep-alive, a two pixel square in a corner that turns
for the whole recording and outlives the cover itself.

The probe is the recorder's instrument and never a tag in a deck. It is added as an init script, so
it runs before any script the page carries, and it is sealed onto the window afterwards so that a
deck cannot take its name. A deck author therefore writes no markup for any of this.

## Why

Chromium starts a screen recording at a moment nobody can predict, and the delay differs by platform
by hundreds of milliseconds. A recording that begins at an unknown time cannot be cut against a
narration clock. Anything the page could tell the recorder in JavaScript arrives on a different
clock than the frames do, so the answer has to be visible in the frames themselves.

Magenta is chosen because nothing in a slide is that colour by accident. The scan asks only whether
a frame sits between `record.cover_luma_min` and `record.cover_luma_max` with both chroma planes
above `record.cover_chroma_min`, which needs no tuning and no threshold a deck could trip.

The keep-alive exists because Chromium's screencast emits a frame only when the compositor paints
one, and a still cover paints once. It outlives the cover for a subtler reason. Playwright stamps
each frame by when it was swapped, rounded down to the capture grid, and a busy compositor swaps
later inside a frame than an idle one. If the motion stopped with the cover, the cover-off frame
would be stamped busy and every later reveal on a still page stamped idle, one or two frames early,
so reveals would record ahead of their words. It is mid-gray at three percent opacity, which moves a
pixel's luma by four steps at most and stays under every difference level `verify` reads.

## What it rules out

- A run that finds no cover does not refuse. It falls back to the first painted frame plus the
  settle, and failing that to `record.fallback_first_paint_seconds` plus the settle, marks the
  recording's `t0_guessed`, and prints a warning naming the section and the second it guessed.
  Reporting the guess rather than hiding it is the point, because a guessed start moves every reveal
  in that section together.
- The start is exact to one frame, which at the 25 frames per second the recorder captures at is
  40 ms. A tighter promise would need a different capture path, not a different cover.
- A page that never registers a scene is a separate refusal that names
  `decktalk-runtime.js`, because the catalog is missing rather than the cover.

## What would change it

A browser that timestamps its first frame against the page's own clock would remove the need for the
cover. No such interface exists in Chromium's recording path today.
