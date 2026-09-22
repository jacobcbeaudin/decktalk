"""Where the narration plays in the final film: the narration clock placed on the film's clock.

`narration.mp3` holds the spoken sections back to back with no gaps, and the film does not. A clip
between two page sections, or a page section's `hold_seconds`, pauses the narration, and the next
page section resumes it on its own first frame. So the track plays in runs of consecutive page
sections, and each run starts where its first section starts in the film.

The mix places the track by these runs, the captions shift each section's words by them, and the
cut check reads the samples the viewer hears before each cut by them, so all three agree on where a
moment of the narration plays because there is one rule for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..artifacts import Takes
from .document import PageSection, Section


@dataclass(frozen=True)
class NarrationRun:
    """Consecutive page sections with no clip or hold between them, which play one unbroken stretch of the narration.

    `at` is where the run begins in the final file. `start` and `end` bound its stretch of
    narration.mp3, and the last run has no end, so it plays to the end of the track.
    """

    keys: tuple[str, ...]
    at: float
    start: float
    end: float | None

    @property
    def offset(self) -> float:
        """What to add to a time in narration.mp3 to place it in the final file."""
        return self.at - self.start


def narration_runs(sections: Sequence[Section], takes: Takes, starts: dict[str, float]) -> list[NarrationRun]:
    """The narration split at every clip that sits between page sections, and after every held page section.

    `sections` are the sections the film plays, in the order it plays them, and `starts` gives
    where each begins in the final file. A project with no clip or hold between page sections has
    one run, and a lone run plays the whole track from its first section's start.
    """
    groups: list[list[str]] = []
    open_run = False
    for sec in sections:
        if sec.is_clip:
            open_run = False
        elif sec.key in takes.sections:
            if not open_run:
                groups.append([])
                open_run = True
            groups[-1].append(sec.key)
            if isinstance(sec, PageSection) and sec.hold_seconds > 0:
                open_run = False
    return [
        NarrationRun(
            keys=tuple(keys),
            at=starts[keys[0]],
            start=0.0 if len(groups) == 1 else takes.start(keys[0]) or 0.0,
            end=None if i == len(groups) - 1 else takes.end(keys[-1]),
        )
        for i, keys in enumerate(groups)
    ]


def narration_offsets(sections: Sequence[Section], takes: Takes, starts: dict[str, float]) -> dict[str, float]:
    """What to add to a time in narration.mp3 to place it in the final file, per spoken section key.

    A section in the take index that the film does not play takes the offset of the first run, and
    with no run at all every offset is 0.
    """
    runs = narration_runs(sections, takes, starts)
    offsets = {key: run.offset for run in runs for key in run.keys}
    first = runs[0].offset if runs else 0.0
    return {key: offsets.get(key, first) for key in takes.sections}
