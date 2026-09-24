"""Every section becomes one silent mp4, and the cut list records where each one plays.

A page section is cut to its exact span in the narration, rounded on cumulative frame boundaries so
the picture never drifts, with the recorder's lead-in trimmed off the head and the last frame cloned
to fill. A clip section plays its own file, or a titled slate when that file is missing. A page
section with no recording plays black. No intermediate carries audio, so the concatenation cannot
reintroduce AAC priming and the picture starts at pts 0 as the sound does.

`cuts.json` is written from the same rows, so where a section plays, what it was made from and
whether a slate or a black frame stands in for it are all recorded once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from decktalk.artifacts import Cut, Cuts, RecordingLog, Takes
from decktalk.errors import InputError, NotBuiltError, ToolError
from decktalk.events import Level, Unit
from decktalk.findings import Code, Location
from decktalk.inputs import ClipSection, Inputs, PageSection, Section
from decktalk.inputs.document import frame_dip
from decktalk.machine import Run
from decktalk.media import browser, ffmpeg
from decktalk.media.encode import Encoder
from decktalk.pipeline import Stage
from decktalk.results import SectionKind, Substitute
from decktalk.stages import SECOND_DIGITS, judge, selects


def encoder(inputs: Inputs) -> Encoder:
    """The encoder every output of this stage is made with, built from `[video]` once."""
    return Encoder(inputs.settings.video)


BLACK = "0x000000"
"""Truth: the colour a section with no recording plays, written the way ffmpeg reads a colour."""

SLATES_DIR = "slates"
"""Where a rendered slate is kept under the final directory, so a second run draws none of them again."""


@dataclass(frozen=True)
class Rendered:
    """One section cut to its span: the file, how long it runs, and what it was made from.

    This is the working row the cut, the mix and the captions all read. The result model of the same
    idea is `results.RenderedSection`, which carries what a reader receives rather than the paths a
    filter graph is built from, and `rendered_rows` is the one place the two meet.
    """

    section: Section
    path: Path
    seconds: float
    note: str
    source: str
    substitute: Substitute | None = None
    audio: Path | None = None
    """A clip with its own sound, which is mixed in at the section start."""

    missing: str | None = None
    """The file the project names and has not got, project-relative, or None when nothing is missing."""

    @property
    def key(self) -> str:
        return self.section.key

    @property
    def number(self) -> int:
        return self.section.number


def section_slate(inputs: Inputs, run: Run, section: ClipSection) -> Path | None:
    """A titled slate for a clip that is missing, drawn once and kept beside the film.

    The slate is drawn on the colour `[video] slate_color` names, which is the same colour the plain
    frame behind it is drawn on, so a project that sets one colour gets one colour either way.
    """
    out = inputs.workspace.final_dir / SLATES_DIR / f"{section.key}-slate.png"
    if out.exists():
        return out
    video = inputs.settings.video
    try:
        return browser.render_slate(
            out,
            title=inputs.chapters()[section.number],
            sub="Your clip goes here",
            eyebrow=f"section {section.number}: slate",
            foot=f"drop it at {section.clip} and run `decktalk assemble`",
            width=video.width,
            height=video.height,
            background=video.slate_color,
            browser_path=inputs.settings.record.browser_path,
        )
    except ToolError as refused:
        run.note(f"A slate could not be drawn ({refused}), so section {section.number} plays a plain frame.",
                 level=Level.WARNING)  # fmt: skip
        return None


def render_clip(inputs: Inputs, run: Run, enc: Encoder, section: ClipSection, out: Path, dip: float, *, strict: bool
                ) -> Rendered:  # fmt: skip
    """One clip section cut to its own length, or the slate that stands in for a clip the project has not got."""
    fades = inputs.document.fade_flags[section.key]
    clip = inputs.path(section.clip)
    if clip.exists():
        total = ffmpeg.probe_duration(clip)
        ffmpeg.run(
            "-i", str(clip),
            "-filter_complex", f"[0:v]{enc.fit}{vfades(total, *fades, dip)}[v]",
            "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", str(out),
        )  # fmt: skip
        sounds = ffmpeg.has_audio(clip)
        if not sounds:
            run.note(f"{section.clip} carries no audio track, so section {section.number} plays silent.",
                     level=Level.WARNING)  # fmt: skip
        note = f"{clip.name} (own audio)" if sounds else f"{clip.name} (silent)"
        return Rendered(section, out, ffmpeg.probe_duration(out), note, str(inputs.relative(clip).as_posix()),
                        audio=clip if sounds else None)  # fmt: skip
    if strict and not section.optional:
        raise InputError(
            f"section {section.number} names the clip {section.clip}, which is not there.",
            hint="Put your clip at that path, or set optional = true on the section to play its slate instead.",
            location=Location(where=section.clip, file=inputs.relative(clip), section=section.number),
        )
    return _render_slate_section(inputs, run, enc, section, out, dip)


def _render_slate_section(
    inputs: Inputs, run: Run, enc: Encoder, section: ClipSection, out: Path, dip: float
) -> Rendered:
    """The picture a section plays when the clip it names is not there, which is a slate or a plain frame."""
    seconds = section.slate_seconds
    configured = inputs.path(inputs.document.mix.slate) if inputs.document.mix.slate else None
    png = configured if configured is not None and configured.exists() else section_slate(inputs, run, section)
    source = (
        ["-loop", "1", "-framerate", str(enc.v.output_fps), "-t", f"{seconds}", "-i", str(png)]
        if png
        else enc.color_source(enc.v.slate_color, seconds)
    )
    ffmpeg.run(
        *source,
        "-filter_complex", f"[0:v]{enc.fit}{vfades(seconds, *inputs.document.fade_flags[section.key], dip)}[v]",
        "-map", "[v]", "-an", "-t", f"{seconds}", *enc.venc, "-movflags", "+faststart", str(out),
    )  # fmt: skip
    return Rendered(
        section=section,
        path=out,
        seconds=ffmpeg.probe_duration(out),
        note=Substitute.SLATE.value,
        source=section.clip,
        substitute=Substitute.SLATE,
        missing=section.clip,
    )


def render_page(inputs: Inputs, run: Run, enc: Encoder, section: PageSection, out: Path, dip: float, total: float, *,
                strict: bool) -> Rendered:  # fmt: skip
    """One page section cut to its span in the narration, from its recording or from black."""
    webm = inputs.workspace.recording(section.key)
    source = str(inputs.relative(webm).as_posix())
    fades = inputs.document.fade_flags[section.key]
    if not webm.exists():
        if strict:
            raise NotBuiltError(
                f"section {section.number} has no recording at {source}.",
                hint="Run `decktalk record` first.",
            )
        run.note(f"{source} is not there, so section {section.number} plays black.", level=Level.WARNING)
        ffmpeg.run(
            *enc.color_source(BLACK, total),
            "-filter_complex",
            f"[0:v]{enc.fit},trim=duration={total},setpts=PTS-STARTPTS{vfades(total, *fades, dip)}[v]",
            "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", "-t", f"{total}", str(out),
        )  # fmt: skip
        return Rendered(section, out, ffmpeg.probe_duration(out), Substitute.BLACK.value, source,
                        substitute=Substitute.BLACK, missing=source)  # fmt: skip
    # The recorder covers the page until it starts the narration clock, so the head of the webm is
    # trimmed at the moment its own log recorded as narration t=0.
    log = RecordingLog.read(inputs.workspace.recording_log(section.key))
    lead = "" if log is None else f"trim=start={log.trim_seconds},setpts=PTS-STARTPTS,"
    note = webm.name if log is None else f"{webm.name} (t0 {log.trim_seconds}s trimmed)"
    ffmpeg.run(
        "-i", str(webm),
        "-filter_complex",
        f"[0:v]{lead}{enc.fit},tpad=stop_mode=clone:stop=-1,trim=duration={total},"
        f"setpts=PTS-STARTPTS{vfades(total, *fades, dip)}[v]",
        "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", "-t", f"{total}", str(out),
    )  # fmt: skip
    return Rendered(section, out, ffmpeg.probe_duration(out), note, source)


def page_target(takes: Takes, section: PageSection, fps: int) -> float:
    """How long one page section runs in the film, which is its span in the narration plus its hold."""
    span = section_targets(takes, fps).get(section.number, 0.0)
    if span <= 0:
        raise NotBuiltError(
            f"section {section.number} has no span in the take index.",
            hint="Run `decktalk narrate` first.",
        )
    # The narration pauses for a hold exactly as it pauses for a clip, so the hold is picture alone.
    return round(span + section.hold_seconds, SECOND_DIGITS)


def render_sections(
    inputs: Inputs, run: Run, takes: Takes, *, only: list[int] | None, strict: bool, passes: int | None = None
) -> list[Rendered]:
    """Every section of the film cut to its span, in the order the film plays them.

    A film is always whole, so every section is in the answer. `only` decides which of them are cut
    again: a section it does not name whose cut is already on disk is kept, because re-encoding a
    picture that has not moved buys nothing and costs the longest pass in the stage.

    `passes` is how many passes the whole stage runs, so the cuts count against the same total the
    passes after them do and a renderer never sees one bar restart inside one stage.
    """
    enc = encoder(inputs)
    inputs.workspace.final_dir.mkdir(parents=True, exist_ok=True)
    inputs.workspace.sections_dir.mkdir(parents=True, exist_ok=True)
    dip = frame_dip(inputs.document.transition.dip_seconds, enc.v.output_fps)
    wanted = selects(only)
    sections = inputs.document.sections
    rows: list[Rendered] = []
    for done, section in enumerate(sections, start=1):
        run.check()
        out = inputs.workspace.section_video(section.key)
        row = _kept(inputs, section, out) if not wanted(section.number) else None
        if row is None:
            row = _cut_one(inputs, run, enc, takes, section, out, dip, strict=strict)
        rows.append(row)
        run.wrote(out)
        run.progress(Stage.ASSEMBLE, done=done, total=passes or len(sections), unit=Unit.PASS,
                     label=f"cut section {section.number}", section=section.number)  # fmt: skip
    _judge_missing(run, rows)
    return rows


def _cut_one(inputs: Inputs, run: Run, enc: Encoder, takes: Takes, section: Section, out: Path, dip: float, *,
             strict: bool) -> Rendered:  # fmt: skip
    """One section cut again, whichever kind of section it is."""
    if isinstance(section, ClipSection):
        return render_clip(inputs, run, enc, section, out, dip, strict=strict)
    total = page_target(takes, section, enc.v.output_fps)
    return render_page(inputs, run, enc, section, out, dip, total, strict=strict)


def _kept(inputs: Inputs, section: Section, out: Path) -> Rendered | None:
    """The row for a cut already on disk that this run was not asked to make again, or None."""
    if not out.exists():
        return None
    source = section.clip if isinstance(section, ClipSection) else inputs.relative(
        inputs.workspace.recording(section.key)
    ).as_posix()  # fmt: skip
    return Rendered(section, out, ffmpeg.probe_duration(out), f"{out.name} (kept)", str(source))


def _judge_missing(run: Run, rows: list[Rendered]) -> None:
    """One judgement per section whose own file the project names and has not got."""
    for row in rows:
        if row.missing is None:
            continue
        stood_in = "a slate" if row.substitute is Substitute.SLATE else "a black frame"
        run.found(
            judge(
                Code.FILE_MISSING,
                f"section {row.number} names {row.missing}, which is not on disk, so {stood_in} plays for "
                f"{row.seconds:.2f}s in its place.",
                Location(where=row.missing, file=Path(row.missing), section=row.number),
                stage=Stage.ASSEMBLE,
            )
        )


def cut_list(inputs: Inputs, rows: list[Rendered]) -> Cuts:
    """The cut list: where each section plays, what it was cut from, and what stands in for it."""
    starts = rendered_starts(rows)
    flags = inputs.document.fade_flags
    chapters = inputs.chapters()
    return Cuts(
        fps=inputs.settings.video.output_fps,
        sections=tuple(
            Cut(
                section=row.number,
                key=row.key,
                kind=SectionKind.CLIP if row.section.is_clip else SectionKind.PAGE,
                start=round(starts[row.number], SECOND_DIGITS),
                end=round(starts[row.number] + row.seconds, SECOND_DIGITS),
                source=Path(row.source),
                chapter=chapters[row.number],
                substitute=row.substitute,
                dip_in=flags.get(row.key, (False, False))[0],
                dip_out=flags.get(row.key, (False, False))[1],
            )
            for row in rows
        ),
    )


def rendered_starts(rows: list[Rendered]) -> dict[int, float]:
    """Where each section begins in the finished film, added up from the lengths the cut rendered."""
    starts: dict[int, float] = {}
    at = 0.0
    for row in rows:
        starts[row.number] = at
        at += row.seconds
    return starts


def concat(files: list[Path], out: Path) -> None:
    """Join the section cuts into one picture, with no re-encoding and no gaps between them."""
    listing = out.with_suffix(".concat.txt")
    listing.write_text(ffmpeg.concat_list(files), encoding="utf-8")
    try:
        ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out))
    finally:
        listing.unlink(missing_ok=True)


def section_targets(takes: Takes, fps: int) -> dict[int, float]:
    """Frame-exact video length per spoken section, from cumulative frame boundaries.

    Each length is the difference between two rounded cumulative boundaries rather than one rounded
    length, so the sections add up to the narration exactly and the picture never drifts off it.
    """
    targets: dict[int, float] = {}
    for take in takes.sections:
        start, end = takes.start(take.section), takes.end(take.section)
        if start is None or end is None:
            continue
        targets[take.section] = (round(end * fps) - round(start * fps)) / fps
    return targets


def vfades(total: float, fade_in: bool, fade_out: bool, dip: float) -> str:
    """The fade filters one section carries, which is what a dip to black at a cut is made of."""
    filters = ""
    if fade_in:
        filters += f",fade=t=in:st=0:d={dip}"
    if fade_out:
        filters += f",fade=t=out:st={max(total - dip, 0):.3f}:d={dip}"
    return filters


def stray_cuts(inputs: Inputs, run: Run) -> None:
    """Say which leftover section cuts this film leaves out, which is what a renumbering leaves behind."""
    for path in inputs.stray_section_videos():
        run.note(
            f"{inputs.relative(path).as_posix()} is a cut of a section decktalk.toml no longer declares, "
            "so it is left out of the film.",
            level=Level.WARNING,
        )


__all__ = [
    "BLACK",
    "Rendered",
    "concat",
    "cut_list",
    "page_target",
    "render_clip",
    "render_page",
    "render_sections",
    "rendered_starts",
    "section_slate",
    "section_targets",
    "stray_cuts",
    "vfades",
]
