"""The typed build artifacts and their JSON files under `build/`.

These file shapes are part of the public contract: the page runtime and users' own scripts read
them. Field names match the JSON keys, every file is written atomically through `jsonio`, and one
module here owns each file.

    build/narration/<hash>.words.json   words.py       the time base everything shares
    build/narration/takes.json          takes.py       the take index and the narration cache
    build/narration/timeline.json       timeline.py    section and word times in narration.mp3
    build/cue-times.json                cue_times.py   every cue resolved against the words
    build/recordings/NN.json            recordings.py  what `record` did, judged and measured
"""

from __future__ import annotations

from .cue_times import CueTime, CueTimes
from .recordings import Luma, RecordingChecks, RecordingLog, gap_time
from .takes import Take, Takes
from .timeline import Timeline, TimelineSection
from .words import Word, read_words, write_words

__all__ = [
    "CueTime",
    "CueTimes",
    "Luma",
    "RecordingChecks",
    "RecordingLog",
    "Take",
    "Takes",
    "Timeline",
    "TimelineSection",
    "Word",
    "gap_time",
    "read_words",
    "write_words",
]
