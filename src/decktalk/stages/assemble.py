"""Stage 5: recordings, narration, clips and the soundscape become the final mp4 (ffmpeg).

1. Every section becomes a video-only sections/NN.mp4. A page section is cut to its exact
   span in the timeline, rounded on cumulative frame boundaries so the picture never
   drifts. The recorder lead-in is trimmed off the head and the last frame is cloned to
   fill. A missing clip becomes a titled slate. A missing recording becomes black. No
   intermediate carries audio, so the concatenation cannot reintroduce AAC priming and
   the picture starts at pts 0 like the sound does.
2. The sections are concatenated with no gaps (concat demuxer, stream copy).
3. The whole soundtrack is one mix over a silent anchor of the picture's length: the
   narration from the first page section, each clip's own audio delayed to its section
   start with a 20 ms fade at both ends, and the optional beds. A clip between page
   sections pauses the narration, so the track is split into runs of consecutive page
   sections and each run starts where its first section starts. The music is ducked
   under speech and shaped by markers.json, an ambience bed sits under sections flagged
   ambience, and one-shot sfx land on resolved cues.
4. EBU R128 loudness: pass one measures integrated loudness and true peak, pass two
   applies the gain that reaches the target and a true-peak limiter at the ceiling,
   oversampled at 192 kHz. The result is measured again and reported.
5. Captions (srt and vtt) come from the word timestamps, plus the words file of any clip
   section that names one, and chapter markers from the section chapters are muxed into the
   mp4. Consecutive sections with the same chapter share one chapter marker.
6. Atomic publish: work file, then one rename to build/out/<name>.mp4, plus a
   timestamped copy when [output] timestamped_copy is on.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import CueTimes, RecordingLog, Takes, Timeline, Word, read_words
from ..captions import CaptionCue, Chapter, caption_cues, display_words, write_chapters, write_srt, write_vtt
from ..errors import MissingInputError, ToolError
from ..jsonio import as_json, relative
from ..media import audio, ffmpeg
from ..media.browser import render_slate
from ..media.encode import Encoder
from ..model import ClipSection, PageSection, Project, Section
from ..model.cues import find_phrase
from ..model.document import frame_dip
from ..model.markers import Marker
from ..settings import AudioConfig
from ..verdicts import Findings

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


def vfades(total: float, fade_in: bool, fade_out: bool, dip: float) -> str:
    out = ""
    if fade_in:
        out += f",fade=t=in:st=0:d={dip}"
    if fade_out:
        out += f",fade=t=out:st={max(total - dip, 0):.3f}:d={dip}"
    return out


# ---- stage 1: sections -----------------------------------------------------------------


@dataclass
class RenderedSection:
    section: Section
    path: Path
    duration: float
    note: str
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


def _render_clip(
    project: Project,
    enc: Encoder,
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
    enc: Encoder,
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
        recording_log = RecordingLog.load(project.recording_log(sec))
        if recording_log is not None:
            vlead = f"trim=start={recording_log.trim_seconds},setpts=PTS-STARTPTS,"
            note += f" (t0 {recording_log.trim_seconds}s trimmed)"
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


def stray_warnings(project: Project, command: str) -> list[str]:
    """Warn about every leftover section video, and return what was said."""
    messages = project.stray_section_warnings(command)
    for message in messages:
        log.warning(message)
    return messages


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
            note, audio = _render_clip(project, enc, sec, out, flags[sec.key], dip, strict=strict)
        else:
            total = targets.get(sec.key, 0.0)
            if total <= 0:
                raise MissingInputError(
                    f"section {sec.key} has no span in {project.timeline_path}; run `decktalk narrate`"
                )
            total = round(total + sec.hold_seconds, 3)  # the narration pauses for the hold, as it does for a clip
            note = _render_page(project, enc, sec, out, flags[sec.key], dip, total, strict=strict)
        dur = ffmpeg.probe_duration(out)
        log.info("[cut ] %s  %s -> %s  (%.3fs)", sec.key, note, out.name, dur)
        rows.append(RenderedSection(section=sec, path=out, duration=dur, note=note, audio=audio))
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
    marker: Marker,
    starts: dict[str, float],
    takes: Takes,
    takes_dir: Path,
    leads: Mapping[str, float] | None = None,
) -> float | None:
    """Where a marker falls in the final file, or None when its phrase is not in the narration.

    `starts` gives each section's start in the final file and `leads` each section's lead_seconds,
    which the marker's words already sit after.
    """
    if marker.key not in starts:
        return None
    if marker.on == "$start":
        return starts[marker.key] + marker.offset
    entry = takes.sections.get(marker.key)
    if entry is None:
        return None
    lead = (leads or {}).get(marker.key, 0.0)
    words = [Word(w.word, w.start + lead, w.end + lead) for w in read_words(takes_dir / entry.words_file)]
    if marker.on == "$end":
        return starts[marker.key] + words[-1].end + marker.offset if words else None
    idx = find_phrase(words, marker.on, marker.occurrence, marker.case_sensitive)
    return None if idx is None else starts[marker.key] + words[idx].start + marker.offset


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
    """The narration split at every clip that sits between page sections, and after every held page section.

    The track holds the spoken sections with no gaps, so a clip between two page sections
    pauses it, and the next page section resumes it on its own first frame. A page section's
    hold_seconds pauses it the same way. A project with no clip or hold between page sections
    has one run.
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
            if isinstance(row.section, PageSection) and row.section.hold_seconds > 0:
                open_run = False
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


def plan_mix(project: Project, rows: list[RenderedSection], timeline: Timeline, *, soundscape: bool) -> MixPlan:
    mix = project.mix
    audio: AudioConfig = project.settings.audio
    sr = project.settings.video.sample_rate
    takes = project.takes() or Takes(script="", model="", output_format="")
    cue_times: CueTimes = project.cue_times()
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
    narration = project.narration_dir / timeline.narration
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

    # music
    music = project.path(mix.music) if mix.music and soundscape else None
    if music is not None and music.exists():
        idx = add_input("loop", str(music))
        base = db(mix.music_db)
        duck = db(mix.music_duck_db)
        factors = [f"(1-{1 - duck:.5f}*{max_expr([ramp_expr(a, b, audio.duck_ramp_seconds) for a, b in speech])})"]
        if mix.music_markers:
            mpath = project.path(mix.music_markers)
            spec = project.markers()
            if spec is None:
                plan.warnings.append(f"markers file missing ({mpath}); music without structure")
            else:
                boost = db(spec.boost_db) - 1
                boosts: list[str] = []
                mutes: list[str] = []
                leads = {r.section.key: project.lead_seconds(r.section.key) for r in rows}
                for marker in spec.markers:
                    mt = resolve_marker_time(marker, starts, takes, project.takes_dir, leads)
                    if mt is None:
                        plan.warnings.append(f"marker {marker.name!r} unresolved; skipped")
                        continue
                    if marker.mute_seconds > 0:
                        mutes.append(ramp_expr(mt, mt + marker.mute_seconds, audio.marker_mute_ramp_seconds))
                    swell_at = mt + marker.mute_seconds
                    boosts.append(ramp_expr(swell_at, swell_at + spec.boost_seconds, audio.marker_boost_ramp_seconds))
                if boosts:
                    factors.append(f"(1+{boost:.5f}*{max_expr(boosts)})")
                if mutes:
                    factors.append(f"(1-{max_expr(mutes)})")
        vol = f"{base:.5f}*" + "*".join(factors)
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={plan.total:.3f},asetpts=PTS-STARTPTS,volume='{vol}':eval=frame,"
            f"afade=t=in:d={mix.music_fade_in_seconds},"
            f"afade=t=out:st={max(plan.total - mix.music_fade_out_seconds, 0):.3f}"
            f":d={mix.music_fade_out_seconds}[music]"
        )
        labels.append("[music]")
    elif mix.music and soundscape:
        plan.warnings.append(f"music missing ({project.path(mix.music)}); no music (`decktalk soundscape`)")

    # ambience under flagged sections
    amb = project.path(mix.ambience) if mix.ambience and soundscape else None
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
    elif mix.ambience and soundscape and flagged:
        plan.warnings.append(f"ambience missing ({amb}); no ambience")

    # one-shot sfx on resolved cues
    for n, sfx in enumerate(mix.sfx if soundscape else ()):
        path = project.path(sfx.file)
        key = f"{sfx.section:02d}"
        cue_t = cue_times.get(key, sfx.cue)
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


def normalize_loudness(project: Project, src: Path, dst: Path) -> tuple[audio.Loudness, audio.Loudness]:
    """Gain to the integrated target, then a true-peak limiter at the ceiling. Returns (before, after).

    A plain gain keeps the mix's dynamics intact, and the limiter only touches peaks that
    would cross the ceiling. It runs oversampled so inter-sample peaks are caught, which
    is what a true-peak ceiling promises.
    """
    ln = project.mix.loudness
    enc = Encoder(project.settings.video)
    before = audio.measure_loudness(src, i=ln.target_lufs, tp=ln.true_peak_db, lra=ln.range_lu)
    gain = ln.target_lufs - before.i
    ceiling = db(ln.true_peak_db - LIMITER_HEADROOM_DB)
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        "-af",
        f"volume={gain:.2f}dB,aresample={LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false,aresample={enc.v.sample_rate}",
        *enc.aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    after = audio.measure_loudness(dst, i=ln.target_lufs, tp=ln.true_peak_db, lra=ln.range_lu)
    return before, after


def loudness_problems(project: Project, after: audio.Loudness) -> list[str]:
    """What is wrong with the normalized result, if anything: a peak over the ceiling or a missed target."""
    ln = project.mix.loudness
    problems: list[str] = []
    if after.tp > ln.true_peak_db:
        problems.append(f"true peak {after.tp:.1f} dBTP is above the {ln.true_peak_db:.1f} dBTP ceiling")
    if abs(after.i - ln.target_lufs) > LOUDNESS_TOLERANCE_LU:
        problems.append(
            f"integrated loudness {after.i:.1f} LUFS is {abs(after.i - ln.target_lufs):.1f} LU "
            f"from the {ln.target_lufs:.1f} LUFS target"
        )
    return problems


# ---- stage 5: captions and chapters --------------------------------------------------------


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

    The take index records the text each section was narrated from, so the captions match
    the audio even when the script has been edited since. A take index written before that
    field existed has empty entries, and those sections fall back to the script as it is now.
    """
    takes = project.takes()
    texts = {k: seg.spoken for k, seg in (takes.sections.items() if takes else ()) if seg.spoken}
    if any(key not in texts for key in timeline.keys):
        for seg in project.script_sections()[0]:
            texts.setdefault(seg.key, seg.spoken)
    return texts


def build_chapters(rows: list[RenderedSection], titles: dict[int, str]) -> list[Chapter]:
    """One chapter per section, where consecutive sections with the same chapter share one chapter marker."""
    starts = section_starts(rows)
    chapters: list[Chapter] = []
    for r in rows:
        start = starts[r.section.key]
        title = titles[r.section.number]
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
    """The finished video and everything written beside it."""

    final: Path
    stamped: Path | None  # The timestamped copy, when [output] timestamped_copy is on.
    duration: float
    sections: list[RenderedSection]
    warnings: list[str]
    loudness: tuple[audio.Loudness, audio.Loudness] | None
    captions_srt: Path | None = None
    captions_vtt: Path | None = None
    chapters: Path | None = None
    loudness_problems: list[str] = field(default_factory=list)  # A peak over the ceiling, or a missed target.

    @property
    def findings(self) -> Findings:
        """Uncertain: a loudness result that missed its target or crossed its ceiling."""
        return Findings(uncertain=len(self.loudness_problems))

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
                    "duration": round(row.duration, 3),
                    "path": relative(row.path, root),
                }
                for row in self.sections
            ],
            "captions": {
                "srt": None if self.captions_srt is None else relative(self.captions_srt, root),
                "vtt": None if self.captions_vtt is None else relative(self.captions_vtt, root),
                "chapters": None if self.chapters is None else relative(self.chapters, root),
            },
            "loudness": None
            if after is None or before is None
            else {"before": as_json(before), "after": as_json(after), "problems": list(self.loudness_problems)},
            "warnings": list(self.warnings),
        }


def assemble(
    project: Project, *, soundscape: bool = True, loudness: bool = True, strict: bool = False
) -> AssembleResult:
    timeline = project.timeline()
    if timeline is None:
        raise MissingInputError(f"{project.timeline_path} not found; run `decktalk narrate` first")
    out_dir = project.out_dir
    paths = project.workspace.output_paths()
    strays = stray_warnings(project, "assemble")
    rows = render_sections(project, timeline, strict=strict)

    picture = out_dir / ".picture.mp4"
    log.info("[cat ] %d sections, %s", len(rows), project.document.cut_summary)
    concat([r.path for r in rows], picture)

    work = out_dir / f".{project.name}.tmp.mp4"
    work.unlink(missing_ok=True)
    plan = plan_mix(project, rows, timeline, soundscape=soundscape)
    warnings = strays + plan.warnings
    for w in plan.warnings:
        log.warning(w)
    enc = Encoder(project.settings.video)
    log.info("[mix ] %d audio input(s) -> %s", len(plan.inputs), project.final.name)
    try:
        ffmpeg.run(
            "-i", str(picture), *mix_input_args(plan),
            "-filter_complex", plan.filter,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", *enc.aenc, "-movflags", "+faststart", str(work),
        )  # fmt: skip
    finally:
        picture.unlink(missing_ok=True)

    measured = None
    problems: list[str] = []
    if loudness and timeline.estimated:
        # A build without voice carries clicks and silence, and normalizing them would move the clicks
        # the a/v check listens for, so the pass is skipped and the result has no loudness.
        log.info(
            "[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, "
            "and the clicks stay at -24 dBFS for the a/v check"
        )
    elif loudness:
        raw = out_dir / ".premix-loudness.mp4"
        work.rename(raw)
        try:
            measured = normalize_loudness(project, raw, work)
        finally:
            raw.unlink(missing_ok=True)
        b, a = measured
        ln = project.mix.loudness
        log.info(
            "[loud] I %.1f -> %.1f LUFS (target %.1f), TP %.1f -> %.1f dBTP (ceiling %.1f), LRA %.1f -> %.1f LU",
            b.i, a.i, ln.target_lufs, b.tp, a.tp, ln.true_peak_db, b.lra, a.lra,
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
    chapters = build_chapters(rows, project.chapters())
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
    work.replace(project.final)  # atomic: a viewer never opens a half-written file
    stamped = None
    if project.settings.output.timestamped_copy:
        stamped = out_dir / f"{project.name}-{time.strftime('%Y%m%d-%H%M')}.mp4"
        shutil.copyfile(project.final, stamped)
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
        captions_srt=paths["srt"],
        captions_vtt=paths["vtt"],
        chapters=paths["chapters"],
    )
