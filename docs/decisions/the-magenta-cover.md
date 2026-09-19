# The recorder covers the page in magenta

## Decision

The page draws a full-screen magenta cover over itself until it is ready to play. The recorder takes
the first frame after the cover disappears as narration t=0. The cover holds an element that is
always moving.

## Why

Chromium starts a screen recording at a moment nobody can predict, and the delay differs by platform
by hundreds of milliseconds. A recording that begins at an unknown time cannot be cut against a
narration clock. Anything the page could tell the recorder in JavaScript arrives on a different
clock than the frames do, so the answer has to be visible in the frames themselves.

Magenta is chosen because nothing in a slide is that colour by accident, so the scan that finds the
last covered frame needs no tuning and no threshold a deck could trip.

The moving element exists because Chromium sends a frame only when the page paints. A still page
under a still cover produces no frames at all, and the recorder would find no edge to measure. The
movement guarantees a frame every tick while the page waits.

## What it rules out

- A page that does not load the runtime cannot be recorded, because nothing draws the cover and
  there is no t=0 to find. That is reported as a certain finding rather than guessed at.
- The alignment is exact to one frame, which at 25 fps is 40 ms. A tighter promise would need a
  different capture path, not a different cover.

## What would change it

A browser that timestamps its first frame against the page's own clock would remove the need for the
cover. No such interface exists in Chromium's recording path today.
