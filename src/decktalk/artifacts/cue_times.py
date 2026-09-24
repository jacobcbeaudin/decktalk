"""Every cue resolved to a second on its section's own clock.

    build/cue-times.json   one block per section, each holding every cue that section declares

A row's `cue` is the wire id the page understands, `phrase` is the script phrase it was matched
against, `seconds` is where it lands after its section starts, and `offset` is the author's own
nudge, which is already inside `seconds`. The rows are the same `SectionCues` and `CueTime` a
`cue` result carries, so the file the stage writes and the JSON a caller reads are one shape.

The recorder passes this file's seconds to the page as the `cues` query, so a cue with no second
behind it is left out of that value rather than passed as a null the page would have to reason
about.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from decktalk.artifacts.stored import Stored
from decktalk.results import CueTime, SectionCues

CUE_AT = "@"
"""What separates a cue's wire id from its second in the query the recorder passes the page."""

CUE_SEPARATOR = ","
"""What separates two cues in that query."""

PREVIEW_ALIAS = "/__decktalk/cue-times.json"
"""Where a previewed page reads the resolved cues from, which is an alias and never a project file.

A recorded page is handed its seconds in its own URL, because the recorder decides them. An author
previewing the same deck in a browser has no such URL, so the origin answers this one path from the
artifact instead. It is an alias rather than the file itself because the build directory is not
served, and a page reading it is previewing rather than being recorded, so it is never part of what
a recording is keyed on.
"""


class CueTimes(Stored):
    """Every section's cues, each resolved against the words that section speaks."""

    sections: tuple[SectionCues, ...] = Field((), description="Every section that declares a cue, in section order.")

    @property
    def estimated(self) -> bool:
        """True when any section's seconds come from estimated words rather than a voiced take."""
        return any(block.estimated for block in self.sections)

    def of(self, section: int) -> SectionCues | None:
        """One section's block, or None when that section declares no cue."""
        return next((block for block in self.sections if block.section == section), None)

    def rows(self, section: int) -> tuple[CueTime, ...]:
        """One section's cues, in the order they play."""
        block = self.of(section)
        return block.cues if block else ()

    def row(self, section: int, cue: str) -> CueTime | None:
        """One cue of one section, or None when that section does not declare it."""
        return next((row for row in self.rows(section) if row.cue == cue), None)

    def at(self, section: int, cue: str) -> float | None:
        """Where one cue lands, or None when it was never resolved."""
        row = self.row(section, cue)
        return None if row is None else row.seconds

    def word_at(self, section: int, cue: str) -> float | None:
        """Where the word behind one cue begins, before the author's nudge, or None when it has none."""
        row = self.row(section, cue)
        return None if row is None or row.seconds is None else round(row.seconds - row.offset, 3)

    def times(self, section: int) -> dict[str, float]:
        """One section's resolved cues, keyed by wire id, with the unresolved ones left out."""
        return {row.cue: row.seconds for row in self.rows(section) if row.seconds is not None}

    def query(self, section: int) -> str | None:
        """The `cues` query value for one section, or None when it has no resolved cue."""
        times = self.times(section)
        return CUE_SEPARATOR.join(f"{cue}{CUE_AT}{at}" for cue, at in times.items()) or None

    def preview(self, scenes: Mapping[int, str]) -> dict[str, Any]:
        """The document a previewed page reads from the alias, which is one block per resolved section.

        A section the project no longer plays is left out, because the page asks this for the scene
        it is about to draw and an answer about a section nobody plays would be noise.
        """
        return {
            "sections": [
                {
                    "key": block.key,
                    "scene": scenes[block.section],
                    "cues": [{"cue": row.cue, "at": row.seconds} for row in block.cues if row.seconds is not None],
                }
                for block in self.sections
                if block.section in scenes
            ]
        }


__all__ = ["CUE_AT", "CUE_SEPARATOR", "PREVIEW_ALIAS", "CueTimes"]
