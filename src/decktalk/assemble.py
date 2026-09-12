"""Build the final mp4 from recordings, narration, clips and the soundscape (ffmpeg).

Timeline mode, the only mode: the narration is one continuous track and the visuals
are cut to it.

1. Every narrated section becomes a silent NN-section.mp4 cut to its exact span in
   build/audio/timeline.json, rounded on cumulative 30 fps frame boundaries so the
   picture never drifts; the recorder lead-in is trimmed off the head and the last
   frame is cloned to fill. Video sections (your own clips) keep their audio; a
   missing clip becomes a slate for slate_seconds. A missing recording becomes black.
2. The sections are concatenated with no gaps (concat demuxer, stream copy).
3. The narration is laid under the picture from the end of the last clip before the
   first narrated section. Optional beds: an underscore (ducked under speech, shaped
   by media/markers.json), an ambience bed under sections flagged ambience: true, and
   one-shot sfx on resolved cues.
4. Two-pass EBU R128 loudness normalisation with linear gain.
5. Atomic publish: work file -> mv to build/out/<name>.mp4, plus a timestamped copy.

scenes.json knobs: dips ([from, to] boundaries that get a dip-to-black; absent = every
cut), transition.dip_seconds, transition.page_fades_in, hold_seconds (last narrated
section only), mix.* (see the template).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .beats import find_phrase, load_words, parse_beats_string
from .project import Project, Section
from .tools import ff, ff_stderr, ffprobe_duration, has_audio, log, warn

W, H, FPS = 1920, 1080, 30
VF_FIT = (
    f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS},format=yuv420p"
)
AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]


def venc(preset: str, crf: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(FPS)]


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


def timeline_targets(timeline: dict[str, Any]) -> dict[str, float]:
    """Frame-exact video length per spoken section, from cumulative frame boundaries."""
    targets: dict[str, float] = {}
    for key, sec in timeline.get("sections", {}).items():
        start_f = round(float(sec["start"]) * FPS)
        end_f = round(float(sec["end"]) * FPS)
        targets[key] = (end_f - start_f) / FPS
    return targets


def fade_flags(project: Project) -> dict[str, tuple[bool, bool]]:
    """(fade_in, fade_out) per section key from the dips config."""
    sections = project.sections
    dips = project.dips
    pairs = {(f"{a:02d}", f"{b:02d}") for a, b in dips} if dips is not None else None
    page_fades_in = bool(project.transition.get("page_fades_in", False))
    flags: dict[str, tuple[bool, bool]] = {}
    for i, sec in enumerate(sections):
        prev_key = sections[i - 1].key if i > 0 else None
        next_key = sections[i + 1].key if i + 1 < len(sections) else None
        if pairs is None:
            dip_in, dip_out = prev_key is not None, next_key is not None
        else:
            dip_in = prev_key is not None and (prev_key, sec.key) in pairs
            dip_out = next_key is not None and (sec.key, next_key) in pairs
        fade_in = dip_in and not (not sec.is_video and page_fades_in)
        flags[sec.key] = (fade_in, dip_out)
    return flags


def vfades(total: float, fade_in: bool, fade_out: bool, dip: float) -> str:
    out = ""
    if fade_in:
        out += f",fade=t=in:st=0:d={dip}"
    if fade_out:
        out += f",fade=t=out:st={max(total - dip, 0):.3f}:d={dip}"
    return out


def resolve_marker_time(
    marker: dict[str, Any], starts: dict[str, float], manifest: dict[str, Any], audio_dir: Path
) -> float | None:
    key = f"{int(marker['section']):02d}"
    if key not in starts:
        return None
    on = str(marker.get("on", "$start"))
    offset = float(marker.get("offset", 0))
    if on == "$start":
        return starts[key] + offset
    entry = manifest.get(key)
    if not entry:
        return None
    words = load_words(audio_dir, entry)
    if on == "$end":
        return starts[key] + float(words[-1]["end"]) + offset if words else None
    idx = find_phrase(words, on, int(marker.get("occurrence", 1)), bool(marker.get("case_sensitive", False)))
    return None if idx is None else starts[key] + float(words[idx]["start"]) + offset


def section_slate(project: Project, sec: Section) -> Path | None:
    """A titled slate PNG for a section whose clip is missing (rendered once, cached in build/out)."""
    out = project.out_dir / "slates" / f"{sec.key}-slate.png"
    if out.exists():
        return out
    try:
        from .slate import render_slate

        return render_slate(
            out,
            title=sec.title or f"Section {sec.index}",
            sub="Your clip goes here",
            eyebrow=f"section {sec.index} · slate",
            foot=f"drop it at {sec.data['video']} and run `decktalk assemble`",
        )
    except Exception as exc:  # Chromium unavailable: fall back to a plain frame
        warn(f"could not render a slate ({exc}); using a plain frame")
        return None


# ---- stages --------------------------------------------------------------------


def render_sections(
    project: Project,
    timeline: dict[str, Any],
    *,
    preset: str,
    crf: int,
    strict: bool,
) -> list[tuple[Section, Path, float]]:
    """Encode NN-section.mp4 per section. Returns (section, file, duration)."""
    out_dir = project.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    dip = float(project.transition.get("dip_seconds", 0.15))
    flags = fade_flags(project)
    targets = timeline_targets(timeline)
    tts_keys = sorted(timeline.get("sections", {}))
    last_tts = tts_keys[-1] if tts_keys else None
    slate_png = project.mix.get("slate")
    slate = project.path(slate_png) if slate_png else None
    rows: list[tuple[Section, Path, float]] = []

    for sec in project.sections:
        out = out_dir / f"{sec.key}-section.mp4"
        fade_in, fade_out = flags[sec.key]
        if sec.is_video:
            clip = project.path(sec.data["video"])
            if clip.exists():
                total = ffprobe_duration(clip)
                fades = vfades(total, fade_in, fade_out, dip)
                log(f"[clip] {sec.key}  {clip.name} (own audio) -> {out.name}")
                if has_audio(clip):
                    ff(
                        "-i",
                        str(clip),
                        "-filter_complex",
                        f"[0:v]{VF_FIT}{fades}[v];[0:a]aresample=44100,aformat=channel_layouts=stereo[a]",
                        "-map",
                        "[v]",
                        "-map",
                        "[a]",
                        *venc(preset, crf),
                        *AENC,
                        "-movflags",
                        "+faststart",
                        str(out),
                    )
                else:
                    warn(f"{clip} has no audio track; muxing silence")
                    ff(
                        "-i",
                        str(clip),
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=44100:cl=stereo",
                        "-filter_complex",
                        f"[0:v]{VF_FIT}{fades}[v]",
                        "-map",
                        "[v]",
                        "-map",
                        "1:a",
                        "-shortest",
                        *venc(preset, crf),
                        *AENC,
                        "-movflags",
                        "+faststart",
                        str(out),
                    )
            else:
                if strict:
                    raise SystemExit(f"error: clip missing: {clip}")
                secs = sec.slate_seconds
                warn(f"{sec.data['video']} missing; slate for {secs}s (drop your clip at that path)")
                png = slate if slate and slate.exists() else section_slate(project, sec)
                if png:
                    vin = ["-loop", "1", "-framerate", str(FPS), "-t", f"{secs}", "-i", str(png)]
                else:
                    vin = ["-f", "lavfi", "-t", f"{secs}", "-i", f"color=c=0x0e1116:s={W}x{H}:r={FPS}"]
                fades = vfades(secs, fade_in, fade_out, dip)
                ff(
                    *vin,
                    "-f",
                    "lavfi",
                    "-t",
                    f"{secs}",
                    "-i",
                    "anullsrc=r=44100:cl=stereo",
                    "-filter_complex",
                    f"[0:v]{VF_FIT}{fades}[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "1:a",
                    "-t",
                    f"{secs}",
                    *venc(preset, crf),
                    *AENC,
                    "-movflags",
                    "+faststart",
                    str(out),
                )
        else:
            total = targets.get(sec.key, 0.0)
            if total <= 0:
                raise SystemExit(f"error: section {sec.key} has no span in {project.timeline} (run `decktalk narrate`)")
            if sec.hold_seconds > 0:
                if sec.key != last_tts:
                    raise SystemExit(
                        f"error: hold_seconds on section {sec.key}: only the last narrated section "
                        f"({last_tts}) may hold; the narration is continuous, so holding earlier "
                        "would push every later visual off its words"
                    )
                total = round(total + sec.hold_seconds, 3)
            webm = project.rec_dir / f"{sec.key}-scene.webm"
            vlead = ""
            if webm.exists():
                vin = ["-i", str(webm)]
                note = webm.name
                sidecar = webm.with_suffix(".json")
                if sidecar.exists():
                    side = json.loads(sidecar.read_text())
                    lead = side.get("lead_in_seconds", side.get("lead_seconds", 0))
                    vlead = f"trim=start={lead},setpts=PTS-STARTPTS,"
                    note += f" (lead {lead}s trimmed)"
            else:
                if strict:
                    raise SystemExit(f"error: recording missing: {webm}")
                warn(f"{webm.name} missing; using black")
                vin = ["-f", "lavfi", "-t", f"{total}", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
                note = "black"
            log(f"[cut ] {sec.key}  {note} -> {out.name}  ({total}s)")
            fades = vfades(total, fade_in, fade_out, dip)
            ff(
                *vin,
                "-f",
                "lavfi",
                "-t",
                f"{total}",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-filter_complex",
                f"[0:v]{vlead}{VF_FIT},tpad=stop_mode=clone:stop=-1,trim=duration={total},setpts=PTS-STARTPTS{fades}[v]",
                "-map",
                "[v]",
                "-map",
                "1:a",
                *venc(preset, crf),
                *AENC,
                "-movflags",
                "+faststart",
                "-t",
                f"{total}",
                str(out),
            )
        rows.append((sec, out, ffprobe_duration(out)))
    return rows


def concat(files: list[Path], out: Path) -> None:
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out))
    lst.unlink()


def plan_mix(
    project: Project,
    rows: list[tuple[Section, Path, float]],
    timeline: dict[str, Any],
    *,
    nomix: bool,
) -> tuple[list[tuple[str, str]], str, float]:
    """(inputs [(loop|once, path)], filter_complex, total). Narration is always laid in."""
    mix = project.mix
    manifest = project.manifest_data().get("segments", {})
    beats = project.beats_data()
    starts: dict[str, float] = {}
    t = 0.0
    for sec, _f, dur in rows:
        starts[sec.key] = t
        t += dur
    total = t

    inputs: list[tuple[str, str]] = []
    chain: list[str] = []
    labels: list[str] = ["[pic]"]

    def add_input(mode: str, path: str) -> int:
        inputs.append((mode, path))
        return len(inputs)

    # narration under the picture from the first narrated section
    narration = project.audio_dir / timeline.get("narration", "narration.mp3")
    first_tts = next((sec.key for sec, _f, _d in rows if sec.key in timeline["sections"]), None)
    t0 = starts[first_tts] if first_tts else 0.0
    idx = add_input("once", str(narration))
    chain.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={int(round(t0 * 1000))}:all=1[narr]")
    labels.append("[narr]")
    speech_spans: list[tuple[float, float]] = []
    for sec_tl in timeline["sections"].values():
        end = float(sec_tl.get("speech_end") or sec_tl["end"])
        speech_spans.append((t0 + float(sec_tl["start"]), t0 + end))
    for sec, _f, dur in rows:
        if sec.is_video:
            speech_spans.append((starts[sec.key], starts[sec.key] + dur))

    # underscore
    music_rel = None if nomix else mix.get("underscore")
    music = project.path(music_rel) if music_rel else None
    if music and music.exists():
        idx = add_input("loop", str(music))
        base = db(float(mix.get("underscore_db", -24)))
        duck = db(float(mix.get("underscore_duck_db", -6)))
        fi = float(mix.get("underscore_fade_in", 2))
        fo = float(mix.get("underscore_fade_out", 3))
        speech = [ramp_expr(a, b, 0.5) for a, b in speech_spans]
        factors = [f"(1-{1 - duck:.5f}*{max_expr(speech)})"]
        markers_rel = mix.get("markers")
        if markers_rel:
            mpath = project.path(markers_rel)
            if mpath.exists():
                mspec = json.loads(mpath.read_text())
                boost = db(float(mspec.get("boost_db", 3))) - 1
                boost_len = float(mspec.get("boost_seconds", 2))
                boosts: list[str] = []
                mutes: list[str] = []
                for marker in mspec.get("markers", []):
                    mt = resolve_marker_time(marker, starts, manifest, project.audio_dir)
                    if mt is None:
                        warn(f"marker {marker.get('name')} unresolved; skipped")
                        continue
                    mute = float(marker.get("mute_seconds", 0))
                    if mute > 0:
                        mutes.append(ramp_expr(mt, mt + mute, 0.04))
                    boosts.append(ramp_expr(mt + mute, mt + mute + boost_len, 0.3))
                if boosts:
                    factors.append(f"(1+{boost:.5f}*{max_expr(boosts)})")
                if mutes:
                    factors.append(f"(1-{max_expr(mutes)})")
            else:
                warn(f"markers file missing ({mpath}); underscore without structure")
        vol = f"{base:.5f}*" + "*".join(factors)
        chain.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
            f"volume='{vol}':eval=frame,afade=t=in:d={fi},afade=t=out:st={max(total - fo, 0):.3f}:d={fo}[music]"
        )
        labels.append("[music]")
    elif music_rel:
        warn(f"underscore missing ({music}); no music (`decktalk soundscape`)")

    # ambience bed under flagged sections
    amb_rel = None if nomix else (mix.get("ambience") or mix.get("crowd"))
    amb = project.path(amb_rel) if amb_rel else None
    if amb and amb.exists():
        spans = [ramp_expr(starts[s.key] - 0.5, starts[s.key] + d + 0.5, 1.0) for s, _f, d in rows if s.ambience]
        if spans:
            idx = add_input("loop", str(amb))
            gain = db(float(mix.get("ambience_db", mix.get("crowd_db", -20))))
            chain.append(
                f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
                f"volume='{gain:.5f}*{max_expr(spans)}':eval=frame[amb]"
            )
            labels.append("[amb]")
    elif amb_rel:
        warn(f"ambience missing ({amb}); no ambience")

    # one-shot sfx on cues
    for n, sfx in enumerate([] if nomix else mix.get("sfx", [])):
        path = project.path(sfx["file"])
        key = f"{int(sfx['section']):02d}"
        if not path.exists() or key not in starts:
            continue
        cue_t = parse_beats_string(beats.get(key, "")).get(sfx["step"])
        if cue_t is None:
            continue  # cue not resolved this render; silent skip by design
        idx = add_input("once", str(path))
        at_ms = int(round((starts[key] + cue_t + float(sfx.get("offset", 0))) * 1000))
        chain.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"volume={db(float(sfx.get('db', -16))):.5f},adelay={at_ms}:all=1[sfx{n}]"
        )
        labels.append(f"[sfx{n}]")

    chain.insert(0, "[0:a]aresample=44100,aformat=channel_layouts=stereo[pic]")
    chain.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95[a]")
    return inputs, ";".join(chain), total


def loudnorm_field(name: str, text: str) -> str:
    import re

    m = re.search(rf'"{name}"\s*:\s*"([-0-9.]+)"', text)
    return m.group(1) if m else "0"


def loudnorm(project: Project, src: Path, dst: Path) -> None:
    cfg = project.mix.get("loudnorm", {})
    i, tp, lra = cfg.get("I", -16), cfg.get("TP", -1.5), cfg.get("LRA", 11)
    base = f"loudnorm=I={i}:TP={tp}:LRA={lra}"

    def measure(path: Path) -> str:
        return ff_stderr("-i", str(path), "-map", "0:a", "-af", f"{base}:print_format=json", "-f", "null", "-")

    meas = measure(src)
    fields = {k: loudnorm_field(k, meas) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    log(
        f"[loud] before: I={fields['input_i']} LUFS  TP={fields['input_tp']} dBTP  "
        f"LRA={fields['input_lra']} LU -> target I={i} TP={tp} LRA={lra}"
    )
    ff(
        "-i",
        str(src),
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:v",
        "copy",
        "-af",
        f"{base}:measured_I={fields['input_i']}:measured_TP={fields['input_tp']}:measured_LRA={fields['input_lra']}"
        f":measured_thresh={fields['input_thresh']}:offset={fields['target_offset']}:linear=true",
        *AENC,
        "-movflags",
        "+faststart",
        str(dst),
    )
    after = measure(dst)
    log(
        f"[loud] after:  I={loudnorm_field('input_i', after)} LUFS  TP={loudnorm_field('input_tp', after)} dBTP  "
        f"LRA={loudnorm_field('input_lra', after)} LU"
    )


def assemble(
    project: Project,
    *,
    nomix: bool = False,
    no_loudnorm: bool = False,
    strict: bool = False,
    preset: str | None = None,
    crf: int | None = None,
) -> int:
    timeline = project.timeline_data()
    if not timeline:
        raise SystemExit(f"error: {project.timeline} not found; run `decktalk narrate` first")
    preset = preset or os.environ.get("DECKTALK_PRESET", "medium")
    crf = crf if crf is not None else int(os.environ.get("DECKTALK_CRF", "18"))
    out_dir = project.out_dir
    rows = render_sections(project, timeline, preset=preset, crf=crf, strict=strict)

    picture = out_dir / ".picture.mp4"
    log(f"[cat ] {len(rows)} sections, straight cuts")
    concat([f for _s, f, _d in rows], picture)

    work = out_dir / f".{project.name}.tmp.mp4"
    work.unlink(missing_ok=True)
    inputs, filt, _total = plan_mix(project, rows, timeline, nomix=nomix)
    args: list[str] = ["-i", str(picture)]
    for mode, path in inputs:
        args += ["-stream_loop", "-1", "-i", path] if mode == "loop" else ["-i", path]
    log(
        f"[mix ] {len(inputs)} audio input(s): narration"
        f"{'' if nomix else ' + beds/sfx where present'} -> {project.final.name}"
    )
    ff(
        *args,
        "-filter_complex",
        filt,
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        *AENC,
        "-movflags",
        "+faststart",
        str(work),
    )
    picture.unlink()

    if not no_loudnorm:
        raw = out_dir / ".premix-loudness.mp4"
        work.rename(raw)
        loudnorm(project, raw, work)
        raw.unlink()

    if not work.exists() or work.stat().st_size == 0:
        raise SystemExit("error: render produced no output")
    stamped = out_dir / f"{project.name}-{time.strftime('%Y%m%d-%H%M')}.mp4"
    work.replace(project.final)
    shutil.copyfile(project.final, stamped)
    log(f"done: {project.final}  ({ffprobe_duration(project.final)}s)  copy: {stamped.name}")
    return 0
