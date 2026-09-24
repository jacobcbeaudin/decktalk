"""The typed build artifacts and the files they are written to.

These shapes are part of the published contract, because a later stage, a user's own script and the
page runtime all read them. One module owns each file, every model is frozen, every field name is
the JSON key, and `Stored` is the one place a file is read from disk or written to it.

    build/narrate/<hash>.words.json   words.py       the time base everything shares
    build/narrate/takes.json          takes.py       the take index and the narration clock
    build/cue-times.json              cue_times.py   every cue resolved against those words
    build/recordings/NN.json          recordings.py  what `record` did, judged and measured
    build/final/cuts.json             cuts.py        where every section sits in the finished film

What a run is doing while it does it is not an artifact. That is the event stream, and a run's
lines are appended to `build/events/<run>.jsonl` by a subscriber rather than written here.
"""

from __future__ import annotations

from decktalk.artifacts.cue_times import CUE_AT, CUE_SEPARATOR, PREVIEW_ALIAS, CueTimes
from decktalk.artifacts.cuts import Cut, Cuts
from decktalk.artifacts.recordings import (
    Luma,
    RecordingChecks,
    RecordingLog,
    file_digest,
    input_hash,
    text_digest,
)
from decktalk.artifacts.stored import Stored
from decktalk.artifacts.takes import (
    PLACEHOLDER_PREFIX,
    TAKE_DIGITS,
    PlaceholderInputs,
    Take,
    TakeInputs,
    Takes,
    is_placeholder,
    take_file,
)
from decktalk.artifacts.words import WORDS_SUFFIX, Words, words_file

__all__ = [
    "CUE_AT",
    "CUE_SEPARATOR",
    "PLACEHOLDER_PREFIX",
    "PREVIEW_ALIAS",
    "TAKE_DIGITS",
    "WORDS_SUFFIX",
    "Cut",
    "CueTimes",
    "Cuts",
    "Luma",
    "PlaceholderInputs",
    "RecordingChecks",
    "RecordingLog",
    "Stored",
    "Take",
    "TakeInputs",
    "Takes",
    "Words",
    "file_digest",
    "input_hash",
    "is_placeholder",
    "take_file",
    "text_digest",
    "words_file",
]
