"""The typed build artifacts and the files they are written to.

The files are part of the published contract, because a later stage, a user's own script and the
page runtime all read them, and the reference page documents them as JSON. The classes are not, so
`decktalk.__all__` leaves them out. One module owns each file, every model is frozen, every field
name is the JSON key, and `Stored` is the one place a file is read from disk or written to it.

    build/narrate/<digest>.words.json  words.py       the time base everything shares
    build/narrate/takes.json           takes.py       the take index and where each take sits in the narration
    build/cue-times.json               cue_times.py   every cue resolved against those words
    build/recordings/NN.json           recordings.py  what `record` did, judged and measured
    build/final/placements.json        placements.py  where every section sits in the finished film
    build/sections/NN.json             placements.py  what each section video was encoded from

What a run is doing while it does it is not an artifact. That is the event stream, and a run's
lines are appended to `build/events/<run>.jsonl` by a subscriber rather than written here.
"""

from __future__ import annotations

from decktalk.artifacts.cue_times import CueTimes
from decktalk.artifacts.placements import Placement, Placements
from decktalk.artifacts.recordings import (
    Luma,
    RecordingChecks,
    RecordingLog,
    Start,
    input_digest,
)
from decktalk.artifacts.stored import Stored, Unreadable, content_digest, file_digest
from decktalk.artifacts.takes import (
    PLACEHOLDER_PREFIX,
    PLACEHOLDER_SUFFIX,
    TAKE_DIGITS,
    PlaceholderInputs,
    Take,
    TakeInputs,
    Takes,
    is_placeholder,
    take_file,
)
from decktalk.artifacts.words import (
    WORDS_SUFFIX,
    AudioPrint,
    ClipWords,
    EstimatedWords,
    ProviderWords,
    Words,
    on_section_clock,
    words_file,
)

__all__ = [
    "PLACEHOLDER_PREFIX",
    "PLACEHOLDER_SUFFIX",
    "TAKE_DIGITS",
    "WORDS_SUFFIX",
    "AudioPrint",
    "ClipWords",
    "CueTimes",
    "EstimatedWords",
    "Luma",
    "PlaceholderInputs",
    "Placement",
    "Placements",
    "ProviderWords",
    "RecordingChecks",
    "RecordingLog",
    "Start",
    "Stored",
    "Unreadable",
    "Take",
    "TakeInputs",
    "Takes",
    "Words",
    "content_digest",
    "file_digest",
    "input_digest",
    "is_placeholder",
    "on_section_clock",
    "take_file",
    "words_file",
]
