"""The pipeline, one package per stage and one module per call that reports or cuts.

    narrate/     the script becomes one take per section, with a time for every word
    cue/         every cue phrase becomes a second on its own section's clock
    record/      each page section is recorded against those seconds
    soundscape/  the music, the ambience bed and the effects are generated
    assemble/    the recordings, the narration and the soundscape become one film
    verify/      the finished film is measured against the clock it was promised
    build.py     the six stages in order, or the span of them a caller named
    check.py     what a build would spend and show, judged before anything is spent
    status.py    what is written, what is built, what is stale and what to do next
    words.py     every spoken word with its span, which is how a cue phrase is written
    storyboard.py  every slide at every cue, frozen onto one page
    clip.py      a span of one built section, cut into its own file

Every one of them satisfies the same convention: the module named after the call holds a function
of that name, taking the project's `Inputs` and the `Run` the facade opened, and returning the
result model named after it. That is the whole seam between `project.py` and the stages, so a test
fakes a stage by replacing one attribute and no stage ever sees a project, a machine or a run
opener.

A stage therefore cannot read the environment and cannot print. It reports through the run: one
sentence with `run.note`, one judgement with `run.found`, one count with `run.progress`, one file
with `run.wrote`, and one price with `run.approve` before anything is bought. It asks `run.check`
between sections, so a caller that cancelled a run stops it inside the section it was in rather
than at the end.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from decktalk.findings import Code, Finding, Fix, Location
from decktalk.pipeline import Stage

SECOND_DIGITS = 3
"""Truth: three decimal places of a second is one millisecond, which is finer than any frame."""


def judge(
    code: Code, message: str, location: Location, *, stage: Stage | None = None, fix: Fix | None = None
) -> Finding:
    """One judgement, built through validation so the code fills its own certainty and its own page.

    A raiser names the code, the sentence, the place and sometimes the fix. Writing the certainty
    out beside the code would be the second spelling of one fact, which is what the code owning it
    exists to prevent.
    """
    return Finding.model_validate({"code": code, "message": message, "location": location, "stage": stage, "fix": fix})


def selects(only: Sequence[int] | None) -> Callable[[int], bool]:
    """Whether one section number is in this run's selection, which is every section when it names none."""
    numbers = set(only or ())
    return lambda number: not numbers or number in numbers


def since(started: float) -> float:
    """How long a stage has been running, in seconds, which is what every result reports."""
    return round(time.monotonic() - started, SECOND_DIGITS)


def clock() -> float:
    """The moment a stage started, read from a clock that cannot go backwards."""
    return time.monotonic()


__all__ = ["SECOND_DIGITS", "clock", "judge", "selects", "since"]
