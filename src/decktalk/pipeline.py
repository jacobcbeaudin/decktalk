"""The vocabulary of a run: the five stages in the order they run, the events a run records, and the
closed values its files carry.

A stage is a member of `Stage` and never its name as a string, so `build`, its progress log,
`status` and the CLI's `--from` and `--to` share one closed list, and a misspelt stage fails where it
is written rather than making a comparison quietly false. The members are declared in the order a
build runs them, so the enum is the pipeline: `list(Stage)` is a whole run, and a run from one stage
to another is a slice of it.

The value of a member is how a build names the stage: the word a person passes to `--from` and
`--to`, the command that runs that stage alone, the key its payload sits under in `build --json`,
and the `stage` of a progress row. Nothing compares against that word except the reader that parses
it into a member. `ProgressEvent` is the same kind of list for what a progress row says happened.

The other closed words a run writes are enums here too, each with its JSON word as its value: what a
run does with a take, what `soundscape` did with an item, and in `cuts.json` what kind of section
plays and what stands in for one that is missing. A misspelt status then fails where it is written,
as a misspelt stage does, rather than making a comparison quietly false.
"""

from __future__ import annotations

from enum import Enum


class Stage(Enum):
    """One stage of the pipeline, declared in run order. The value is the name a build gives it."""

    NARRATE = "narrate"
    ALIGN = "align"
    RECORD = "record"
    ASSEMBLE = "assemble"
    VERIFY = "verify"

    @staticmethod
    def span(first: Stage | None, last: Stage | None) -> tuple[Stage, ...]:
        """The stages from `first` to `last`, both inclusive, in run order. None is the pipeline's end."""
        stages = list(Stage)
        begin = stages.index(first) if first is not None else 0
        end = stages.index(last) if last is not None else len(stages) - 1
        return tuple(stages[begin : end + 1])


class ProgressEvent(Enum):
    """What one row of the progress log says happened to a stage or to one of its sections.

    Every stage and every section opens with `START` and closes with one of the other three, so a
    log whose last row is a `START` is a run that is still working or one whose process died.
    """

    START = "start"
    DONE = "done"
    SKIP = "skip"
    FAIL = "fail"

    @property
    def closes(self) -> bool:
        """True for the three events that end what a `START` opened."""
        return self is not ProgressEvent.START


class TakeStatus(Enum):
    """What a run does with one section's take, declared in the order the plan's totals print them.

    The value is the `status` of a take-plan row and the key its count sits under in the totals.
    """

    SYNTHESIZE = "synthesize"  # The section is sent to the voice, which spends credits.
    CACHED = "cached"  # The take of this exact text is on disk already.
    UNKNOWN = "unknown"  # The provider could not be set up, so the content hash cannot be computed.


class SoundscapeStatus(Enum):
    """What `soundscape` did with one item. The value is the item's `status`."""

    PLANNED = "planned"  # A dry run, which asked for nothing.
    UNCHANGED = "unchanged"  # The file on disk was made from this exact request.
    GENERATED = "generated"  # The provider made the file in this run.


class SectionKind(Enum):
    """What a section plays: a page recorded in the browser, or a video clip.

    The value is the `kind` of a `cuts.json` row and of a section `status` reports.
    """

    PAGE = "page"
    CLIP = "clip"


class Substitute(Enum):
    """What plays in place of a section whose file is missing. The value is a `cuts.json` row's `substitute`."""

    SLATE = "slate"  # A clip section whose file is missing plays its titled slate.
    BLACK = "black"  # A page section with no recording plays black for its span.
