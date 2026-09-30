"""The measurements behind the cue plan: the probes, the onset scan and the click search.

`plan.py` decides what to measure and this module measures it. The cue loop runs in two passes. The
first works out, for every cue, where its reference, its probes and its controls fall and which
frames its onset scan could read, and adds them to one `Wanted` plan. `verify` decodes the film once
through that plan, and the second pass judges every cue from the decoded frames. The onset scan is
planned over the span every probe could end, because which probe wins is known only once the
frames are read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from decktalk.artifacts import CueTimes
from decktalk.events import Level, Unit
from decktalk.findings import Code, Location
from decktalk.inputs import Inputs
from decktalk.inputs.document import frame_dip
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg
from decktalk.media.audio import FULL_SCALE, gain
from decktalk.media.frames import Decoded, Size, Wanted
from decktalk.page import MILLISECONDS
from decktalk.pagescan import measured_rows, scene_entry
from decktalk.pipeline import Stage
from decktalk.results import CueCheck, SkipReason
from decktalk.settings import CLICK_LEVEL_DBFS
from decktalk.stages import judge
from decktalk.stages.verify.plan import (
    HALF_FRAME,
    Neighbour,
    block_size,
    control_spans,
    frame_size,
    onset_offset_seconds,
    probe_plan,
    reference_lead,
    reference_time,
    thin_change,
)


def film_starts(inputs: Inputs, film: Path) -> tuple[dict[int, float], float]:
    """(where each section starts in the film, how long the film runs), read from the cut list.

    The cut list is the film's own record of its shape, so nothing here adds up section files a
    second time and reaches a total the film does not have.
    """
    cuts = inputs.cuts()
    if cuts is not None and cuts.sections:
        return {cut.section: cut.start for cut in cuts.sections}, cuts.total_seconds
    starts: dict[int, float] = {}
    at = 0.0
    for section in inputs.document.sections:
        cut = inputs.workspace.section_video(section.key)
        if not cut.exists():
            continue
        starts[section.number] = at
        at += ffmpeg.probe_duration(cut)
    return starts, at if starts else ffmpeg.probe_duration(film)


def declared_spans(inputs: Inputs, section: int) -> dict[str, float]:
    """How long each cue of one section keeps moving after it fires, from the catalog the page published.

    The page declares the span of every effect it draws, and the recording log keeps that catalog
    whole, so the forward half of the neighbour allowance is the neighbour's own arithmetic rather
    than one constant that was wrong for a draw and wrong again for a cut.
    """
    found = inputs.document.section(section)
    log = inputs.recording_log(f"{section:02d}")
    scene = getattr(found, "scene", None)
    if log is None or scene is None:
        return {}
    scale = inputs.settings.motion.scale
    spans: dict[str, float] = {}
    entry = scene_entry(log.recording.report.catalog, scene)
    for measured in measured_rows(entry) if entry is not None else ():
        if measured.cue is not None:
            spans[measured.cue] = max(spans.get(measured.cue, 0.0), measured.span(scale))
    return spans


def neighbours_of(times: dict[str, float], spans: dict[str, float], sec_start: float, cue: str) -> list[Neighbour]:
    """Every other cue of this section, in the film's own clock, with the span each one declares."""
    return [Neighbour(at=sec_start + at, span=spans.get(other, 0.0)) for other, at in times.items() if other != cue]


def best_probe(
    film: Decoded, before: float, floor: float, cue_at: float, delays: list[float], inputs: Inputs
) -> tuple[float, float, float, float] | None:
    """(margin, changed, control, probe time) of the probe with the largest margin, the earlier on a tie.

    Each probe after the cue is compared with the reference, and the control is the quieter of two
    spans of the same length that end at the reference. Motion that is always there shows in both,
    while an earlier reveal still settling shows in one.
    """
    level, size = inputs.settings.verify.probe_diff_luma, frame_size(inputs.settings)
    best: tuple[float, float, float, float] | None = None
    for delay in delays:
        after = cue_at + delay
        changed = film.changed(before, after, level=level, size=size)
        controls = [film.changed(a, b, level=level, size=size) for a, b in control_spans(before, after - before, floor)]
        control = min(controls) if controls else 0.0
        margin = changed - control
        if best is None or margin > best[0]:
            best = (margin, changed, control, after)
    return best


def first_change_seconds(film: Decoded, before: float, after: float, cue_at: float, inputs: Inputs) -> float | None:
    """Seconds from the cue to the first frame past the reference where the reveal begins.

    The frames up to the cue set a noise floor, so an earlier reveal still settling does not count as
    the onset. A second series at one pixel per transform block cancels the encoder's ringing, which
    otherwise reads as a reveal a frame or two early.
    """
    verify = inputs.settings.verify
    fps = inputs.settings.video.output_fps
    series, blocks = (
        film.series(before, before, after, level=verify.onset_diff_luma, size=size)
        for size in (frame_size(inputs.settings), block_size(inputs.settings))
    )
    return onset_offset_seconds(
        series,
        before,
        cue_at,
        verify.onset_rise_points,
        tolerance=(verify.cue_offset_max_ms / MILLISECONDS) + HALF_FRAME / fps,
        blocks=dict(blocks),
    )


def click_seconds(
    film: Path, expected: float, inputs: Inputs, *, floor: float = 0.0, ceiling: float | None = None
) -> float | None:
    """Where the click nearest `expected` sounds in the film, or None when nothing loud enough is there.

    A build with placeholder narration carries a click at every word start, so the film's own audio
    can be measured against its own picture. The window never reaches before `floor` or past
    `ceiling`, so the sound of a neighbouring section is never taken for the click.
    """
    verify = inputs.settings.verify
    rate = inputs.settings.video.sample_rate
    start = max(floor, expected - verify.click_search_seconds)
    stop = (
        expected + verify.click_search_seconds
        if ceiling is None
        else min(ceiling, expected + verify.click_search_seconds)
    )
    if stop <= start:
        return None
    samples = audio.pcm_span(film, start, stop - start, sample_rate=rate)
    if not samples:
        return None
    peak = max(range(len(samples)), key=lambda index: abs(samples[index]))
    if abs(samples[peak]) < _amplitude(verify.click_floor_dbfs):
        return None
    return round(start + peak / rate, 3)


def _amplitude(dbfs: float) -> float:
    """The sample magnitude one level in dBFS is, which is what a peak is compared against.

    DeckTalk generates its own click at `CLICK_LEVEL_DBFS`, so a floor at or above that level would
    find no click at all, which is what the key's own hazard sentence warns about.
    """
    return FULL_SCALE * gain(min(dbfs, CLICK_LEVEL_DBFS))


@dataclass(frozen=True)
class Probed:
    """One cue whose probes are planned: where it sits in the film, its reference, its floor and its delays."""

    section: int
    cue: str
    at: float  # the cue's second inside its section
    sec_start: float
    sec_end: float
    spoken: float  # the cue's second in the film
    before: float  # the reference frame's second in the film
    floor: float  # the earliest second a control span may reach back to
    delays: tuple[float, ...]

    def want(self, wanted: Wanted, probe: Size, blocks: Size) -> None:
        """Add every frame this cue's probes, controls and onset scan could read to the film's one plan."""
        if not self.delays:
            return
        for delay in self.delays:
            after = self.spoken + delay
            wanted.point(probe, self.before, after)
            for a, b in control_spans(self.before, after - self.before, self.floor):
                wanted.point(probe, a, b)
        latest = self.spoken + max(self.delays)
        for size in (probe, blocks):
            wanted.point(size, self.before)
            wanted.span(size, self.before, latest)


def planned_cues(
    inputs: Inputs,
    run: Run,
    starts: dict[int, float],
    total: float,
    checks: list[tuple[int, str]],
    opted: set[tuple[int, str]],
) -> list[CueCheck | Probed]:
    """For each named cue, the row it is skipped with, or the probes it will be measured by."""
    cue_times = inputs.cue_times()
    return [_planned(inputs, run, starts, total, cue_times, section, cue, opted) for section, cue in checks]


def fitted_note(section: int, cue: str, delays: list[float]) -> str:
    """The line that says a cue was measured at delays fitted between its neighbours.

    It is a detail of how verify measured, and it says nothing is wrong, so it is written for a
    reader who asked for detail and names what each number is.
    """
    seconds = " and ".join(f"{delay:g} s" for delay in delays)
    return (
        f"Cue {cue} in section {section} sits close to another cue, so verify compared the picture {seconds} "
        "after its word instead of at the usual delays. This is how a close pair is measured, not a problem."
    )


def want_cues(inputs: Inputs, planned: list[CueCheck | Probed], wanted: Wanted) -> None:
    """Add every frame the planned cues could read to the film's one plan."""
    probe, blocks = frame_size(inputs.settings), block_size(inputs.settings)
    for row in planned:
        if isinstance(row, Probed):
            row.want(wanted, probe, blocks)


def cue_checks(
    inputs: Inputs, run: Run, film: Path, planned: list[CueCheck | Probed], decoded: Decoded
) -> tuple[CueCheck, ...]:
    """One row per named cue: where the picture changed, how far from its word, and how much of it moved."""
    takes = inputs.takes()
    clicks = bool(takes and takes.estimated)
    rows: list[CueCheck] = []
    for done, row in enumerate(planned, start=1):
        run.check()
        run.progress(
            Stage.VERIFY, done=done, total=len(planned), unit=Unit.PROBE, label=f"{row.section}:{row.cue}",
            section=row.section,
        )  # fmt: skip
        rows.append(row if isinstance(row, CueCheck) else _measured(inputs, run, film, decoded, row, clicks=clicks))
    return tuple(rows)


def _planned(
    inputs: Inputs,
    run: Run,
    starts: dict[int, float],
    total: float,
    cue_times: CueTimes | None,
    section: int,
    cue: str,
    opted: set[tuple[int, str]],
) -> CueCheck | Probed:
    """One cue's probes, or the one reason it cannot be measured."""
    at = None if cue_times is None else cue_times.at(section, cue)
    sec_start = starts.get(section, 0.0)
    spoken = round(sec_start + (at or 0.0), 3)
    if (section, cue) in opted:
        return CueCheck(section=section, cue=cue, spoken=spoken, skipped=SkipReason.OPTED_OUT)
    if at is None:
        return CueCheck(section=section, cue=cue, spoken=spoken, skipped=SkipReason.NO_CUES)
    if section not in starts:
        return CueCheck(section=section, cue=cue, spoken=spoken, skipped=SkipReason.NOT_ASSEMBLED)
    verify = inputs.settings.verify
    fps = inputs.settings.video.output_fps
    flags = inputs.document.fade_flags
    found = inputs.document.section(section)
    key = found.key if found is not None else f"{section:02d}"
    dip = frame_dip(inputs.document.transition.dip_seconds, fps)
    fade_in = flags.get(key, (False, False))[0]
    before = reference_time(sec_start, at, fade_in, dip, inputs.settings, fps)
    if before is None:
        return CueCheck(section=section, cue=cue, spoken=spoken, skipped=SkipReason.AT_SECTION_START)
    floor = sec_start + (dip if fade_in else 0.0)
    sec_end = min((t for t in starts.values() if t > sec_start), default=total)
    times = cue_times.times(section) if cue_times is not None else {}
    lead = reference_lead(inputs.settings)
    delays, fitted = probe_plan(
        spoken, before, floor, sec_end, neighbours_of(times, declared_spans(inputs, section), sec_start, cue),
        verify, fps, lead=lead,
    )  # fmt: skip
    if fitted:
        run.note(fitted_note(section, cue, delays), level=Level.DEBUG)
    return Probed(
        section=section, cue=cue, at=at, sec_start=sec_start, sec_end=sec_end, spoken=spoken, before=before,
        floor=floor, delays=tuple(delays),
    )  # fmt: skip


def _measured(inputs: Inputs, run: Run, film: Path, decoded: Decoded, cue: Probed, *, clicks: bool) -> CueCheck:
    """One planned cue measured on the decoded film, or the row that says no probe fit."""
    best = best_probe(decoded, cue.before, cue.floor, cue.spoken, list(cue.delays), inputs)
    if best is None:
        return CueCheck(section=cue.section, cue=cue.cue, spoken=cue.spoken, skipped=SkipReason.TOO_CLOSE_TO_END)
    return _judge(inputs, run, film, decoded, cue, best, clicks=clicks)


def _judge(
    inputs: Inputs,
    run: Run,
    film: Path,
    decoded: Decoded,
    probed: Probed,
    best: tuple[float, float, float, float],
    *,
    clicks: bool,
) -> CueCheck:
    """The row for one cue whose best probe has been measured: the landing, the onset and the word."""
    verify = inputs.settings.verify
    margin, changed, _control, after = best
    section, cue, at = probed.section, probed.cue, probed.spoken
    where = Location(where=cue, file=inputs.relative(film), section=section, cue=cue)
    if changed < verify.changed_share_min_percent or margin < verify.margin_min_points:
        run.found(
            judge(
                Code.CUE_NO_CHANGE,
                f"{changed:.2f} percent of the picture changed at {cue}, against the "
                f"{verify.changed_share_min_percent:.2f} percent floor, with a margin of {margin:.2f} "
                f"against {verify.margin_min_points:.2f}, so nothing visibly happened at the cue.",
                where,
                stage=Stage.VERIFY,
            )
        )
        return CueCheck(section=section, cue=cue, spoken=at, change_percent=round(changed, 2))
    if thin_change(changed, margin, verify):
        run.found(
            judge(
                Code.CUE_THIN_CHANGE,
                f"the reveal at {cue} changed {changed:.2f} percent of the picture with a margin of "
                f"{margin:.2f}, which is under {verify.thin_change_factor:g} times the "
                f"{verify.changed_share_min_percent:.2f} percent floor, so a slightly smaller reveal "
                "would not have been measured at all.",
                where,
                stage=Stage.VERIFY,
            )
        )
    offset = first_change_seconds(decoded, probed.before, after, at, inputs)
    if offset is None:
        run.found(
            judge(
                Code.CUE_NO_ONSET,
                f"{changed:.2f} percent of the picture changed at {cue} and no frame rose by the "
                f"{verify.onset_rise_points:.3f} percentage points an onset must, so its second is the "
                "section's start rather than its word's.",
                where,
                stage=Stage.VERIFY,
            )
        )
        return CueCheck(
            section=section,
            cue=cue,
            spoken=at,
            change_percent=round(changed, 2),
            skipped=SkipReason.NO_ONSET,
        )
    shown = round(at + offset, 3)
    word = at
    if clicks:
        heard = click_seconds(film, at, inputs, floor=probed.sec_start, ceiling=probed.sec_end)
        if heard is not None:
            word = heard
            _judge_click(inputs, run, cue, where, promised=at, heard=heard)
    landed = round(shown - word, 3)
    limit = verify.cue_offset_max_ms / MILLISECONDS
    if abs(landed) > limit + HALF_FRAME / inputs.settings.video.output_fps:
        run.found(
            judge(
                Code.CUE_OFF,
                f"the reveal at {cue} first changed {landed * MILLISECONDS:+.0f} ms from the word it lands "
                f"on, which is outside the {verify.cue_offset_max_ms:.0f} ms the offset limit allows.",
                where,
                stage=Stage.VERIFY,
            )
        )
    return CueCheck(
        section=section,
        cue=cue,
        spoken=round(word, 3),
        shown=shown,
        offset=landed,
        change_percent=round(changed, 2),
    )


def _judge_click(inputs: Inputs, run: Run, cue: str, where: Location, *, promised: float, heard: float) -> None:
    """Report a click that sounds further from the second the cue was promised than the a/v limit allows.

    An mp3 frame smears a transient and the encode adds its own, so the sound has its own wider limit
    than the picture, which is what `av_offset_max_ms` is for.
    """
    verify = inputs.settings.verify
    apart = (heard - promised) * MILLISECONDS
    if abs(apart) <= verify.av_offset_max_ms:
        return
    run.found(
        judge(
            Code.CUE_OFF,
            f"the word behind {cue} sounds {apart:+.0f} ms from the second it was promised, which is "
            f"outside the {verify.av_offset_max_ms:.0f} ms the sound is allowed to drift, so the film's "
            "own audio and its cue times disagree.",
            where,
            stage=Stage.VERIFY,
        )
    )


__all__ = [
    "Probed",
    "best_probe",
    "click_seconds",
    "cue_checks",
    "declared_spans",
    "film_starts",
    "first_change_seconds",
    "fitted_note",
    "neighbours_of",
    "planned_cues",
    "want_cues",
]
