"""The pipeline, one module per stage. Each stage takes a Project, logs progress, and returns a result."""

from .assemble import AssembleResult, assemble
from .beats import BeatsResult, resolve_beats
from .build import BuildResult, build
from .clip import ClipResult, SectionWords, cut_clip, spoken_words
from .measure import LeadMeasurement, RecordingCheck, check, measure
from .narrate import NarrateResult, narrate, script_segments
from .preflight import PreflightResult, preflight
from .record import Recording, record
from .shots import shoot
from .soundscape import SoundscapeItem, soundscape
from .verify import VerifyResult, verify

__all__ = [
    "AssembleResult",
    "BeatsResult",
    "BuildResult",
    "ClipResult",
    "LeadMeasurement",
    "NarrateResult",
    "PreflightResult",
    "Recording",
    "RecordingCheck",
    "SectionWords",
    "SoundscapeItem",
    "VerifyResult",
    "assemble",
    "build",
    "check",
    "cut_clip",
    "measure",
    "narrate",
    "preflight",
    "record",
    "resolve_beats",
    "script_segments",
    "shoot",
    "soundscape",
    "spoken_words",
    "verify",
]
