"""Stage six: the one read-only stage, over the finished film and the logs that made it.

    plan.py      the probe arithmetic, with no ffmpeg, no file and no project
    measure.py   the ffmpeg calls behind the plan, the onset scan and the cue loop
    seams.py     the start, cut and seam checks

`verify` measures four things. Every section must open on a real picture past its dip to black. The
narration must be quiet in the window before each cut, so no cut lands on a word. A section that
declares itself seamless must open on the picture the section before it ended on. And every cue must
change the picture, by enough to be seen, within the offset limit of the word it was promised to.

It repeats what each recording log already judged rather than measuring it again, so a page that
threw or an equation that was never typeset is still a finding after the build that recorded it, and
after a build somebody else recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from decktalk.errors import NotBuiltError
from decktalk.findings import Code, Location
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.media import ffmpeg
from decktalk.pipeline import Artifact, Stage
from decktalk.results import VerifyResult
from decktalk.stages import clock, judge, selects, since
from decktalk.stages.verify.measure import cue_checks, film_starts
from decktalk.stages.verify.plan import default_checks, opted_out, thin_change
from decktalk.stages.verify.seams import cut_checks, seam_checks, start_checks

__all__ = ["opted_out", "thin_change", "verify"]


def verify(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None) -> VerifyResult:
    """Measure the finished film against the clock the earlier stages promised it would keep."""
    started = clock()
    film = inputs.workspace.film
    if not film.exists():
        raise NotBuiltError(
            f"{inputs.relative(film)} is not there, so there is nothing to measure.",
            hint="Run `decktalk assemble` first.",
        )
    starts, total = film_starts(inputs, film)
    if not starts:
        raise NotBuiltError(
            f"{Artifact.FINAL.value} holds no cut list and no section was cut, so the film has no shape to read.",
            hint="Run `decktalk assemble` first.",
        )
    if inputs.cuts() is None:
        run.note("there is no cut list, so the section starts were probed from the section files instead.")
    wanted = selects(only)
    kept = {number: at for number, at in starts.items() if wanted(number)}
    _repeat_recorded(inputs, run, wanted)
    _unresolved(inputs, run, wanted)
    checks = default_checks(inputs.cue_times(), list(kept))
    return run.result(
        VerifyResult,
        film=inputs.relative(film),
        film_seconds=round(ffmpeg.probe_duration(film), 3),
        starts=start_checks(inputs, run, film, kept),
        cuts=cut_checks(inputs, run, film, inputs.takes(), kept),
        seams=seam_checks(inputs, run, film, kept),
        cues=cue_checks(inputs, run, film, starts, total, checks, opted_out(inputs)),
        seconds=since(started),
    )


def _repeat_recorded(inputs: Inputs, run: Run, wanted: Callable[[int], bool]) -> None:
    """Report again what every recording log judged, which this stage repeats and never re-measures.

    A judgement a recording made is still true of the film that was cut from it, and the log is the
    only record of it after the run that recorded it has ended.
    """
    for section in inputs.document.page_sections:
        if not wanted(section.number):
            continue
        log = inputs.recording_log(section.key)
        if log is None:
            continue
        for found in log.findings:
            run.found(found)


def _unresolved(inputs: Inputs, run: Run, wanted: Callable[[int], bool]) -> None:
    """One judgement per cue `cues.json` lists that no run could give a second to.

    A cue with no second was never played, so the film shows nothing where the author wrote a
    reveal, and that is worth saying against the finished film as well as against the cue file.
    """
    times = inputs.cue_times()
    for block in inputs.cues():
        if not wanted(block.number):
            continue
        resolved = times.times(block.number) if times is not None else {}
        for cue in block.cues:
            if cue.cue in resolved:
                continue
            run.found(
                judge(
                    Code.CUE_UNRESOLVED,
                    f"the cue {cue.cue} lands on the phrase {cue.on!r}, which section {block.number} does not "
                    "speak, so there is no second to place it at and the film never plays it.",
                    Location(
                        where=cue.cue,
                        file=inputs.relative(inputs.cues_path),
                        section=block.number,
                        cue=cue.cue,
                    ),
                    stage=Stage.VERIFY,
                )
            )
