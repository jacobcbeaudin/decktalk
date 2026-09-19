"""The pipeline, one module per command.

Each one takes a `Project` and logs its progress to the `decktalk` logger. Eleven of them return a
result that satisfies `verdicts.StageResult`, which is what the run judged and the same thing as
JSON-ready data, so the CLI counts the findings and prints the envelope without knowing any
result's shape. `record`, `measure` and `check` return one row per section instead, and
`tests/test_imports.py` holds the table of which does which.
"""

from .align import AlignResult, align
from .assemble import AssembleResult, assemble
from .build import BuildResult, build
from .clip import ClipResult, SectionWords, WordsResult, clip, words
from .measure import LeadMeasurement, RecordingCheck, check, measure
from .narrate import NarrateResult, narrate
from .preflight import PreflightResult, preflight
from .record import RecordResult, record
from .screenshots import ScreenshotsResult, screenshots
from .soundscape import SoundscapeItem, SoundscapeResult, soundscape
from .verify import VerifyResult, verify

__all__ = [
    "AlignResult",
    "AssembleResult",
    "BuildResult",
    "ClipResult",
    "LeadMeasurement",
    "NarrateResult",
    "PreflightResult",
    "RecordResult",
    "RecordingCheck",
    "ScreenshotsResult",
    "SectionWords",
    "SoundscapeItem",
    "SoundscapeResult",
    "VerifyResult",
    "WordsResult",
    "align",
    "assemble",
    "build",
    "check",
    "clip",
    "measure",
    "narrate",
    "preflight",
    "record",
    "screenshots",
    "soundscape",
    "verify",
    "words",
]
