"""Everything DeckTalk drives to make a picture and a sound: ffmpeg, ffprobe and headless Chromium.

`ffmpeg.py` finds the binaries and runs them, `audio.py` and `frames.py` are the two kinds of work
DeckTalk asks of them, `encode.py` holds the settings every output shares, `browser.py` drives
Chromium, `pagereport.py` reads what a page says about itself, and `origin.py` decides what a page
may load. The stages build on these and never spell a filter or a launch argument themselves.
"""

MILLISECONDS = 1000
"""Truth: the milliseconds in a second, which is the unit Chromium and the page both count in."""
