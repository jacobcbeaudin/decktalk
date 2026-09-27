"""Stage six: the one read-only stage, over the finished film and the logs that made it.

    plan.py      the probe arithmetic, with no ffmpeg, no file and no project
    measure.py   the measurements behind the plan, the onset scan and the cue loop
    seams.py     the start, cut and seam checks

The film is decoded once. The cues and the seams first say which frames they will read, the film is
streamed through that one plan at each size a comparison needs, and every comparison is then made in
this process on the frames that were kept.

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
from decktalk.findings import Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.media import ffmpeg, frames
from decktalk.pipeline import Artifact, Stage
from decktalk.results import VerifyResult
from decktalk.stages import clock, judge, selects, since
from decktalk.stages.verify.measure import cue_checks, film_starts, planned_cues, want_cues
from decktalk.stages.verify.plan import default_checks, opted_out, thin_change
from decktalk.stages.verify.seams import cut_checks, planned_seams, seam_checks, start_checks, want_seams

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
        run.note("The film has no cut list, so verify read where each section starts from the section files.")
    wanted = selects(only)
    kept = {number: at for number, at in starts.items() if wanted(number)}
    _repeat_recorded(inputs, run, wanted)
    _placed(inputs, run, wanted)
    cues = planned_cues(inputs, run, starts, total, default_checks(inputs.cue_times(), list(kept)), opted_out(inputs))
    seams = planned_seams(inputs, kept)
    wanted = frames.Wanted()
    want_cues(inputs, cues, wanted)
    want_seams(inputs, seams, wanted)
    decoded = frames.decode(film, wanted)
    return run.result(
        VerifyResult,
        film=inputs.relative(film),
        film_seconds=round(ffmpeg.probe_duration(film), 3),
        starts=start_checks(inputs, run, film, kept),
        cuts=cut_checks(inputs, run, film, inputs.takes(), kept),
        seams=seam_checks(inputs, run, film, seams, decoded),
        cues=cue_checks(inputs, run, film, cues, decoded),
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


def _placed(inputs: Inputs, run: Run, wanted: Callable[[int], bool]) -> None:
    """One judgement per cue whose second on disk is missing, or was placed from another `cues.json`.

    The cue times are compared with the cue file row by row. A cue whose phrase or nudge differs from
    the one its second was placed from, a cue the times do not list, and a second placed for a cue the
    file no longer declares all mean the times are older than the file, which a build puts right. Only
    a cue whose own phrase was matched and found nowhere is one the script does not speak. Telling the
    two apart matters, because blaming the script for a phrase it speaks sends an author to edit a
    sentence that was never wrong.
    """
    times = inputs.cue_times()
    where = inputs.relative(inputs.cues_path)
    for block in inputs.cues():
        if not wanted(block.number):
            continue
        rows = {row.cue: row for row in times.rows(block.number)} if times is not None else {}
        for cue in block.cues:
            row = rows.get(cue.cue)
            here = Location(where=cue.cue, file=where, section=block.number, cue=cue.cue)
            if times is not None and (row is None or row.phrase != cue.on or row.offset != cue.offset):
                placed = "no second at all" if row is None else f"a second placed for {row.phrase!r}"
                run.found(_stale(f"cues.json asks for {cue.cue} on {cue.on!r} and the cue times hold {placed}", here))
            elif row is None or row.seconds is None:
                run.found(
                    judge(
                        Code.CUE_UNRESOLVED,
                        f"the cue {cue.cue} lands on the phrase {cue.on!r}, which section {block.number} does "
                        "not speak, so there is no second to place it at and the film never plays it.",
                        here,
                        stage=Stage.VERIFY,
                    )
                )
        declared = {cue.cue for cue in block.cues}
        for gone in sorted(set(rows) - declared):
            here = Location(where=gone, file=where, section=block.number, cue=gone)
            run.found(_stale(f"the cue times hold a second for {gone}, which cues.json no longer declares", here))


def _stale(what: str, where: Location) -> Finding:
    """The judgement that the cue times on disk are older than `cues.json`, which names the way out."""
    return judge(
        Code.CUE_STALE,
        f"{what}, so the cue times are older than cues.json and the film was cut to moments nobody asks for "
        "now. Run `decktalk build` to place them again.",
        where,
        stage=Stage.VERIFY,
    )
