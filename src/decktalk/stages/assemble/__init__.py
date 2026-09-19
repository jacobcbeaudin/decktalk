"""Stage 4: the recordings, the narration, the clips and the soundscape become one mp4.

    cut.py        every section as one silent mp4, and the cut list
    mix.py        the whole soundtrack as one filter graph, one MixInput per layer
    loudness.py   the two-pass normalization and its report
    publish.py    captions, chapters, the transcript, the poster and the atomic final file

The order is fixed. Each section is cut to its span and the sections are concatenated with no gaps.
The soundtrack is mixed over a silent anchor of the picture's length. The mix is normalized to the
EBU R128 target unless the narration is a placeholder, whose clicks the a/v check listens for. Then
everything a viewer receives is written, and the finished film is renamed into place in one step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import Cuts, Takes
from ...captions import write_transcript
from ...errors import MissingInputError, ToolError
from ...jsonio import as_json, relative
from ...media import audio, ffmpeg
from ...media.encode import Encoder
from ...model import Project
from ...verdicts import Finding, Findings, Verdict
from .cut import RenderedSection, concat, cut_list, render_sections, rendered_starts, stray_warnings
from .loudness import loudness_problems, normalize_loudness
from .mix import MixInput, MixPlan, encode_soundtrack, mix_input_args, narration_offsets, plan_mix
from .publish import (
    build_captions,
    build_chapters,
    caption_texts,
    clip_captions,
    publish,
    render_poster,
    sound_captions,
    transcript_sections,
    uncaptioned_sounds,
    with_sound_captions,
    write_caption_files,
)

log = logging.getLogger(__name__)

__all__ = [
    "AssembleResult",
    "MixInput",
    "MixPlan",
    "RenderedSection",
    "assemble",
    "cut_list",
    "plan_mix",
    "render_sections",
]


@dataclass
class AssembleResult:
    """The finished video and everything written beside it."""

    final: Path
    stamped: Path | None  # The timestamped copy, when [output] timestamped_copy is on.
    duration: float
    sections: list[RenderedSection]
    warnings: list[str]
    loudness: tuple[audio.Loudness, audio.Loudness] | None
    cuts: Cuts | None = None
    captions_srt: Path | None = None
    captions_vtt: Path | None = None
    chapters: Path | None = None
    cuts_file: Path | None = None
    transcript: Path | None = None
    poster: Path | None = None
    loudness_problems: list[Finding] = field(default_factory=list)  # A peak over the ceiling, or a missed target.
    rows: list[Finding] = field(default_factory=list)  # A cued sound that names no caption line.

    @property
    def substituted(self) -> list[RenderedSection]:
        """The sections that played a slate or a black frame instead of the real thing."""
        return [row for row in self.sections if row.substitute is not None]

    @property
    def substitutions(self) -> list[Finding]:
        """One `SLATE` row per section a slate or a black frame stood in for, naming the file that is missing."""
        return [
            Finding(
                detail=f"section {row.section.key} plays {row.substitute.value} because {row.source} is not there",
                verdict=Verdict.SLATE,
                section=row.section.number,
                where=row.source,
            )
            for row in self.sections
            if row.substitute is not None
        ]

    @property
    def written(self) -> list[Path]:
        """Every file this run wrote, in the order it wrote them."""
        made = [self.captions_srt, self.captions_vtt, self.chapters, self.cuts_file, self.transcript, self.poster]
        return [path for path in (*made, self.final, self.stamped) if path is not None]

    @property
    def findings(self) -> Findings:
        """Uncertain: a slate or black section, a loudness miss, and a cued sound with no caption."""
        return (
            Findings.of(row.verdict for row in self.loudness_problems)
            + Findings.of(row.verdict for row in self.substitutions)
            + Findings.of(row.verdict for row in self.rows)
        )

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The build as JSON-ready data, with every written path relative to the project root."""
        before, after = self.loudness if self.loudness else (None, None)
        return {
            "final": relative(self.final, root),
            "duration": round(self.duration, 3),
            "stamped": None if self.stamped is None else relative(self.stamped, root),
            "sections": [
                {
                    "key": row.section.key,
                    "source": row.note,
                    "substitute": None if row.substitute is None else row.substitute.value,
                    "duration": round(row.duration, 3),
                    "path": relative(row.path, root),
                }
                for row in self.sections
            ],
            "captions": {
                "srt": None if self.captions_srt is None else relative(self.captions_srt, root),
                "vtt": None if self.captions_vtt is None else relative(self.captions_vtt, root),
                "chapters": None if self.chapters is None else relative(self.chapters, root),
                "transcript": None if self.transcript is None else relative(self.transcript, root),
            },
            "cuts": None if self.cuts_file is None else relative(self.cuts_file, root),
            "poster": None if self.poster is None else relative(self.poster, root),
            "loudness": None
            if after is None or before is None
            else {
                "before": as_json(before),
                "after": as_json(after),
                "problems": [row.to_dict() for row in self.loudness_problems],
            },
            "warnings": list(self.warnings),
            "uncaptioned": [row.to_dict() for row in self.rows],
            "substituted": [row.to_dict() for row in self.substitutions],
        }


def mix_soundtrack(
    project: Project, rows: list[RenderedSection], takes: Takes, work: Path, *, soundscape: bool
) -> MixPlan:
    """Concatenate the sections and lay the whole soundtrack under them, into one work file.

    The soundtrack is written as floating-point samples, so a sum of layers louder than 0 dBFS is
    carried rather than clipped, and the delivery encoder runs once, downstream of the limiter.
    """
    out_dir = project.out_dir
    picture = out_dir / ".picture.mp4"
    log.info("[cat ] %d sections, %s", len(rows), project.document.cut_summary)
    concat([r.path for r in rows], picture)
    plan = plan_mix(project, rows, takes, soundscape=soundscape)
    for message in plan.warnings:
        log.warning(message)
    enc = Encoder(project.settings.video)
    log.info("[mix ] %d audio input(s) -> %s", len(plan.inputs), project.final.name)
    try:
        ffmpeg.run(
            "-i", str(picture), *mix_input_args(plan),
            "-filter_complex", plan.filter,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", *enc.amix, str(work),
        )  # fmt: skip
    finally:
        picture.unlink(missing_ok=True)
    return plan


def deliver(
    project: Project, mixed: Path, work: Path, takes: Takes, *, loudness: bool, strict: bool
) -> tuple[tuple[audio.Loudness, audio.Loudness] | None, list[Finding]]:
    """Encode the mixed soundtrack to its delivery codec, normalized when there is speech to normalize.

    Returns (the loudness before and after, the problems). The encode happens exactly once, here or
    in `normalize_loudness`, so nothing a viewer hears has been through AAC twice.
    """
    if takes.estimated or not loudness:
        # A build without voice carries clicks and silence, and normalizing them would move the clicks
        # the a/v check listens for, so the pass is skipped and the result has no loudness.
        why = "the narration is a silent placeholder" if takes.estimated else "--no-loudness was passed"
        log.info("[loud] skipped: %s, so the soundtrack is encoded as it was mixed", why)
        encode_soundtrack(project, mixed, work)
        return None, []
    measured = normalize_loudness(project, mixed, work)
    b, a = measured
    ln = project.mix.loudness
    log.info(
        "[loud] I %.1f -> %.1f LUFS (target %.1f), TP %.1f -> %.1f dBTP (ceiling %.1f), LRA %.1f -> %.1f LU",
        b.i, a.i, ln.target_lufs, b.tp, a.tp, ln.true_peak_db, b.lra, a.lra,
    )  # fmt: skip
    problems = loudness_problems(project, a)
    for row in problems:
        log.warning("[loud] %s", row.detail)
    if problems and strict:
        raise ToolError("loudness: " + ", ".join(row.detail for row in problems))
    return measured, problems


def assemble(
    project: Project, *, soundscape: bool = True, loudness: bool = True, strict: bool = False
) -> AssembleResult:
    """Cut, mix, normalize and publish the whole film, with everything a viewer receives beside it."""
    takes = project.takes()
    if takes is None:
        raise MissingInputError(
            f"{relative(project.takes_path, project.root)} is not there, so there is nothing to assemble.",
            hint="Run `decktalk narrate` first.",
            path=project.takes_path,
        )
    paths = project.workspace.output_paths()
    warnings = stray_warnings(project)
    rows = render_sections(project, takes, strict=strict)

    work = project.out_dir / f".{project.name}.tmp.mp4"
    mixed = project.out_dir / f".{project.name}.mix.mov"
    for path in (work, mixed):
        path.unlink(missing_ok=True)
    plan = mix_soundtrack(project, rows, takes, mixed, soundscape=soundscape)
    warnings += plan.warnings
    try:
        measured, problems = deliver(project, mixed, work, takes, loudness=loudness, strict=strict)
    finally:
        mixed.unlink(missing_ok=True)

    starts = rendered_starts(rows)
    texts = caption_texts(project, takes)
    cues = build_captions(project, takes, narration_offsets(rows, takes, starts), texts)
    cues = with_sound_captions(
        sorted(cues + clip_captions(project, rows), key=lambda c: c.start), sound_captions(project, starts)
    )
    chapters = build_chapters(rows, project.chapters())
    write_caption_files(paths, cues, chapters)
    cuts = cut_list(project, rows)
    cuts.save(paths["cuts"])
    write_transcript(
        paths["transcript"], project.name, transcript_sections(project, cuts, texts), language=project.document.language
    )
    stamped = publish(project, work, chapters, paths)
    # The poster is drawn last, because it is one picture beside a film that is already finished.
    poster = render_poster(project, paths["poster"])

    duration = ffmpeg.probe_duration(project.final)
    log.info("done: %s  (%.2fs)%s", project.final, duration, f"  copy: {stamped.name}" if stamped else "")
    return AssembleResult(
        final=project.final,
        stamped=stamped,
        duration=duration,
        sections=rows,
        warnings=warnings,
        loudness=measured,
        loudness_problems=problems,
        rows=uncaptioned_sounds(project),
        cuts=cuts,
        cuts_file=paths["cuts"],
        captions_srt=paths["srt"],
        captions_vtt=paths["vtt"],
        chapters=paths["chapters"],
        transcript=paths["transcript"],
        poster=poster,
    )
