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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from decktalk.artifacts import Takes
from decktalk.inputs.document import PageSection, Section


@dataclass(frozen=True)
class NarrationRun:
    """Consecutive page sections with nothing between them, which play one unbroken stretch of the track.

    `at` is where the run begins in the finished film. `start` and `end` bound its stretch of the
    joined narration, and the last run has no end, so it plays to the end of the track.
    """

    sections: tuple[int, ...]
    at: float
    start: float
    end: float | None

    @property
    def offset(self) -> float:
        """What to add to a time in the joined narration to place it in the finished film."""
        return round(self.at - self.start, 3)


def narration_runs(sections: Sequence[Section], takes: Takes, starts: Mapping[int, float]) -> tuple[NarrationRun, ...]:
    """The narration split at every clip between two page sections, and after every held page section.

    `sections` are the sections the film plays, in the order it plays them, and `starts` says where
    each begins in the finished film. A project with no clip and no hold between page sections has
    one run, and a lone run plays the whole track from its first section's start.
    """
    groups: list[list[int]] = []
    open_run = False
    for section in sections:
        if section.is_clip or takes.of(section.number) is None:
            open_run = False
            continue
        if not open_run:
            groups.append([])
            open_run = True
        groups[-1].append(section.number)
        if isinstance(section, PageSection) and section.hold_seconds > 0:
            open_run = False
    last = len(groups) - 1
    return tuple(
        NarrationRun(
            sections=tuple(group),
            at=starts[group[0]],
            start=0.0 if len(groups) == 1 else takes.start(group[0]) or 0.0,
            end=None if index == last else takes.end(group[-1]),
        )
        for index, group in enumerate(groups)
    )


def narration_offsets(sections: Sequence[Section], takes: Takes, starts: Mapping[int, float]) -> dict[int, float]:
    """What to add to a time in the joined narration to place it in the film, per spoken section.

    A section in the take index that the film does not play takes the offset of the first run, and
    with no run at all every offset is zero.
    """
    runs = narration_runs(sections, takes, starts)
    offsets = {number: run.offset for run in runs for number in run.sections}
    first = runs[0].offset if runs else 0.0
    return {take.section: offsets.get(take.section, first) for take in takes.sections}


__all__ = ["NarrationRun", "narration_offsets", "narration_runs"]
