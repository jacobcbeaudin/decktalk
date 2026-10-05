"""Every cue resolved to a second on its section's own clock.

    build/cue-times.json   one block per section, each holding every cue that section declares

A row's `id` is the cue id the page understands, `phrase` is the script phrase it was matched
against, `seconds` is where it lands after its section starts, and `nudge_seconds` is the author's
own nudge, which is already inside `seconds`. The rows are the same `SectionCues` and `CueTime` a
`cue` result carries, so the file the stage writes and the JSON a caller reads are one shape.

The recorder passes this file's seconds to the page as the `cues` query, spelt with the marks the
page contract publishes, so a cue with no second behind it is left out of that value rather than
passed as a null the page would have to reason about. A previewed page has no recorder to write that
query, so it reads the same seconds from the alias the contract names as `PREVIEW_CUE_TIMES`, which
the origin answers from this file and which is never itself a project file.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import Field

from decktalk.artifacts.stored import Stored
from decktalk.page import LIST_SEPARATOR, TIME_MARK
from decktalk.results import CueTime, SectionCues


class CueTimes(Stored):
    """Every section's cues, each resolved against the words that section speaks."""

    label: ClassVar[str] = "the second each cue lands on"

    sections: tuple[SectionCues, ...] = Field((), description="Every section that declares a cue, in section order.")

    def rows(self, section: int) -> tuple[CueTime, ...]:
        """One section's cues, in the order they play."""
        return next((block.cues for block in self.sections if block.section == section), ())

    def at(self, section: int, cue: str) -> float | None:
        """Where one cue lands, or None when it was never resolved."""
        return self.times(section).get(cue)

    def times(self, section: int) -> dict[str, float]:
        """One section's resolved cues, keyed by cue id, with the unresolved ones left out."""
        return {row.id: row.seconds for row in self.rows(section) if row.seconds is not None}

    def query(self, section: int) -> str | None:
        """The `cues` query value for one section, or None when it has no resolved cue."""
        times = self.times(section)
        return LIST_SEPARATOR.join(f"{cue}{TIME_MARK}{at}" for cue, at in times.items()) or None

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
                    "cues": [{"cue": row.id, "at": row.seconds} for row in block.cues if row.seconds is not None],
                }
                for block in self.sections
                if block.section in scenes
            ]
        }


__all__ = ["CueTimes"]
