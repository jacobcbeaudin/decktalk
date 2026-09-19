"""Everything DeckTalk drives to make a picture and a sound: ffmpeg, ffprobe and headless Chromium.

`ffmpeg.py` finds the binaries and runs them, `audio.py` and `frames.py` are the two kinds of work
DeckTalk asks of them, `encode.py` holds the settings every output shares, and `browser.py` drives
Chromium. The stages build on these and never spell a filter or a launch argument themselves.
"""
