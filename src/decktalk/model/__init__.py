"""The model of one project: everything DeckTalk knows before a stage runs.

    document.py    the frozen decktalk.toml tables and the section types
    workspace.py   every path under build/, named once
    env.py         .env read once, with every value handed back as a Secret
    script.py      script.md parsed into the sections the voice reads
    cues.py        cues.json parsed, and phrase matching over a take's words
    markers.py     media/markers.json parsed into typed Marker rows
    timeline.py    where the narration plays in the final film, run by run
    project.py     the thin composer of document, workspace, env and settings

Each loader takes a path, not a project, so a stage can parse one file without loading a whole
project, and `Project` composes them.
"""

from __future__ import annotations

from .document import (
    ClipSection,
    Document,
    Loudness,
    Mix,
    MusicSpec,
    PageSection,
    Section,
    Sfx,
    Soundscape,
    SoundSpec,
    Transition,
    Voice,
)
from .env import Env
from .project import Project
from .workspace import Workspace

__all__ = [
    "ClipSection",
    "Document",
    "Env",
    "Loudness",
    "Mix",
    "MusicSpec",
    "PageSection",
    "Project",
    "Section",
    "Sfx",
    "SoundSpec",
    "Soundscape",
    "Transition",
    "Voice",
    "Workspace",
]
