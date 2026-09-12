"""DeckTalk: narrated presentation videos, cut to the word.

Public API (stable within a minor version once 1.0 is reached; before that, the file
formats and the page contract are stable and the Python names below may still move):

    Project, Section types, Settings, load_settings
    errors: DeckTalkError, ConfigError, MissingInputError, ProviderError, ToolError
    artifacts: Manifest, Timeline, Beats, Word, Sidecar
    stages: narrate, resolve_beats, record, measure, check, assemble, verify, shoot,
            soundscape, build

Everything under decktalk.media, decktalk.providers and names starting with an
underscore is internal.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .artifacts import Beats, Manifest, Sidecar, Timeline, Word
from .config import Settings, load_settings
from .errors import ConfigError, DeckTalkError, MissingInputError, ProviderError, ToolError
from .project import ClipSection, PageSection, Project, Section
from .stages import assemble, build, check, measure, narrate, record, resolve_beats, shoot, soundscape, verify

try:
    __version__ = version("decktalk")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0+unknown"

__all__ = [
    "Beats",
    "ClipSection",
    "ConfigError",
    "DeckTalkError",
    "Manifest",
    "MissingInputError",
    "PageSection",
    "Project",
    "ProviderError",
    "Section",
    "Settings",
    "Sidecar",
    "Timeline",
    "ToolError",
    "Word",
    "__version__",
    "assemble",
    "build",
    "check",
    "load_settings",
    "measure",
    "narrate",
    "record",
    "resolve_beats",
    "shoot",
    "soundscape",
    "verify",
]
