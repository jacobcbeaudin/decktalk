"""Stage 5: recordings, narration, clips and the soundscape become the final mp4 (ffmpeg).

1. Every section becomes a video-only NN-section.mp4. A page section is cut to its exact
   span in the timeline, rounded on cumulative frame boundaries so the picture never
   drifts; the recorder lead-in is trimmed off the head and the last frame is cloned to
   fill. A missing clip becomes a titled slate. A missing recording becomes black. No
   intermediate carries audio, so the concatenation cannot reintroduce AAC priming and
   the picture starts at pts 0 like the sound does.
2. The sections are concatenated with no gaps (concat demuxer, stream copy).
3. The whole soundtrack is one mix over a silent anchor of the picture's length: the
   narration from the first page section, each clip's own audio delayed to its section
   start with a 20 ms fade at both ends, and the optional beds. A clip between page
   sections pauses the narration, so the track is split into runs of consecutive page
   sections and each run starts where its first section starts. An underscore is ducked
   under speech and shaped by markers.json, an ambience bed sits under sections flagged
   ambience, and one-shot sfx land on resolved cues.
4. EBU R128 loudness: pass one measures integrated loudness and true peak, pass two
   applies the gain that reaches the target and a true-peak limiter at the ceiling,
   oversampled at 192 kHz. The result is measured again and reported.
5. Captions (srt and vtt) come from the word timestamps, plus the words file of any clip
   section that names one, and chapter markers from the section titles are muxed into the
   mp4. Consecutive sections with the same title share one chapter.
6. Atomic publish: work file, then one rename to build/out/<name>.mp4, plus a
   timestamped copy.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import (
    Beats,
    CaptionCue,
    Chapter,
    Manifest,
    Sidecar,
    Timeline,
    Word,
    caption_cues,
    display_words,
    read_words,
    write_chapters,
    write_srt,
    write_vtt,
)
from ..config import AudioConfig, VideoConfig
from ..errors import ConfigError, MissingInputError, ToolError
from ..media import ffmpeg
from ..media.browser import render_slate
from ..project import ClipSection, PageSection, Project, Section
from .beats import find_phrase
from .measure import stale_measure

log = logging.getLogger(__name__)

CLIP_FADE_SECONDS = 0.02  # Every clip's audio fades in and out over this long, so a cut never clicks.
LIMITER_HEADROOM_DB = 0.3  # The limiter works on oversampled samples, so it sits a little under the true-peak ceiling.
LIMITER_OVERSAMPLE_RATE = 192000  # The true-peak limiter runs at this rate and resamples back afterwards.
LOUDNESS_TOLERANCE_LU = 1.0  # The measured result may sit this far from the integrated target.


# ---- small helpers ------------------------------------------------------------------


def db(x: float) -> float:
    return 10 ** (x / 20)


def ramp_expr(a: float, b: float, r: float) -> str:
    """0 outside [a,b], 1 inside, linear ramps of r seconds at both edges."""
    return f"min(1,max(0,(t-{a:.3f})/{r}))*min(1,max(0,({b:.3f}-t)/{r}))"


def max_expr(terms: list[str]) -> str:
    if not terms:
        return "0"
    expr = terms[0]
    for t in terms[1:]:
        expr = f"max({expr},{t})"
    return expr


def timeline_targets(timeline: Timeline, fps: int) -> dict[str, float]:
    """Frame-exact video length per spoken section, from cumulative frame boundaries."""
    targets: dict[str, float] = {}
    for key, sec in timeline.sections.items():
        start_f = round(sec.start * fps)
        end_f = round(sec.end * fps)
        targets[key] = (end_f - start_f) / fps
    return targets


def frame_dip(dip_seconds: float, fps: int) -> float:
    """The dip length quantized to whole frames, so a fade never ends part way through one."""
    if dip_seconds <= 0:
        return 0.0
    return round(max(round(dip_seconds * fps), 1) / fps, 4)


def fade_flags(project: Project) -> dict[str, tuple[bool, bool]]:
    """(fade_in, fade_out) per section key from the transition config."""
    sections = project.sections
    tr = project.transition
    pairs = {(a, b) for a, b in tr.dips} if tr.dips is not None else None
    flags: dict[str, tuple[bool, bool]] = {}
    for i, sec in enumerate(sections):
        prev_n = sections[i - 1].number if i > 0 else None
        next_n = sections[i + 1].number if i + 1 < len(sections) else None
        if pairs is None:
            dip_in, dip_out = prev_n is not None, next_n is not None
        else:
            dip_in = prev_n is not None and (prev_n, sec.number) in pairs
            dip_out = next_n is not None and (sec.number, next_n) in pairs
        fade_in = dip_in and not (not sec.is_clip and tr.page_fades_in)
        flags[sec.key] = (fade_in, dip_out)
    return flags


def cut_summary(project: Project) -> str:
    """What happens at the section cuts, for the assemble log: straight cuts, or dips at some or all of them."""
    dips = sum(1 for _fade_in, fade_out in fade_flags(project).values() if fade_out)
    if dips == 0:
        return "straight cuts"
    if project.transition.dips is None:
        return "dips at every cut"
    return f"dips at {dips} cut{'s' if dips != 1 else ''}"


def vfades(total: float, fade_in: bool, fade_out: bool, dip: float) -> str:
    out = ""
    if fade_in:
        out += f",fade=t=in:st=0:d={dip}"
    if fade_out:
        out += f",fade=t=out:st={max(total - dip, 0):.3f}:d={dip}"
    return out


class _Encoder:
    """The x264 and AAC settings every intermediate and the final file share.

    The frame rate comes from the fps filter in `fit`, so the encoder takes no -r of its
    own. Every output is tagged BT.709 and keyframed every two seconds. The tag is set
    twice on purpose: the encoder flags cover older ffmpeg builds, and the setparams filter
    covers ffmpeg 9, which takes the colour properties from the frames rather than from
    those flags.
    """

    def __init__(self, video: VideoConfig) -> None:
        self.v = video
        self.fit = (
            f"scale={video.width}:{video.height}:force_original_aspect_ratio=decrease,"
            f"pad={video.width}:{video.height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={video.fps},format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"
        )
        gop = str(2 * video.fps)
        self.venc = [
            "-c:v", "libx264",
            "-preset", video.preset,
            "-crf", str(video.crf),
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-g", gop,
            "-keyint_min", gop,
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
        ]  # fmt: skip
        self.aenc = [
            "-c:a", "aac",
            "-b:a", video.audio_bitrate,
            "-ar", str(video.sample_rate),
            "-ac", str(video.channels),
        ]  # fmt: skip
        self.silence = f"anullsrc=r={video.sample_rate}:cl=stereo"

    def color_source(self, color: str, seconds: float) -> list[str]:
        return [
            "-f",
            "lavfi",
            "-t",
            f"{seconds}",
            "-i",
            f"color=c={color}:s={self.v.width}x{self.v.height}:r={self.v.fps}",
        ]


# ---- stage 1: sections -----------------------------------------------------------------


@dataclass
class RenderedSection:
    section: Section
    path: Path
    duration: float
    note: str
    audio: Path | None = None  # A clip with its own sound, mixed in at the section start.
    warning: str | None = None  # Why the section may be out of sync, such as a stale measurement.


def section_slate(project: Project, sec: ClipSection) -> Path | None:
    """A titled slate PNG for a clip that is missing (rendered once, cached in build/out/slates)."""
    out = project.out_dir / "slates" / f"{sec.key}-slate.png"
    if out.exists():
        return out
    try:
        return render_slate(
            out,
            title=sec.title or f"Section {sec.number}",
            sub="Your clip goes here",
            eyebrow=f"section {sec.number} · slate",
            foot=f"drop it at {sec.clip} and run `decktalk assemble`",
            width=project.settings.video.width,
            height=project.settings.video.height,
        )
    except ToolError as exc:
        log.warning("could not render a slate (%s); using a plain frame", exc)
        return None


def _render_clip(
    project: Project,
    enc: _Encoder,
    sec: ClipSection,
    out: Path,
    fades: tuple[bool, bool],
    dip: float,
    *,
    strict: bool,
) -> tuple[str, Path | None]:
    """Render the clip's picture only. Its audio, when it has any, is returned for the mix."""
    clip = project.path(sec.clip)
    if clip.exists():
        total = ffmpeg.probe_duration(clip)
        f = vfades(total, *fades, dip)
        ffmpeg.run(
            "-i", str(clip),
            "-filter_complex", f"[0:v]{enc.fit}{f}[v]",
            "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", str(out),
        )  # fmt: skip
        if ffmpeg.has_audio(clip):
            return f"{clip.name} (own audio)", clip
        log.warning("%s has no audio track; it plays silent", clip)
        return f"{clip.name} (silent)", None
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
    return "slate", None


def _render_page(
    project: Project,
    enc: _Encoder,
    sec: PageSection,
    out: Path,
    fades: tuple[bool, bool],
    dip: float,
    total: float,
    *,
    strict: bool,
) -> str:
    webm = project.recording(sec)
    vlead = ""
    if webm.exists():
        vin = ["-i", str(webm)]
        note = webm.name
        side = project.rec_dir / f"{sec.key}-scene.json"
        sidecar = Sidecar.load(side)
        if sidecar is not None:
            vlead = f"trim=start={sidecar.trim_seconds},setpts=PTS-STARTPTS,"
            note += f" (lead {sidecar.trim_seconds}s trimmed)"
    else:
        if strict:
            raise MissingInputError(f"section {sec.number}: recording missing: {webm}")
        log.warning("section %s: %s missing; using black", sec.key, webm.name)
        vin = enc.color_source("black", total)
        note = "black"
    f = vfades(total, *fades, dip)
    ffmpeg.run(
        *vin,
        "-filter_complex",
        f"[0:v]{vlead}{enc.fit},tpad=stop_mode=clone:stop=-1,trim=duration={total},setpts=PTS-STARTPTS{f}[v]",
        "-map", "[v]", "-an", *enc.venc, "-movflags", "+faststart", "-t", f"{total}", str(out),
    )  # fmt: skip
    return note


def measure_warning(project: Project, sec: PageSection, *, strict: bool) -> str | None:
    """The warning for a recording whose narration t=0 was not measured on it, or None.

    A cut trims the recording at the sidecar's measurement. When `measure` did not read this
    recording, the trim belongs to another take or is the recorder's wall-clock estimate, and
    every reveal in the section plays early or late. --strict refuses such a section.
    """
    webm = project.recording(sec)
    if not webm.exists():
        return None
    reason = stale_measure(webm, Sidecar.load(project.rec_dir / f"{sec.key}-scene.json"), project.root)
    if reason is None:
        return None
    if strict:
        raise MissingInputError(
            f"section {sec.number}: STALE MEASUREMENT: {reason}. Run `decktalk measure --only {sec.number}`, "
            "then `decktalk assemble` again."
        )
    message = (
        f"section {sec.key}: STALE MEASUREMENT: {reason}. Every reveal in the section may play early or late. "
        f"Run `decktalk measure --only {sec.number}`, or pass --strict to stop on this."
    )
    log.warning(message)
    return message


def stray_warnings(project: Project, command: str) -> list[str]:
    """One logged warning per NN-section.mp4 in build/out whose section is not in decktalk.toml.

    `command` names the stage that ignores the file, for the message.
    """
    messages = []
    for f in project.stray_section_videos():
        name = f.relative_to(project.root).as_posix() if f.is_relative_to(project.root) else f.as_posix()
        message = (
            f"{name} is not a section in decktalk.toml, so {command} ignores it. "
            "Delete the file if an earlier build left it."
        )
        log.warning(message)
        messages.append(message)
    return messages


def render_sections(project: Project, timeline: Timeline, *, strict: bool) -> list[RenderedSection]:
    enc = _Encoder(project.settings.video)
    project.out_dir.mkdir(parents=True, exist_ok=True)
    flags = fade_flags(project)
    targets = timeline_targets(timeline, enc.v.fps)
    dip = frame_dip(project.transition.dip_seconds, enc.v.fps)
    rows: list[RenderedSection] = []
    for sec in project.sections:
        out = project.section_video(sec)
        audio: Path | None = None
        warning: str | None = None
        if isinstance(sec, ClipSection):
            note, audio = _render_clip(project, enc, sec, out, flags[sec.key], dip, strict=strict)
        else:
            total = targets.get(sec.key, 0.0)
            if total <= 0:
                raise MissingInputError(
                    f"section {sec.key} has no span in {project.timeline_path}; run `decktalk narrate`"
                )
            total = round(total + sec.hold_seconds, 3)  # validated at load: only the last page section holds
            warning = measure_warning(project, sec, strict=strict)
            note = _render_page(project, enc, sec, out, flags[sec.key], dip, total, strict=strict)
        dur = ffmpeg.probe_duration(out)
        log.info("[cut ] %s  %s -> %s  (%.3fs)", sec.key, note, out.name, dur)
        rows.append(RenderedSection(section=sec, path=out, duration=dur, note=note, audio=audio, warning=warning))
    return rows


def section_starts(rows: list[RenderedSection]) -> dict[str, float]:
    """Where each section begins in the final file: the cumulative rendered lengths."""
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


# ---- stage 3: the mix ---------------------------------------------------------------------


@dataclass
class MixPlan:
    inputs: list[tuple[str, str]] = field(default_factory=list)  # Pairs of a mode (loop, once or lavfi) and a path.
    filter: str = ""
    total: float = 0.0
    warnings: list[str] = field(default_factory=list)


def resolve_marker_time(
    marker: dict[str, Any], starts: dict[str, float], manifest: Manifest, audio_dir: Path
) -> float | None:
    key = f"{int(marker['section']):02d}"
    if key not in starts:
        return None
    on = str(marker.get("on", "$start"))
    offset = float(marker.get("offset", 0))
    if on == "$start":
        return starts[key] + offset
    entry = manifest.segments.get(key)
    if entry is None:
        return None
    words = read_words(audio_dir / entry.words_file)
    if on == "$end":
        return starts[key] + words[-1].end + offset if words else None
    idx = find_phrase(words, on, int(marker.get("occurrence", 1)), bool(marker.get("case_sensitive", False)))
    return None if idx is None else starts[key] + words[idx].start + offset


def narration_offset(rows: list[RenderedSection], timeline: Timeline, starts: dict[str, float]) -> float:
    """Where narration t=0 sits in the final file: the start of the first page section."""
    first = next((r.section.key for r in rows if r.section.key in timeline.sections), None)
    return starts[first] if first else 0.0


@dataclass(frozen=True)
class NarrationRun:
    """Consecutive page sections with no clip between them, which play one unbroken stretch of the narration.

    `at` is where the run begins in the final file. `start` and `end` bound its stretch of
    narration.mp3, and the last run has no end, so it plays to the end of the track.
    """

    keys: tuple[str, ...]
    at: float
    start: float
    end: float | None

    @property
    def offset(self) -> float:
        """What to add to a time in narration.mp3 to place it in the final file."""
        return self.at - self.start


def narration_runs(rows: list[RenderedSection], timeline: Timeline, starts: dict[str, float]) -> list[NarrationRun]:
    """The narration split at every clip that sits between page sections.

    The track holds the spoken sections with no gaps, so a clip between two page sections
    pauses it, and the next page section resumes it on its own first frame. A project with
    no clip between page sections has one run.
    """
    groups: list[list[str]] = []
    open_run = False
    for row in rows:
        key = row.section.key
        if row.section.is_clip:
            open_run = False
        elif key in timeline.sections:
            if not open_run:
                groups.append([])
                open_run = True
            groups[-1].append(key)
    return [
        NarrationRun(
            keys=tuple(keys),
            at=starts[keys[0]],
            start=timeline.sections[keys[0]].start,
            end=None if i == len(groups) - 1 else timeline.sections[keys[-1]].end,
        )
        for i, keys in enumerate(groups)
    ]


def narration_offsets(rows: list[RenderedSection], timeline: Timeline, starts: dict[str, float]) -> dict[str, float]:
    """What to add to a time in narration.mp3 to place it in the final file, per spoken section key.

    With one run every section shares the offset of the first page section. A section in
    the timeline that has no rendered row takes the offset of the first run.
    """
    runs = narration_runs(rows, timeline, starts)
    if len(runs) <= 1:
        t0 = narration_offset(rows, timeline, starts)
        return {key: t0 for key in timeline.sections}
    offsets = {key: run.offset for run in runs for key in run.keys}
    return {key: offsets.get(key, runs[0].offset) for key in timeline.sections}


def plan_mix(project: Project, rows: list[RenderedSection], timeline: Timeline, *, nomix: bool) -> MixPlan:
    mix = project.mix
    audio: AudioConfig = project.settings.audio
    sr = project.settings.video.sample_rate
    manifest = project.manifest() or Manifest(script="", model="", output_format="")
    beats: Beats = project.beats()
    plan = MixPlan()
    starts = section_starts(rows)
    plan.total = sum(r.duration for r in rows)
    chain: list[str] = []
    labels: list[str] = []

    def add_input(mode: str, path: str) -> int:
        plan.inputs.append((mode, path))
        return len(plan.inputs)

    fmt = f"aresample={sr},aformat=channel_layouts=stereo"

    # A silent anchor of the picture's length fixes the mix duration, since the picture
    # itself carries no audio.
    idx = add_input("lavfi", f"anullsrc=r={sr}:cl=stereo")
    chain.append(f"[{idx}:a]{fmt}[anchor]")
    labels.append("[anchor]")

    # narration under the picture from the first page section
    narration = project.audio_dir / timeline.narration
    runs = narration_runs(rows, timeline, starts)
    if len(runs) <= 1:
        t0 = narration_offset(rows, timeline, starts)
        idx = add_input("once", str(narration))
        chain.append(f"[{idx}:a]{fmt},adelay={int(round(t0 * 1000))}:all=1[narr]")
        labels.append("[narr]")
    else:
        # A clip between page sections pauses the narration. Each run of page sections plays
        # its own stretch of the track, starting where its first section starts.
        for n, run in enumerate(runs):
            idx = add_input("once", str(narration))
            trim = f"atrim=start={run.start:.3f}" + ("" if run.end is None else f":end={run.end:.3f}")
            chain.append(
                f"[{idx}:a]{fmt},{trim},asetpts=PTS-STARTPTS,adelay={int(round(run.at * 1000))}:all=1[narr{n}]"
            )
            labels.append(f"[narr{n}]")
    offsets = narration_offsets(rows, timeline, starts)
    speech: list[tuple[float, float]] = [
        (offsets[key] + s.start, offsets[key] + (s.speech_end if s.speech_end is not None else s.end))
        for key, s in timeline.sections.items()
    ]
    speech += [(starts[r.section.key], starts[r.section.key] + r.duration) for r in rows if r.section.is_clip]

    # each clip's own audio, at its section start, trimmed to its picture and faded at both ends
    for row in rows:
        if row.audio is None:
            continue
        idx = add_input("once", str(row.audio))
        key = row.section.key
        fade_out_at = max(row.duration - CLIP_FADE_SECONDS, 0)
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={row.duration:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={CLIP_FADE_SECONDS},afade=t=out:st={fade_out_at:.3f}:d={CLIP_FADE_SECONDS},"
            f"adelay={int(round(starts[key] * 1000))}:all=1[clip{key}]"
        )
        labels.append(f"[clip{key}]")

    # underscore
    music = project.path(mix.underscore) if mix.underscore and not nomix else None
    if music is not None and music.exists():
        idx = add_input("loop", str(music))
        base = db(mix.underscore_db)
        duck = db(mix.underscore_duck_db)
        factors = [f"(1-{1 - duck:.5f}*{max_expr([ramp_expr(a, b, audio.duck_ramp_seconds) for a, b in speech])})"]
        if mix.markers:
            mpath = project.path(mix.markers)
            if mpath.exists():
                try:
                    mspec = json.loads(mpath.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ConfigError(f"{mpath}: {exc}") from exc
                boost = db(float(mspec.get("boost_db", 3))) - 1
                boost_len = float(mspec.get("boost_seconds", 2))
                boosts: list[str] = []
                mutes: list[str] = []
                for marker in mspec.get("markers", []):
                    mt = resolve_marker_time(marker, starts, manifest, project.audio_dir)
                    if mt is None:
                        plan.warnings.append(f"marker {marker.get('name')!r} unresolved; skipped")
                        continue
                    mute = float(marker.get("mute_seconds", 0))
                    if mute > 0:
                        mutes.append(ramp_expr(mt, mt + mute, audio.marker_mute_ramp_seconds))
                    boosts.append(ramp_expr(mt + mute, mt + mute + boost_len, audio.marker_boost_ramp_seconds))
                if boosts:
                    factors.append(f"(1+{boost:.5f}*{max_expr(boosts)})")
                if mutes:
                    factors.append(f"(1-{max_expr(mutes)})")
            else:
                plan.warnings.append(f"markers file missing ({mpath}); underscore without structure")
        vol = f"{base:.5f}*" + "*".join(factors)
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={plan.total:.3f},asetpts=PTS-STARTPTS,volume='{vol}':eval=frame,"
            f"afade=t=in:d={mix.underscore_fade_in},"
            f"afade=t=out:st={max(plan.total - mix.underscore_fade_out, 0):.3f}:d={mix.underscore_fade_out}[music]"
        )
        labels.append("[music]")
    elif mix.underscore and not nomix:
        plan.warnings.append(f"underscore missing ({project.path(mix.underscore)}); no music (`decktalk soundscape`)")

    # ambience under flagged sections
    amb = project.path(mix.ambience) if mix.ambience and not nomix else None
    flagged = [r for r in rows if isinstance(r.section, PageSection) and r.section.ambience]
    if amb is not None and amb.exists() and flagged:
        idx = add_input("loop", str(amb))
        pad = audio.ambience_pad_seconds
        spans = [
            ramp_expr(
                starts[r.section.key] - pad, starts[r.section.key] + r.duration + pad, audio.ambience_ramp_seconds
            )
            for r in flagged
        ]
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={plan.total:.3f},asetpts=PTS-STARTPTS,volume='{db(mix.ambience_db):.5f}*{max_expr(spans)}':eval=frame[amb]"
        )
        labels.append("[amb]")
    elif mix.ambience and not nomix and flagged:
        plan.warnings.append(f"ambience missing ({amb}); no ambience")

    # one-shot sfx on resolved cues
    for n, sfx in enumerate(() if nomix else mix.sfx):
        path = project.path(sfx.file)
        key = f"{sfx.section:02d}"
        cue_t = beats.get(key, sfx.cue)
        if not path.exists():
            plan.warnings.append(f"sfx {sfx.file} missing; skipped")
            continue
        if key not in starts or cue_t is None:
            plan.warnings.append(f"sfx {sfx.file}: cue {sfx.cue!r} in section {sfx.section} is unresolved; skipped")
            continue
        idx = add_input("once", str(path))
        at_ms = int(round((starts[key] + cue_t + sfx.offset) * 1000))
        chain.append(f"[{idx}:a]{fmt},volume={db(sfx.db):.5f},adelay={at_ms}:all=1[sfx{n}]")
        labels.append(f"[sfx{n}]")

    chain.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[a]")
    plan.filter = ";".join(chain)
    return plan


def mix_input_args(plan: MixPlan) -> list[str]:
    """ffmpeg input arguments for the plan, in the order its filter graph indexes them."""
    args: list[str] = []
    for mode, path in plan.inputs:
        if mode == "loop":
            args += ["-stream_loop", "-1", "-i", path]
        elif mode == "lavfi":
            args += ["-f", "lavfi", "-t", f"{plan.total:.3f}", "-i", path]
        else:
            args += ["-i", path]
    return args


# ---- stage 4: loudness ------------------------------------------------------------------------


def normalize_loudness(project: Project, src: Path, dst: Path) -> tuple[ffmpeg.Loudness, ffmpeg.Loudness]:
    """Gain to the integrated target, then a true-peak limiter at the ceiling. Returns (before, after).

    A plain gain keeps the mix's dynamics intact, and the limiter only touches peaks that
    would cross the ceiling. It runs oversampled so inter-sample peaks are caught, which
    is what a true-peak ceiling promises.
    """
    ln = project.mix.loudnorm
    enc = _Encoder(project.settings.video)
    before = ffmpeg.measure_loudness(src, i=ln.i, tp=ln.tp, lra=ln.lra)
    gain = ln.i - before.i
    ceiling = db(ln.tp - LIMITER_HEADROOM_DB)
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        "-af",
        f"volume={gain:.2f}dB,aresample={LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false,aresample={enc.v.sample_rate}",
        *enc.aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    after = ffmpeg.measure_loudness(dst, i=ln.i, tp=ln.tp, lra=ln.lra)
    return before, after


def loudness_problems(project: Project, after: ffmpeg.Loudness) -> list[str]:
    """What is wrong with the normalized result, if anything: a peak over the ceiling or a missed target."""
    ln = project.mix.loudnorm
    problems: list[str] = []
    if after.tp > ln.tp:
        problems.append(f"true peak {after.tp:.1f} dBTP is above the {ln.tp:.1f} dBTP ceiling")
    if abs(after.i - ln.i) > LOUDNESS_TOLERANCE_LU:
        problems.append(
            f"integrated loudness {after.i:.1f} LUFS is {abs(after.i - ln.i):.1f} LU from the {ln.i:.1f} LUFS target"
        )
    return problems


# ---- stage 5: captions and chapters --------------------------------------------------------


def output_paths(project: Project) -> dict[str, Path]:
    """The files assemble writes next to the final mp4, keyed final, srt, vtt and chapters."""
    out, name = project.out_dir, project.name
    return {
        "final": project.final,
        "srt": out / f"{name}.srt",
        "vtt": out / f"{name}.vtt",
        "chapters": out / f"{name}.chapters.txt",
    }


def build_captions(
    timeline: Timeline, t0: float | Mapping[str, float], texts: dict[str, str] | None = None
) -> list[CaptionCue]:
    """Cues for every spoken section, shifted to where its narration sits in the final file.

    `t0` is where narration t=0 sits in the final file. It may instead map each section key
    to its own offset, as `narration_offsets` gives when a clip between page sections
    pauses the narration. `texts` maps a section key to its spoken script text, which lends
    the captions their punctuation and case.
    """
    cues: list[CaptionCue] = []
    for key in timeline.keys:
        shift = t0.get(key, 0.0) if isinstance(t0, Mapping) else t0
        words = [Word(w.word, round(shift + w.start, 3), round(shift + w.end, 3)) for w in timeline.sections[key].words]
        if texts and key in texts:
            words = display_words(words, texts[key])
        cues += caption_cues(words)
    return cues


def clip_captions(project: Project, rows: list[RenderedSection]) -> list[CaptionCue]:
    """Cues for the speech inside each clip section that names a words file.

    The words count from the clip's start and carry their own punctuation. A word that starts
    after the clip's picture ends is dropped. A slate, or a clip with no audio, gets no captions.
    """
    starts = section_starts(rows)
    cues: list[CaptionCue] = []
    for r in rows:
        sec = r.section
        if not isinstance(sec, ClipSection) or not sec.words or r.audio is None:
            continue
        path = project.path(sec.words)
        if not path.exists():
            log.warning("section %s: words file missing: %s; the clip plays without captions", sec.key, sec.words)
            continue
        shift = starts[sec.key]
        words = [
            Word(w.word, round(shift + w.start, 3), round(shift + min(w.end, r.duration), 3))
            for w in read_words(path)
            if w.start < r.duration
        ]
        cues += caption_cues(words)
    return cues


def caption_texts(project: Project, timeline: Timeline) -> dict[str, str]:
    """The spoken text per section key, which lends the captions their punctuation and case.

    The manifest records the text each section was narrated from, so the captions match
    the audio even when the script has been edited since. A manifest written before that
    field existed has empty entries, and those sections fall back to the script as it is now.
    """
    manifest = project.manifest()
    texts = {k: seg.spoken for k, seg in (manifest.segments.items() if manifest else ()) if seg.spoken}
    if any(key not in texts for key in timeline.keys):
        from .narrate import script_segments

        for seg in script_segments(project)[0]:
            texts.setdefault(seg.key, seg.spoken)
    return texts


def build_chapters(rows: list[RenderedSection]) -> list[Chapter]:
    """One chapter per section, where consecutive sections with the same title share one chapter."""
    starts = section_starts(rows)
    chapters: list[Chapter] = []
    for r in rows:
        start = starts[r.section.key]
        title = r.section.title or f"Section {r.section.number}"
        if chapters and chapters[-1].title == title:
            chapters[-1] = Chapter(start=chapters[-1].start, end=start + r.duration, title=title)
        else:
            chapters.append(Chapter(start=start, end=start + r.duration, title=title))
    return chapters


def mux_chapters(src: Path, chapters: Path, dst: Path) -> None:
    """Copy both streams into dst with the chapter markers from an ffmetadata file."""
    ffmpeg.run(
        "-i", str(src), "-f", "ffmetadata", "-i", str(chapters),
        "-map", "0:v", "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
        "-c", "copy", "-movflags", "+faststart", str(dst),
    )  # fmt: skip


# ---- entry ------------------------------------------------------------------------------------


@dataclass
class AssembleResult:
    final: Path
    stamped: Path
    duration: float
    sections: list[RenderedSection]
    warnings: list[str]
    loudness: tuple[ffmpeg.Loudness, ffmpeg.Loudness] | None
    captions_srt: Path | None = None
    captions_vtt: Path | None = None
    chapters: Path | None = None


def assemble(project: Project, *, nomix: bool = False, loudnorm: bool = True, strict: bool = False) -> AssembleResult:
    timeline = project.timeline()
    if timeline is None:
        raise MissingInputError(f"{project.timeline_path} not found; run `decktalk narrate` first")
    out_dir = project.out_dir
    paths = output_paths(project)
    strays = stray_warnings(project, "assemble")
    rows = render_sections(project, timeline, strict=strict)

    picture = out_dir / ".picture.mp4"
    log.info("[cat ] %d sections, %s", len(rows), cut_summary(project))
    concat([r.path for r in rows], picture)

    work = out_dir / f".{project.name}.tmp.mp4"
    work.unlink(missing_ok=True)
    plan = plan_mix(project, rows, timeline, nomix=nomix)
    warnings = strays + [r.warning for r in rows if r.warning] + plan.warnings
    for w in plan.warnings:
        log.warning(w)
    enc = _Encoder(project.settings.video)
    log.info("[mix ] %d audio input(s) -> %s", len(plan.inputs), project.final.name)
    try:
        ffmpeg.run(
            "-i", str(picture), *mix_input_args(plan),
            "-filter_complex", plan.filter,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", *enc.aenc, "-movflags", "+faststart", str(work),
        )  # fmt: skip
    finally:
        picture.unlink(missing_ok=True)

    loudness = None
    if loudnorm and timeline.estimated:
        # A silent build carries clicks and silence, and normalizing them would move the clicks
        # the a/v check listens for, so the pass is skipped and the result has no loudness.
        log.info(
            "[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, "
            "and the clicks stay at -24 dBFS for the a/v check"
        )
    elif loudnorm:
        raw = out_dir / ".premix-loudness.mp4"
        work.rename(raw)
        try:
            loudness = normalize_loudness(project, raw, work)
        finally:
            raw.unlink(missing_ok=True)
        b, a = loudness
        ln = project.mix.loudnorm
        log.info(
            "[loud] I %.1f -> %.1f LUFS (target %.1f), TP %.1f -> %.1f dBTP (ceiling %.1f), LRA %.1f -> %.1f LU",
            b.i, a.i, ln.i, b.tp, a.tp, ln.tp, b.lra, a.lra,
        )  # fmt: skip
        problems = loudness_problems(project, a)
        for p in problems:
            log.warning("[loud] %s", p)
        warnings += problems
        if problems and strict:
            raise ToolError("loudness: " + ", ".join(problems))

    starts = section_starts(rows)
    cues = build_captions(timeline, narration_offsets(rows, timeline, starts), caption_texts(project, timeline))
    cues = sorted(cues + clip_captions(project, rows), key=lambda c: c.start)
    chapters = build_chapters(rows)
    write_srt(paths["srt"], cues)
    write_vtt(paths["vtt"], cues)
    write_chapters(paths["chapters"], chapters)
    log.info("[caps] %d cue(s) -> %s, %s", len(cues), paths["srt"].name, paths["vtt"].name)
    chaptered = out_dir / f".{project.name}.chapters.mp4"
    mux_chapters(work, paths["chapters"], chaptered)
    chaptered.replace(work)
    log.info("[chap] %d chapter(s) -> %s", len(chapters), paths["chapters"].name)

    if not work.exists() or work.stat().st_size == 0:
        raise ToolError("render produced no output")
    stamped = out_dir / f"{project.name}-{time.strftime('%Y%m%d-%H%M')}.mp4"
    work.replace(project.final)  # atomic: a viewer never opens a half-written file
    shutil.copyfile(project.final, stamped)
    duration = ffmpeg.probe_duration(project.final)
    log.info("done: %s  (%.2fs)  copy: %s", project.final, duration, stamped.name)
    return AssembleResult(
        final=project.final,
        stamped=stamped,
        duration=duration,
        sections=rows,
        warnings=warnings,
        loudness=loudness,
        captions_srt=paths["srt"],
        captions_vtt=paths["vtt"],
        chapters=paths["chapters"],
    )
