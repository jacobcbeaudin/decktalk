"""The pipeline, one package per stage and one module per call that reports or cuts.

    narrate/     the script becomes one take per section, with a time for every word
    cue/         every cue phrase becomes a second on its own section's clock
    record/      each page section is recorded against those seconds
    score/       the music, the ambience bed and the effects are generated
    assemble/    the recordings, the narration and the score become one film
    verify/      the finished film is measured against the clock it was promised
    build.py     the six stages in order, or the span of them a caller named
    cost.py      what every stage that buys costs, priced at the bill its provider declares
    table.py     each stage's function, its result and the options it takes
    check.py     what a build would spend and show, judged before anything is spent
    status.py    what is written, what is built, what is stale and what to do next
    words.py     every spoken word with its span, which is how a cue phrase is written
    storyboard.py  every slide at every cue, frozen onto one page
    clip.py      a span of one built section, cut into its own file

Every one of them satisfies the same convention: the module named after the call holds a function
of that name, taking the project's `Inputs` and the `Run` the facade opened, and returning the
result model named after it. The six stages are called through their rows in `table.py`, which
`build` and `project.py` share, so a test fakes a stage by replacing one row and no stage ever sees
a project, a machine or a run opener.

A stage therefore cannot read the environment and cannot print. It reports through the run: one
sentence with `run.note`, one judgement with `run.found`, one count with `run.progress`, one file
with `run.wrote`, and one price with `run.approve` before anything is bought. It asks `run.check`
between sections, so a caller that cancelled a run stops it inside the section it was in rather
than at the end.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from decktalk.inputs import Inputs
from decktalk.speech import SpeechContext

SECTION_START_SECONDS = 0.0
"""Where a section's own clock begins, which is when its first slide is already on screen."""


def speech_context(inputs: Inputs) -> SpeechContext:
    """What the voice in force is built from, taken from its own table, this project's tuning and its own `.env`.

    The base URL is its own table's `base_url`, which only the machine sets, so a provider with no
    table is handed none.
    """
    settings = inputs.settings
    return SpeechContext(
        secrets=inputs.env,
        base_url=inputs.voice.base_url,
        context_characters=settings.narration.context_characters,
        speech_timeout_seconds=settings.narration.timeout_seconds,
    )


def selects(only: Sequence[int] | None) -> Callable[[int], bool]:
    """Whether one section number is in this run's selection, which is every section when it names none."""
    numbers = set(only or ())
    return lambda number: not numbers or number in numbers


__all__ = [
    "SECTION_START_SECONDS",
    "selects",
    "speech_context",
]
