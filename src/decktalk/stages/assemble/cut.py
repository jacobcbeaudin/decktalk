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

import logging
from dataclasses import dataclass
from pathlib import Path

from ...artifacts import BLACK, CLIP, PAGE, SLATE, Cut, Cuts, RecordingLog, Timeline
from ...errors import MissingInputError, ToolError
from ...jsonio import relative
from ...media import ffmpeg
from ...media.browser import render_slate
from ...media.encode import Encoder
from ...model import ClipSection, PageSection, Project, Section
from ...model.document import frame_dip

log = logging.getLogger(__name__)


@dataclass
class RenderedSection:
    """One section cut to its span: the file, how long it runs, and what it was made from."""

    section: Section
    path: Path
    duration: float
    note: str
    source: str = ""  # The recording or the clip this section was cut from, project-relative.
    substitute: str | None = None  # SLATE or BLACK when the real thing was missing.
    audio: Path | None = None  # A clip with its own sound, mixed in at the section start.


def section_slate(project: Project, sec: ClipSection) -> Path | None:
    """A titled slate PNG for a clip that is missing (rendered once, cached in build/out/slates)."""
    out = project.out_dir / "slates" / f"{sec.key}-slate.png"
    if out.exists():
        return out
    try:
        return render_slate(
            out,
            title=project.chapters()[sec.number],
            sub="Your clip goes here",
            eyebrow=f"section {sec.number} · slate",
            foot=f"drop it at {sec.clip} and run `decktalk assemble`",
            width=project.settings.video.width,
            height=project.settings.video.height,
            browser_path=project.settings.record.browser_path,
        )
    except ToolError as exc:
        log.warning("could not render a slate (%s); using a plain frame", exc)
        return None


def render_clip(
    project: Project,
    enc: Encoder,
    sec: ClipSection,
    out: Path,
    fades: tuple[bool, bool],
    dip: float,
    *,
    strict: bool,
) -> tuple[str, str, str | None, Path | None]:
    """(note, source, substitute, audio) for one clip section. Its picture alone goes to the file."""
    clip = project.path(sec.clip)
    if clip.exists():
        total = ffmpeg.probe_duration(clip)
        f = vfades(total, *fades, dip)
        ffmpeg.run(
            "-i", str(clip),
            "-filter_complex", f"[0:v]{enc.fit}{f}[v]",
            "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", str(out),
        )  # fmt: skip
        where = relative(clip, project.root)
        if ffmpeg.has_audio(clip):
            return f"{clip.name} (own audio)", where, None, clip
        log.warning("%s has no audio track; it plays silent", clip)
        return f"{clip.name} (silent)", where, None, None
    if strict and not sec.optional:
        raise MissingInputError(
            f"section {sec.number}: clip missing: {clip}. Put your clip at that path, or set optional = true "
            "on the section to play its slate under --strict."
        )
    secs = sec.slate_seconds
    if sec.optional:
        log.warning(
            "section %s: %s missing; slate for %gs (drop your clip at that path; the section is optional, "
            "so --strict allows the slate)",
            sec.key, sec.clip, secs,
        )  # fmt: skip
    else:
        log.warning("section %s: %s missing; slate for %gs (drop your clip at that path)", sec.key, sec.clip, secs)
    configured = project.path(project.mix.slate) if project.mix.slate else None
    png = configured if configured and configured.exists() else section_slate(project, sec)
    vin = (
        ["-loop", "1", "-framerate", str(enc.v.fps), "-t", f"{secs}", "-i", str(png)]
        if png
        else enc.color_source(enc.v.slate_color, secs)
    )
    f = vfades(secs, *fades, dip)
    ffmpeg.run(
        *vin,
        "-filter_complex", f"[0:v]{enc.fit}{f}[v]",
        "-map", "[v]", "-an", "-t", f"{secs}", *enc.venc, "-movflags", "+faststart", str(out),
    )  # fmt: skip
    return "slate", sec.clip, SLATE, None


def render_page(
    project: Project,
    enc: Encoder,
    sec: PageSection,
    out: Path,
    fades: tuple[bool, bool],
    dip: float,
    total: float,
    *,
    strict: bool,
) -> tuple[str, str, str | None]:
    """(note, source, substitute) for one page section, cut from its recording or from black."""
    webm = project.recording(sec)
    vlead = ""
    substitute: str | None = None
    source = relative(webm, project.root)
    if webm.exists():
        vin = ["-i", str(webm)]
        note = webm.name
        recording_log = RecordingLog.load(project.recording_log(sec))
        if recording_log is not None:
            vlead = f"trim=start={recording_log.trim_seconds},setpts=PTS-STARTPTS,"
            note += f" (t0 {recording_log.trim_seconds}s trimmed)"
    else:
        if strict:
            raise MissingInputError(f"section {sec.number}: recording missing: {webm}")
        log.warning("section %s: %s missing; using black", sec.key, webm.name)
        vin = enc.color_source("black", total)
        note, substitute = "black", BLACK
    f = vfades(total, *fades, dip)
    ffmpeg.run(
        *vin,
        "-filter_complex",
        f"[0:v]{vlead}{enc.fit},tpad=stop_mode=clone:stop=-1,trim=duration={total},setpts=PTS-STARTPTS{f}[v]",
        "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", "-t", f"{total}", str(out),
    )  # fmt: skip
    return note, source, substitute


def render_sections(project: Project, timeline: Timeline, *, strict: bool) -> list[RenderedSection]:
    enc = Encoder(project.settings.video)
    project.out_dir.mkdir(parents=True, exist_ok=True)
    project.sections_dir.mkdir(parents=True, exist_ok=True)
    flags = project.document.fade_flags
    targets = timeline_targets(timeline, enc.v.fps)
    dip = frame_dip(project.transition.dip_seconds, enc.v.fps)
    rows: list[RenderedSection] = []
    for sec in project.sections:
        out = project.section_video(sec)
        audio: Path | None = None
        if isinstance(sec, ClipSection):
            note, source, substitute, audio = render_clip(project, enc, sec, out, flags[sec.key], dip, strict=strict)
        else:
            total = targets.get(sec.key, 0.0)
            if total <= 0:
                raise MissingInputError(
                    f"section {sec.key} has no span in {project.timeline_path}; run `decktalk narrate`"
                )
            total = round(total + sec.hold_seconds, 3)  # the narration pauses for the hold, as it does for a clip
            note, source, substitute = render_page(project, enc, sec, out, flags[sec.key], dip, total, strict=strict)
        dur = ffmpeg.probe_duration(out)
        log.info("[cut ] %s  %s -> %s  (%.3fs)", sec.key, note, out.name, dur)
        rows.append(
            RenderedSection(
                section=sec, path=out, duration=dur, note=note, source=source, substitute=substitute, audio=audio
            )
        )
    return rows


def cut_list(project: Project, rows: list[RenderedSection]) -> Cuts:
    """The cut list: where each section plays, what it was cut from, and what stands in for it."""
    starts = rendered_starts(rows)
    flags = project.document.fade_flags
    chapters = project.chapters()
    cuts = Cuts(fps=project.settings.video.fps, total_seconds=round(sum(r.duration for r in rows), 3))
    for row in rows:
        key = row.section.key
        dip_in, dip_out = flags.get(key, (False, False))
        cuts.sections.append(
            Cut(
                section=row.section.number,
                kind=CLIP if row.section.is_clip else PAGE,
                start=round(starts[key], 3),
                end=round(starts[key] + row.duration, 3),
                source=row.source,
                chapter=chapters[row.section.number],
                substitute=row.substitute,
                dip_in=dip_in,
                dip_out=dip_out,
            )
        )
    return cuts


def rendered_starts(rows: list[RenderedSection]) -> dict[str, float]:
    """Where each section begins in the final file, added up from the lengths the cut rendered."""
    starts: dict[str, float] = {}
    t = 0.0
    for row in rows:
        starts[row.section.key] = t
        t += row.duration
    return starts


def concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{f}'\n" for f in files), encoding="utf-8")
    try:
        ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out))
    finally:
        lst.unlink(missing_ok=True)


def timeline_targets(timeline: Timeline, fps: int) -> dict[str, float]:
    """Frame-exact video length per spoken section, from cumulative frame boundaries."""
    targets: dict[str, float] = {}
    for key, sec in timeline.sections.items():
        start_f = round(sec.start * fps)
        end_f = round(sec.end * fps)
        targets[key] = (end_f - start_f) / fps
    return targets


def vfades(total: float, fade_in: bool, fade_out: bool, dip: float) -> str:
    out = ""
    if fade_in:
        out += f",fade=t=in:st=0:d={dip}"
    if fade_out:
        out += f",fade=t=out:st={max(total - dip, 0):.3f}:d={dip}"
    return out


def stray_warnings(project: Project, command: str) -> list[str]:
    """Warn about every leftover section video, and return what was said."""
    messages = project.stray_section_warnings(command)
    for message in messages:
        log.warning(message)
    return messages
