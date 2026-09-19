"""The typed build artifacts and their JSON files under `build/`.

These file shapes are part of the public contract: the page runtime and users' own scripts read
them. Field names match the JSON keys, every file is written atomically through `jsonio`, and one
module here owns each file.

    build/narration/<hash>.words.json   words.py       the time base everything shares
    build/narration/takes.json          takes.py       the take index and the narration clock
    build/cue-times.json                cue_times.py   every cue resolved against the words
    build/recordings/NN.json            recordings.py  what `record` did, judged and measured
    build/out/cuts.json                 cuts.py        where every section sits in the finished film
    build/progress.jsonl                progress.py    what a running build has done so far
"""

from __future__ import annotations

from .cue_times import CueTime, CueTimes
from .cuts import Cut, Cuts
from .progress import ProgressRow, append_row, read_rows, start_log
from .recordings import Luma, RecordingChecks, RecordingLog, file_digest, gap_time, input_hash, text_digest
from .takes import Take, Takes
from .words import Word, read_words, write_words

__all__ = [
    "CueTime",
    "CueTimes",
    "Cut",
    "Cuts",
    "Luma",
    "ProgressRow",
    "RecordingChecks",
    "RecordingLog",
    "Take",
    "Takes",
    "Word",
    "append_row",
    "file_digest",
    "gap_time",
    "input_hash",
    "read_rows",
    "read_words",
    "start_log",
    "text_digest",
    "write_words",
]
