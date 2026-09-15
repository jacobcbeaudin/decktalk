"""DeckTalk: narrated presentation videos, cut to the word.

Public API. The file formats and the page contract are covered by the changelog: a change
to them bumps the minor version and ships with a migration note. The Python names below may
still move, and a rename is a breaking change once the project leaves the 0.x series.

    Project, Section types, Settings, load_settings
    project tables: Voice, Mix, Soundscape, Transition
    errors: DeckTalkError, ConfigError, MissingInputError, ProviderError, ToolError
    artifacts: Manifest, Timeline, Beats, Word, Sidecar
    stages: narrate, resolve_beats, preflight, record, measure, check, assemble, verify, shoot,
            soundscape, build, status, cut_clip, spoken_words
    results: NarrateResult, BeatsResult, PreflightResult, Recording, LeadMeasurement, RecordingCheck,
             AssembleResult, VerifyResult, SoundscapeItem, BuildResult, StatusReport, ClipResult, SectionWords
    speech: SpeechProvider, SpeechRequest, register

Everything under decktalk.media, everything under decktalk.providers other than the
three speech names above, and every name starting with an underscore is internal.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .artifacts import Beats, Manifest, Sidecar, Timeline, Word
from .config import Settings, load_settings
from .errors import ConfigError, DeckTalkError, MissingInputError, ProviderError, ToolError
from .project import ClipSection, Mix, PageSection, Project, Section, Soundscape, Transition, Voice
from .providers.speech import SpeechProvider, SpeechRequest, register
from .stages import (
    AssembleResult,
    BeatsResult,
    BuildResult,
    ClipResult,
    LeadMeasurement,
    NarrateResult,
    PreflightResult,
    Recording,
    RecordingCheck,
    SectionWords,
    SoundscapeItem,
    VerifyResult,
    assemble,
    build,
    check,
    cut_clip,
    measure,
    narrate,
    preflight,
    record,
    resolve_beats,
    shoot,
    soundscape,
    spoken_words,
    verify,
)
from .status import StatusReport, status

try:
    __version__ = version("decktalk")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0+unknown"

__all__ = [
    "AssembleResult",
    "Beats",
    "BeatsResult",
    "BuildResult",
    "ClipResult",
    "ClipSection",
    "ConfigError",
    "DeckTalkError",
    "LeadMeasurement",
    "Manifest",
    "MissingInputError",
    "Mix",
    "NarrateResult",
    "PageSection",
    "PreflightResult",
    "Project",
    "ProviderError",
    "Recording",
    "RecordingCheck",
    "Section",
    "SectionWords",
    "Settings",
    "Sidecar",
    "Soundscape",
    "SoundscapeItem",
    "SpeechProvider",
    "SpeechRequest",
    "StatusReport",
    "Timeline",
    "ToolError",
    "Transition",
    "VerifyResult",
    "Voice",
    "Word",
    "__version__",
    "assemble",
    "build",
    "check",
    "cut_clip",
    "load_settings",
    "measure",
    "narrate",
    "preflight",
    "record",
    "register",
    "resolve_beats",
    "shoot",
    "soundscape",
    "spoken_words",
    "status",
    "verify",
]
