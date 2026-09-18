"""The pipeline, one module per stage. Each stage takes a Project, logs progress, and returns a result."""

from .align import AlignResult, align
from .assemble import AssembleResult, assemble
from .build import BuildResult, build
from .clip import ClipResult, SectionWords, clip, words
from .measure import LeadMeasurement, RecordingCheck, check, measure
from .narrate import NarrateResult, narrate
from .preflight import PreflightResult, preflight
from .record import RecordResult, record
from .screenshots import screenshots
from .soundscape import SoundscapeItem, soundscape
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
    "SectionWords",
    "SoundscapeItem",
    "VerifyResult",
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
