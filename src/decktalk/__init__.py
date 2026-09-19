"""DeckTalk: narrated presentation videos, cut to the word.

`decktalk.__all__` is the whole supported Python API, and everything else may move without notice.
One word names the command, the Python call, the type it returns and its `--json` key, so each
command's result class is exported beside the function that returns it.

    project:   Project, Section, ClipSection, PageSection, Voice, Mix, Loudness, Sfx, Soundscape,
               SoundSpec, MusicSpec, Transition
    tuning:    Settings, load_settings
    errors:    DeckTalkError, ConfigError, MissingInputError, ProviderError, ToolError
    enums:     Stage, Verdict, Certainty, SkipReason, ErrorCode
    artifacts: Takes, CueTimes, RecordingLog, Cuts, Word
    stages:    narrate, align, record, assemble, verify
    commands:  preflight, screenshots, words, clip, status, soundscape, build
    results:   NarrateResult, AlignResult, RecordResult, AssembleResult, VerifyResult,
               PreflightResult, ScreenshotsResult, WordsResult, ClipResult, StatusResult,
               SoundscapeResult, BuildResult
    speech:    SpeechProvider, SpeechRequest, register_speech_provider

The file formats and the page contract are covered by the changelog: a change to them bumps the
minor version. The Python names may still move, and a rename is a breaking change once the project
leaves the 0.x series.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .artifacts import CueTimes, Cuts, RecordingLog, Takes, Word
from .errors import ConfigError, DeckTalkError, ErrorCode, MissingInputError, ProviderError, ToolError
from .model import (
    ClipSection,
    Loudness,
    Mix,
    MusicSpec,
    PageSection,
    Project,
    Section,
    Sfx,
    Soundscape,
    SoundSpec,
    Transition,
    Voice,
)
from .pipeline import Stage
from .settings import Settings, load_settings
from .speech import SpeechProvider, SpeechRequest, register_speech_provider
from .stages import (
    AlignResult,
    AssembleResult,
    BuildResult,
    ClipResult,
    NarrateResult,
    PreflightResult,
    RecordResult,
    ScreenshotsResult,
    SoundscapeResult,
    StatusResult,
    VerifyResult,
    WordsResult,
    align,
    assemble,
    build,
    clip,
    narrate,
    preflight,
    record,
    screenshots,
    soundscape,
    status,
    verify,
    words,
)
from .verdicts import Certainty, SkipReason, Verdict

try:
    __version__ = version("decktalk")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0+unknown"

__all__ = [
    "AlignResult",
    "AssembleResult",
    "BuildResult",
    "Certainty",
    "ClipResult",
    "ClipSection",
    "ConfigError",
    "CueTimes",
    "Cuts",
    "DeckTalkError",
    "ErrorCode",
    "Loudness",
    "MissingInputError",
    "Mix",
    "MusicSpec",
    "NarrateResult",
    "PageSection",
    "PreflightResult",
    "Project",
    "ProviderError",
    "RecordResult",
    "RecordingLog",
    "ScreenshotsResult",
    "Section",
    "Settings",
    "Sfx",
    "SkipReason",
    "SoundSpec",
    "Soundscape",
    "SoundscapeResult",
    "SpeechProvider",
    "SpeechRequest",
    "Stage",
    "StatusResult",
    "Takes",
    "ToolError",
    "Transition",
    "Verdict",
    "VerifyResult",
    "Voice",
    "Word",
    "WordsResult",
    "__version__",
    "align",
    "assemble",
    "build",
    "clip",
    "load_settings",
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
