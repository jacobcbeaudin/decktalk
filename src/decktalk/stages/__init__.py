"""The pipeline, one module per command.

Each one takes a `Project` and logs its progress to the `decktalk` logger, and each one returns a
result that satisfies `verdicts.StageResult`, which is what the run judged and the same thing as
JSON-ready data, so the CLI counts the findings and prints the envelope without knowing any
result's shape. `tests/test_imports.py` holds the table of every command and the result it returns.
"""

from .align import AlignResult, align
from .assemble import AssembleResult, assemble
from .build import BuildResult, build
from .clip import ClipResult, SectionWords, WordsResult, clip, words
from .narrate import NarrateResult, narrate
from .preflight import PreflightResult, preflight
from .record import RecordResult, SectionRecording, record
from .screenshots import ScreenshotsResult, screenshots
from .soundscape import SoundscapeItem, SoundscapeResult, soundscape
from .status import StatusResult, status
from .verify import VerifyResult, verify

__all__ = [
    "AlignResult",
    "AssembleResult",
    "BuildResult",
    "ClipResult",
    "NarrateResult",
    "PreflightResult",
    "RecordResult",
    "ScreenshotsResult",
    "SectionRecording",
    "SectionWords",
    "SoundscapeItem",
    "SoundscapeResult",
    "StatusResult",
    "VerifyResult",
    "WordsResult",
    "align",
    "assemble",
    "build",
    "clip",
    "narrate",
    "preflight",
    "record",
    "screenshots",
    "soundscape",
    "status",
    "verify",
    "words",
]
