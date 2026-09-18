"""DeckTalk: narrated presentation videos, cut to the word.

Public API. The file formats and the page contract are covered by the changelog: a change
to them bumps the minor version. The Python names below may still move, and a rename is a
breaking change once the project leaves the 0.x series.

    Project, Section types, Settings, load_settings
    project tables: Voice, Mix, Soundscape, Transition
    errors: DeckTalkError, ConfigError, MissingInputError, ProviderError, ToolError
    artifacts: Takes, Timeline, CueTimes, Word, RecordingLog
    stages: narrate, align, preflight, record, measure, check, assemble, verify, screenshots,
            soundscape, build, status, clip, words
    results: NarrateResult, AlignResult, PreflightResult, RecordResult, LeadMeasurement, RecordingCheck,
             AssembleResult, VerifyResult, SoundscapeItem, BuildResult, StatusResult, ClipResult, SectionWords
    speech: SpeechProvider, SpeechRequest, register_speech_provider

Everything under decktalk.media, everything under decktalk.providers other than the
three speech names above, and every name starting with an underscore is internal.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .artifacts import CueTimes, RecordingLog, Takes, Timeline, Word
from .config import Settings, load_settings
from .errors import ConfigError, DeckTalkError, MissingInputError, ProviderError, ToolError
from .project import ClipSection, Mix, PageSection, Project, Section, Soundscape, Transition, Voice
from .providers.speech import SpeechProvider, SpeechRequest, register_speech_provider
from .stages import (
    AlignResult,
    AssembleResult,
    BuildResult,
    ClipResult,
    LeadMeasurement,
    NarrateResult,
    PreflightResult,
    RecordingCheck,
    RecordResult,
    SectionWords,
    SoundscapeItem,
    VerifyResult,
    align,
    assemble,
    build,
    check,
    clip,
    measure,
    narrate,
    preflight,
    record,
    screenshots,
    soundscape,
    verify,
    words,
)
from .status import StatusResult, status

try:
    __version__ = version("decktalk")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0+unknown"

__all__ = [
    "AlignResult",
    "AssembleResult",
    "BuildResult",
    "ClipResult",
    "ClipSection",
    "ConfigError",
    "CueTimes",
    "DeckTalkError",
    "LeadMeasurement",
    "MissingInputError",
    "Mix",
    "NarrateResult",
    "PageSection",
    "PreflightResult",
    "Project",
    "ProviderError",
    "RecordResult",
    "RecordingCheck",
    "RecordingLog",
    "Section",
    "SectionWords",
    "Settings",
    "Soundscape",
    "SoundscapeItem",
    "SpeechProvider",
    "SpeechRequest",
    "StatusResult",
    "Takes",
    "Timeline",
    "ToolError",
    "Transition",
    "VerifyResult",
    "Voice",
    "Word",
    "__version__",
    "align",
    "assemble",
    "build",
    "check",
    "clip",
    "load_settings",
    "measure",
    "narrate",
    "preflight",
    "record",
    "register_speech_provider",
    "screenshots",
    "soundscape",
    "status",
    "verify",
    "words",
]
