"""Stage 5: the recordings, the narration, the clips and the soundscape become one film.

    cut.py        every section as one silent mp4, and the cut list
    mix.py        the whole soundtrack as one filter graph, one `MixInput` per layer
    loudness.py   the two-pass normalization and what it measured
    publish.py    captions, chapters, the transcript, the poster and the atomic final file

The order is fixed. Each section is cut to its span and the sections are joined with no gaps. The
soundtrack is mixed over a silent anchor of the picture's length. The mix is normalized to the EBU
R128 target unless the narration is a placeholder, whose clicks the a/v check listens for. Then
everything a viewer receives is written, and the finished film is renamed into place in one step.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from decktalk.artifacts import Cuts, Takes
from decktalk.errors import NotBuiltError, ToolError
from decktalk.events import Unit
from decktalk.inputs import Inputs
from decktalk.inputs.timeline import narration_offsets
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg
from decktalk.pipeline import Stage
from decktalk.results import AssembleResult, RenderedSection
from decktalk.stages import SECOND_DIGITS, clock, since
from decktalk.stages.assemble.cut import Rendered, cut_list, render_sections, rendered_starts, stray_cuts
from decktalk.stages.assemble.loudness import loudness_findings, measured, normalize_loudness
from decktalk.stages.assemble.mix import MixPlan, encode_soundtrack, mix_soundtrack
from decktalk.stages.assemble.publish import (
    build_captions,
    build_chapters,
    caption_texts,
    clip_captions,
    publish,
    render_poster,
    sound_captions,
    uncaptioned_sounds,
    with_sound_captions,
    write_caption_files,
    write_transcript_page,
)

WORK_MARK = "."
"""What the name of a file only this run may read opens with, so no viewer ever opens a half-made one."""

DELIVERY_PASSES: tuple[str, ...] = (
    "mix the soundtrack",
    "encode the soundtrack",
    "write the captions",
    "publish the film",
)
"""Truth: the passes that follow the section cuts, in the order the encoder and the writers run them.

They are named rather than counted, so the count a renderer reads and the label it prints beside it
come from one list and a pass added here reaches both.
"""


class Passes:
    """How far through its own passes one assemble is, counted in the passes themselves.

    A renderer never works out a fraction, so the stage counts what it has finished rather than
    naming each step's number where it happens, which is how the count and the plan stay equal.
    """

    def __init__(self, run: Run, cuts: int) -> None:
        self.run = run
        self.total = cuts + len(DELIVERY_PASSES)
        self.done = cuts

    def finished(self, label: str) -> None:
        """One more pass is behind this run, which is the line a renderer draws its bar from."""
        self.done += 1
        self.run.progress(Stage.ASSEMBLE, done=self.done, total=self.total, unit=Unit.PASS, label=label)


def assemble(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    soundscape: bool = True,
    loudness: bool = True,
    strict: bool = False,
) -> AssembleResult:
    """Cut, mix, normalize and publish the whole film, with everything a viewer receives beside it."""
    started = clock()
    takes = _takes(inputs)
    stray_cuts(inputs, run)
    passes = Passes(run, len(inputs.document.sections))
    rows = render_sections(
        inputs, run, takes, only=list(only) if only is not None else None, strict=strict, passes=passes.total
    )

    final_dir = inputs.workspace.final_dir
    work = final_dir / f"{WORK_MARK}{inputs.workspace.name}.tmp.mp4"
    mixed = final_dir / f"{WORK_MARK}{inputs.workspace.name}.mix.mov"
    for path in (work, mixed):
        path.unlink(missing_ok=True)

    plan = mix_soundtrack(inputs, run, rows, takes, mixed, soundscape=soundscape)
    passes.finished(f"mix {len(plan.inputs)} audio layers")
    try:
        after = _deliver(inputs, run, mixed, work, takes, loudness=loudness, strict=strict)
    finally:
        mixed.unlink(missing_ok=True)
    passes.finished(DELIVERY_PASSES[1])

    _write_deliverables(inputs, run, rows, takes)
    passes.finished(DELIVERY_PASSES[2])
    stamped = publish(inputs, work, inputs.workspace.deliverables())
    run.wrote(inputs.workspace.film)
    if stamped is not None:
        run.wrote(stamped)
    # The poster is drawn last, because it is one picture beside a film that is already finished.
    poster = render_poster(inputs, run, inputs.workspace.deliverables()["poster"])
    if poster is not None:
        run.wrote(poster)
    passes.finished(DELIVERY_PASSES[3])

    return run.result(
        AssembleResult,
        film=inputs.relative(inputs.workspace.film),
        film_seconds=ffmpeg.probe_duration(inputs.workspace.film),
        sections=_rendered_rows(inputs, rows),
        loudness=None if after is None else measured(inputs, after),
        seconds=since(started),
    )


def _takes(inputs: Inputs) -> Takes:
    """The take index, or the refusal that names the stage which writes it."""
    takes = inputs.takes()
    if takes is None:
        raise NotBuiltError(
            "the take index is not there, so no section has a length to cut to.",
            hint="Run `decktalk narrate` first, or `decktalk narrate --no-voice` to spend nothing.",
        )
    return takes


def _deliver(inputs: Inputs, run: Run, mixed: Path, work: Path, takes: Takes, *, loudness: bool, strict: bool
             ) -> audio.Loudness | None:  # fmt: skip
    """Encode the mixed soundtrack to its delivery codec, normalized when there is speech to normalize.

    The encode happens exactly once, here or inside the loudness pass, so nothing a viewer hears has
    been through AAC twice.
    """
    if takes.estimated or not loudness:
        # A placeholder narration is clicks and silence, and normalizing them would move the clicks
        # the a/v check listens for, so the pass is skipped and the result reports no loudness.
        why = "the narration is a placeholder" if takes.estimated else "the run asked for no loudness pass"
        run.note(f"The loudness pass is skipped because {why}, so the soundtrack is encoded as it was mixed.")
        encode_soundtrack(inputs, mixed, work)
        return None
    _before, after = normalize_loudness(inputs, mixed, work)
    missed = loudness_findings(inputs, run, after)
    if missed and strict:
        raise ToolError(
            f"the mix missed the loudness it was mastered to in {len(missed)} way(s).",
            hint="Run without --strict to publish it, or change [mix.loudness] to what this film is for.",
        )
    return after


def _write_deliverables(inputs: Inputs, run: Run, rows: list[Rendered], takes: Takes) -> Cuts:
    """The captions, the chapters, the cut list and the transcript, and the cut list they are read from."""
    paths = inputs.workspace.deliverables()
    starts = rendered_starts(rows)
    texts = caption_texts(inputs, takes)
    offsets = narration_offsets([row.section for row in rows], takes, starts)
    uncaptioned_sounds(inputs, run)
    spoken = sorted(build_captions(inputs, takes, offsets, texts) + clip_captions(inputs, run, rows),
                    key=lambda cue: cue.start)  # fmt: skip
    cues = with_sound_captions(spoken, sound_captions(inputs, starts))
    chapters = build_chapters(rows, inputs.chapters())
    write_caption_files(paths, cues, chapters)
    cuts = cut_list(inputs, rows)
    cuts.write(paths["cuts"])
    write_transcript_page(inputs, paths["transcript"], cuts, texts)
    for name in ("srt", "vtt", "chapters", "cuts", "transcript"):
        run.wrote(paths[name])
    return cuts


def _rendered_rows(inputs: Inputs, rows: list[Rendered]) -> tuple[RenderedSection, ...]:
    """Every section as the result publishes it, which is where it plays and what stood in for it."""
    starts = rendered_starts(rows)
    return tuple(
        RenderedSection(
            section=row.number,
            key=row.key,
            file=inputs.relative(row.path),
            start=round(starts[row.number], SECOND_DIGITS),
            seconds=round(row.seconds, SECOND_DIGITS),
            substitute=row.substitute,
        )
        for row in rows
    )


__all__ = ["MixPlan", "Rendered", "assemble"]
