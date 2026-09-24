"""The three checks that read the shape of the film rather than one cue: starts, cuts and seams.

A section start must show a real picture past the dip to black, the narration must be quiet in the
window before each section's narration ends, and a section that declares itself seamless must open
on the picture the section before it ended on.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import Takes
from decktalk.findings import Code, Location
from decktalk.inputs import Inputs
from decktalk.inputs.document import frame_dip
from decktalk.inputs.timeline import narration_offsets
from decktalk.machine import Run
from decktalk.media import audio, frames
from decktalk.pipeline import Stage
from decktalk.results import CutCheck, SeamCheck, StartCheck
from decktalk.stages import judge
from decktalk.stages.verify.plan import EPSILON, frame_size

STEP_WINDOW_SECONDS = 0.05
"""Calibration: how much of the waveform either side of a cut the step is measured over.

A twentieth of a second is longer than one frame at every rate DeckTalk encodes at and shorter than
a syllable, so it reads the level the cut lands on rather than the phrase around it.
"""

SEAM_SEARCH_FRAMES = 3
"""Calibration: how many frames past a seamless cut are searched for the picture the section before it ended on.

A picture that arrives later than this is not drifting, it is a different picture, and the share the
seam check measures says so on its own.
"""

HALF_FRAME = 0.5
"""Truth: a frame is the first one at or after its time, so a read aims half a frame inside it."""

TRAILING_FRAMES = 1.5
"""Truth: the last whole frame of a section sits one and a half frames before the cut that ends it."""


def start_checks(inputs: Inputs, run: Run, film: Path, starts: dict[int, float]) -> tuple[StartCheck, ...]:
    """One row per assembled section: the frame past the dip shows a real picture and not black."""
    verify = inputs.settings.verify
    rows: list[StartCheck] = []
    for number, at in starts.items():
        probe = at + verify.after_dip_seconds
        _mean, brightest = frames.luma_at(film, probe)
        rows.append(StartCheck(section=number, at=round(probe, 3), luma=round(brightest, 2)))
        if brightest <= verify.black_max_luma:
            run.found(
                judge(
                    Code.PAGE_BLACK,
                    f"section {number} opens at {at:.3f}s and the frame read at {probe:.3f}s is black, "
                    f"with a brightest luma of {brightest:.1f} against the {verify.black_max_luma:.0f} "
                    "a frame must pass to count as a picture.",
                    Location(where=f"section {number}", file=inputs.relative(film), section=number),
                    stage=Stage.VERIFY,
                )
            )
    return tuple(rows)


def cut_checks(
    inputs: Inputs, run: Run, film: Path, takes: Takes | None, starts: dict[int, float]
) -> tuple[CutCheck, ...]:
    """One row per spoken section: the narration is quiet before its cut, and the waveform does not step.

    The speech level is read from the narration track alone, so music or an effect at a boundary is
    never taken for a word, and a clip, which carries its own audio, is exempt. The step is read from
    the finished film either side of the cut, because a step is what a viewer hears.
    """
    verify = inputs.settings.verify
    narration = inputs.workspace.narration_path
    if takes is None or not narration.exists():
        return ()
    played = [section for section in inputs.document.sections if section.number in starts]
    offsets = narration_offsets(played, takes, starts)
    rows: list[CutCheck] = []
    for section in played:
        take = takes.of(section.number)
        end = takes.end(section.number)
        if take is None or end is None:
            continue
        window = min(verify.cut_window_seconds, take.span_seconds)
        speech = audio.rms_db(narration, max(0.0, end - window), window)
        at = round(offsets[section.number] + end, 3)
        step = _step_dbfs(film, at)
        rows.append(CutCheck(section=section.number, at=at, speech_dbfs=round(speech, 2), step_dbfs=round(step, 2)))
        if speech > verify.cut_max_dbfs:
            run.found(
                judge(
                    Code.CUT_SPEECH,
                    f"section {section.number}'s narration is still sounding at {speech:.1f} dBFS in the "
                    f"{window:.2f}s before its cut at {at:.3f}s, which is over the "
                    f"{verify.cut_max_dbfs:.1f} dBFS a silent cut must be under, so a word is sliced in two.",
                    Location(where=f"section {section.number}", file=inputs.relative(film), section=section.number),
                    stage=Stage.VERIFY,
                )
            )
    return tuple(rows)


def _step_dbfs(film: Path, at: float) -> float:
    """How far the waveform steps across one cut, as the level after it less the level before it."""
    before = audio.rms_db(film, max(0.0, at - STEP_WINDOW_SECONDS), STEP_WINDOW_SECONDS)
    after = audio.rms_db(film, at, STEP_WINDOW_SECONDS)
    return after - before


def seam_checks(inputs: Inputs, run: Run, film: Path, starts: dict[int, float]) -> tuple[SeamCheck, ...]:
    """One row per assembled section that declares itself seamless and follows an assembled section.

    The frames compared sit outside any dip, so a fade to black is never taken for a jump. The row
    reports how far the picture has drifted from its own clock, which is the distance from the cut to
    the first frame of the incoming section that still shows what the outgoing one ended on.
    """
    verify = inputs.settings.verify
    fps = inputs.settings.video.output_fps
    flags = inputs.document.fade_flags
    dip = frame_dip(inputs.document.transition.dip_seconds, fps)
    size = {"level": verify.probe_diff_luma, **frame_size(inputs.settings)}
    rows: list[SeamCheck] = []
    sections = inputs.document.sections
    for previous, section in zip(sections, sections[1:], strict=False):
        if not section.seamless or section.number not in starts or previous.number not in starts:
            continue
        cut = starts[section.number]
        last = cut - (dip if flags.get(previous.key, (False, False))[1] else 0.0) - TRAILING_FRAMES / fps
        opening = cut + (dip if flags.get(section.key, (False, False))[0] else 0.0) - HALF_FRAME / fps
        opening = max(opening, cut)
        drift, share = _drift(film, last, opening, fps, size, verify.cut_change_max_percent)
        rows.append(SeamCheck(section=section.number, at=round(cut, 3), drift=drift))
        if share > verify.cut_change_max_percent:
            run.found(
                judge(
                    Code.CUT_POP,
                    f"section {section.number} declares itself seamless and {share:.2f} percent of the picture "
                    f"changes across its cut at {cut:.3f}s, which is over the "
                    f"{verify.cut_change_max_percent:.2f} percent a join may show, so the seam is visible.",
                    Location(where=f"section {section.number}", file=inputs.relative(film), section=section.number),
                    stage=Stage.VERIFY,
                )
            )
    return tuple(rows)


def _drift(
    film: Path, last: float, opening: float, fps: int, size: dict[str, int], limit: float
) -> tuple[float, float]:
    """(how far past the cut the outgoing picture is found, the smallest share measured), in seconds.

    The first frame of the incoming section is compared with the outgoing section's last frame. A
    seam that matches straight away has drifted by nothing. When it does not match, the next few
    frames are read too, because a picture that arrives a frame or two late is a section whose clock
    has slipped rather than a section showing something else.
    """
    best = frames.changed_pixels_percent(film, last, opening, **size)
    if best <= limit:
        return 0.0, round(best, 2)
    for step in range(1, SEAM_SEARCH_FRAMES + 1):
        share = frames.changed_pixels_percent(film, last, opening + step / fps, **size)
        best = min(best, share)
        if share <= limit + EPSILON:
            return round(step / fps, 3), round(share, 2)
    return round(SEAM_SEARCH_FRAMES / fps, 3), round(best, 2)


__all__ = ["SEAM_SEARCH_FRAMES", "STEP_WINDOW_SECONDS", "cut_checks", "seam_checks", "start_checks"]
