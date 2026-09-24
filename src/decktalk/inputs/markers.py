"""`media/markers.json` parsed into typed `Marker` rows, which shape the music under the video.

    {"boost_db": 3, "boost_seconds": 2,
     "markers": [{"name": "turn", "section": 3, "on": "$start", "mute_seconds": 0.4}]}

A marker names a moment in the narration: `section` with `on`, `offset`, `occurrence` and
`case_sensitive` resolve exactly as a cue does. `mute_seconds` drops the music there, and the
swell that follows lasts `boost_seconds` at `boost_db`. A marker whose phrase is not found is
reported by `assemble` and skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from decktalk.errors import InputError
from decktalk.inputs.paths import at, relative
from decktalk.tomlmap import Table


@dataclass(frozen=True)
class Marker:
    """One moment in the narration that the music answers to."""

    name: str
    section: int
    on: str = "$start"
    offset: float = 0.0
    occurrence: int = 1
    case_sensitive: bool = False
    mute_seconds: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.section:02d}"


@dataclass(frozen=True)
class Markers:
    """The whole markers file: how loud and how long a swell is, and where the swells fall."""

    boost_db: float = 3.0
    boost_seconds: float = 2.0
    markers: tuple[Marker, ...] = field(default_factory=tuple)


def load_markers(path: Path, root: Path) -> Markers:
    """The parsed markers file. A malformed file fails here, with the file and the row named."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{path.name} is not valid JSON: {exc.msg}.",
            hint="Check the brackets and the commas on the line named here.",
            location=at(path, root, line=exc.lineno),
        ) from exc
    if not isinstance(data, dict):
        raise InputError(
            f"{path.name} has no top-level 'markers' array.",
            hint='Wrap the markers in {"markers": [...]}.',
            location=at(path, root),
        )
    shown = relative(path, root)
    top = Table(data, path.name, shown)
    rows: list[Marker] = []
    for i, raw in enumerate(top.get_tables("markers")):
        t = Table(raw, f"{path.name}: markers #{i + 1}", shown)
        t.warn_unknown(Marker.__dataclass_fields__)
        rows.append(
            Marker(
                name=t.get_str("name", ""),
                section=t.get_int("section", required=True),
                on=t.get_str("on", "$start"),
                offset=t.get_num("offset", 0.0),
                occurrence=t.get_int("occurrence", 1),
                case_sensitive=t.get_bool("case_sensitive"),
                mute_seconds=t.get_num("mute_seconds", 0.0),
            )
        )
    return Markers(
        boost_db=top.get_num("boost_db", 3.0),
        boost_seconds=top.get_num("boost_seconds", 2.0),
        markers=tuple(rows),
    )
