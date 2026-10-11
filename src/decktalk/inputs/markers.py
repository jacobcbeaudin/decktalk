"""`media/markers.json` parsed into typed `Marker` rows, which shape the music under the video.

    {"boost_db": 3, "boost_seconds": 2,
     "markers": [{"name": "turn", "section": 3, "phrase": "$start", "mute_seconds": 0.4}]}

A marker names a moment in the narration: `section` with `phrase`, `offset_seconds`, `occurrence` and
`case_sensitive` resolve exactly as a cue does. `mute_seconds` drops the music there, and the
swell that follows lasts `boost_seconds` at `boost_db`. A marker whose phrase is not found is
reported by `assemble` and skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from decktalk.errors import InputError
from decktalk.inputs.cues import SECTION_START, json_of
from decktalk.inputs.document import fill
from decktalk.inputs.paths import at, relative
from decktalk.tomlmap import Bounds, tune
from decktalk.tomlmap.read import Table


@dataclass(frozen=True)
class Marker:
    """One moment in the narration that the music answers to."""

    name: str
    section: int
    phrase: str = SECTION_START
    offset_seconds: float = tune(
        0.0,
        "Seconds added to the matched second.",
        unit="seconds",
        bounds=Bounds(ge=-10, le=10),
        hazard="A marker answers its phrase, so a nudge past ten seconds lands it where the phrase is not.",
    )
    occurrence: int = tune(1, "Which match of the phrase to take, counting from one.", bounds=Bounds(ge=1))
    case_sensitive: bool = False
    mute_seconds: float = tune(
        0.0,
        "Seconds of silence before the swell starts.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
        hazard="A mute ends where the swell starts, so a negative mute would end before it began.",
    )


@dataclass(frozen=True)
class Markers:
    """The whole markers file: how loud and how long a swell is, and where the swells fall."""

    boost_db: float = tune(
        3.0,
        "How far each marker raises the music.",
        unit="dB",
        bounds=Bounds(ge=0, le=12),
        hazard="A swell is a rise, so a value below zero is a dip, which `mute_seconds` already spells, and a rise "
        "past 12 dB lifts a bed at its default level halfway to the voice it sits under.",
    )
    boost_seconds: float = tune(
        2.0,
        "How long each swell lasts.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
        hazard="A swell is placed after its marker, so a negative length would end before it began.",
    )
    markers: tuple[Marker, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = ()
    """One sentence per key the file holds and nothing reads, which the run that loads it reports."""


def load_markers(path: Path, root: Path) -> Markers:
    """The parsed markers file. A malformed file fails here, with the file and the row named."""
    data = json_of(path.read_text(encoding="utf-8"), path, root)
    if not isinstance(data, dict):
        raise InputError(
            f"{path.name} has no top-level 'markers' array.",
            hint='Wrap the markers in {"markers": [...]}.',
            location=at(path, root),
        )
    shown = relative(path, root)
    top = Table(data, path.name, shown)
    rows: list[Marker] = []
    notes: list[str] = []
    for i, raw in enumerate(top.get_tables("markers")):
        t = Table(raw, f"{path.name}: markers #{i + 1}", shown)
        notes += t.note_unknown(Marker.__dataclass_fields__)
        rows.append(fill(t, Marker, name=t.get_str("name", "")))
    return fill(top, Markers, markers=tuple(rows), notes=tuple(notes))
