"""Turn the narration script into one ElevenLabs mp3 per section, with word timestamps.

The script is markdown with "## N. Title" sections. Bracketed directions such as
[Deck. The curve draws.] are not spoken; markdown formatting is stripped; ALL-CAPS
placeholders like [NUMBER] refuse a real run. Sections that scenes.json maps to a
pre-recorded clip ({"video": ...}) are skipped.

Each section is synthesized with /with-timestamps (so every word has a start and
end), cached by a hash of the model, voice, settings and text, padded so speech ends
at least 0.35 s before the file ends, then all sections are concatenated with no gaps
into build/audio/narration.mp3. build/audio/timeline.json records each section's
absolute start/end and every word at absolute time; the recorder and the assembler
cut the visuals to it.

--silent needs no API key: silent placeholders sized at 150 wpm plus the declared
breaks, with evenly spaced estimated words, so the whole pipeline can be exercised
offline (smoke tests, layout passes).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import Project
from .tools import ff, ff_stderr, ffprobe_duration, fmt_mmss

WORDS_PER_MINUTE = 140
SILENT_WORDS_PER_MINUTE = 150
API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"
MODELS = ["eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5"]
OUTPUT_FORMAT = "mp3_44100_128"
BREAK = '<break time="0.7s" />'  # opens the first section, and follows a direction
TAIL_BREAK = '<break time="0.35s" />'  # sections butt together in the continuous track
MIN_TAIL_SECONDS = 0.35
DEFAULT_VOICE_SETTINGS: dict[str, Any] = {
    "stability": 0.55,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 1.0,
}
DIRECTION_MARK = "\x00DIR\x00"

# "## 3. The demo — 1:40 to 3:40"  (the dash and time range are optional)
SECTION_RE = re.compile(
    r"^##\s+(?P<num>\d+)\.\s+(?P<title>.+?)"
    r"(?:\s+[—–-]+\s+(?P<start>\d+:\d{2})\s+to\s+(?P<end>\d+:\d{2}))?\s*$"
)
DIRECTION_RE = re.compile(r"\[(?![A-Z][A-Z0-9_]*\])[^\]]*\]")
PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_]*)\]")


@dataclass
class Segment:
    index: int
    title: str
    slug: str
    start: str | None
    end: str | None
    text: str
    lead_break: bool = False

    @property
    def key(self) -> str:
        return f"{self.index:02d}"

    @property
    def spoken(self) -> str:
        return re.sub(r"\s*<break[^>]*/>\s*", " ", self.text).strip()

    @property
    def tts_text(self) -> str:
        head = f"{BREAK} " if self.lead_break else ""
        return f"{head}{self.text} {TAIL_BREAK}"

    @property
    def words(self) -> int:
        return len(self.spoken.split())

    @property
    def est_seconds(self) -> float:
        return round(self.words / WORDS_PER_MINUTE * 60, 1)

    @property
    def break_seconds(self) -> float:
        return sum(float(t) for t in re.findall(r'<break time="([0-9.]+)s"', self.tts_text))

    @property
    def silent_seconds(self) -> float:
        return round(self.words / SILENT_WORDS_PER_MINUTE * 60 + self.break_seconds, 3)

    @property
    def target_seconds(self) -> float | None:
        if self.start and self.end:
            return _mmss(self.end) - _mmss(self.start)
        return None

    @property
    def placeholders(self) -> list[str]:
        return sorted(set(PLACEHOLDER_RE.findall(self.text)))

    @property
    def filename(self) -> str:
        return f"{self.key}-{self.slug}.mp3"

    @property
    def words_filename(self) -> str:
        return f"{self.key}-{self.slug}.words.json"


def _mmss(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links, before directions
    text = DIRECTION_RE.sub(f"\n\n{DIRECTION_MARK}\n\n", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    out: list[str] = []
    pending_break = False
    for p in paragraphs:
        if p == DIRECTION_MARK:
            pending_break = bool(out)
            continue
        if pending_break:
            out[-1] = f"{out[-1]} {BREAK}"
            pending_break = False
        out.append(p)
    return "\n\n".join(out)


def parse_script(markdown: str) -> list[Segment]:
    segments: list[Segment] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        segments.append(
            Segment(
                index=int(current["num"]),
                title=current["title"].strip(),
                slug=slugify(current["title"]),
                start=current["start"],
                end=current["end"],
                text=strip_markdown("\n".join(body)),
            )
        )

    for line in markdown.splitlines():
        match = SECTION_RE.match(line)
        if match:
            flush()
            current = match.groupdict()
            body = []
            continue
        if line.startswith("## ") or line.startswith("# ") or (line.strip() == "---"):
            flush()
            current = None
            body = []
            continue
        if current is not None:
            body.append(line)
    flush()
    return segments


def load_segments(project: Project) -> tuple[list[Segment], list[Segment]]:
    """(all sections in the script, the spoken ones in order with lead_break set)."""
    if not project.script.exists():
        sys.exit(f"error: script not found: {project.script}")
    all_segments = parse_script(project.script.read_text())
    if not all_segments:
        sys.exit(f"error: no '## N. Title' sections found in {project.script}")
    skipped = project.video_section_indexes
    spoken = [s for s in all_segments if s.index not in skipped]
    for i, seg in enumerate(spoken):
        seg.lead_break = i == 0
    return all_segments, spoken


# ---- audio helpers -------------------------------------------------------------


def write_silence(out_path: Path, seconds: float) -> None:
    ff(
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-t",
        f"{seconds:.3f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(out_path),
    )


def trailing_silence(path: Path, noise_db: int = -35) -> float:
    duration = ffprobe_duration(path)
    err = ff_stderr("-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d=0.05", "-f", "null", "-")
    starts = re.findall(r"silence_start: ([0-9.]+)", err)
    ends = re.findall(r"silence_end: ([0-9.]+)", err)
    if not starts:
        return 0.0
    if len(ends) < len(starts) or float(ends[-1]) >= duration - 0.05:
        return round(duration - float(starts[-1]), 3)
    return 0.0


def ensure_tail(path: Path, min_tail: float = MIN_TAIL_SECONDS) -> float:
    """Pad with silence so speech ends at least min_tail before the file ends. Returns seconds added."""
    tail = trailing_silence(path)
    if tail >= min_tail:
        return 0.0
    add = round(min_tail - tail + 0.05, 3)
    tmp = path.with_suffix(".pad.mp3")
    ff("-i", str(path), "-af", f"apad=pad_dur={add}", "-c:a", "libmp3lame", "-b:a", "128k", str(tmp))
    tmp.replace(path)
    return add


# ---- ElevenLabs ------------------------------------------------------------------


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    words: list[dict] = []
    current: list[tuple[str, float, float]] = []
    in_tag = False

    def flush() -> None:
        if not current:
            return
        raw = "".join(c for c, _, _ in current)
        clean = raw.strip("\"'“”‘’.,;:!?()[]—–-…")
        if clean:
            words.append({"word": clean, "start": round(current[0][1], 3), "end": round(current[-1][2], 3)})
        current.clear()

    for ch, start, end in zip(chars, starts, ends, strict=False):
        if in_tag:
            if ch == ">":
                in_tag = False
            continue
        if ch == "<":
            flush()
            in_tag = True
            continue
        if ch.isspace():
            flush()
            continue
        current.append((ch, start, end))
    flush()
    return words


def synthesize(
    text: str,
    api_key: str,
    voice_id: str,
    model: str,
    voice_settings: dict[str, Any],
    out_path: Path,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> list[dict]:
    url = f"{API_BASE}/text-to-speech/{voice_id}/with-timestamps?output_format={OUTPUT_FORMAT}"
    payload: dict[str, Any] = {"text": text, "model_id": model, "voice_settings": voice_settings}
    if previous_text:
        payload["previous_text"] = previous_text[-1500:]
    if next_text:
        payload["next_text"] = next_text[:1500]
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            reply = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        sys.exit(f"error: ElevenLabs HTTP {exc.code} for {out_path.name}: {detail}")
    out_path.write_bytes(base64.b64decode(reply["audio_base64"]))
    alignment = reply.get("alignment") or reply.get("normalized_alignment") or {}
    words = words_from_alignment(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    )
    out_path.with_name(out_path.stem + ".words.json").write_text(json.dumps(words, indent=1) + "\n")
    return words


def estimated_words(segment: Segment, duration: float) -> list[dict]:
    """Evenly spaced words for --silent runs, so cues resolve to plausible times."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    lead = 0.7 if segment.lead_break else 0.0
    span = max(0.1, duration - lead - MIN_TAIL_SECONDS)
    per = span / len(tokens)
    return [
        {
            "word": t.strip("\"'“”‘’.,;:!?()[]—–-…"),
            "start": round(lead + i * per, 3),
            "end": round(lead + (i + 1) * per - 0.02, 3),
        }
        for i, t in enumerate(tokens)
    ]


def text_hash(segment: Segment, model: str, voice_id: str, settings: dict[str, Any]) -> str:
    payload = (
        f"{model}\n{voice_id}\n{OUTPUT_FORMAT}\n{json.dumps(settings, sort_keys=True)}\n{segment.tts_text}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ---- timeline ------------------------------------------------------------------


def build_timeline(project: Project, manifest: dict, order: list[Segment]) -> dict:
    out_dir = project.audio_dir
    keys = [s.key for s in order if s.key in manifest["segments"]]
    files = [out_dir / manifest["segments"][k]["file"] for k in keys]
    narration = out_dir / "narration.mp3"
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    labels = "".join(f"[{i}:a]" for i in range(len(files)))
    ff(
        *inputs,
        "-filter_complex",
        f"{labels}concat=n={len(files)}:v=0:a=1[a]",
        "-map",
        "[a]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        str(narration),
    )
    t = 0.0
    sections: dict[str, dict] = {}
    for k, f in zip(keys, files, strict=True):
        dur = ffprobe_duration(f)
        words_file = out_dir / manifest["segments"][k].get("words_file", "")
        words = json.loads(words_file.read_text()) if words_file.is_file() else []
        sections[k] = {
            "title": manifest["segments"][k]["title"],
            "start": round(t, 3),
            "end": round(t + dur, 3),
            "duration": round(dur, 3),
            "speech_end": round(t + words[-1]["end"], 3) if words else None,
            "words": [
                {"word": w["word"], "start": round(t + w["start"], 3), "end": round(t + w["end"], 3)} for w in words
            ],
        }
        t += dur
    timeline = {
        "narration": narration.name,
        "estimated": bool(manifest.get("estimated")),
        "total_seconds": round(ffprobe_duration(narration), 3),
        "sections": sections,
    }
    project.timeline.write_text(json.dumps(timeline, indent=1) + "\n")
    return timeline


def print_table(segments: list[Segment], manifest: dict | None = None) -> None:
    header = f"{'#':>2}  {'section':<22} {'words':>5}  {'est@140':>7}  {'target':>6}  {'actual':>6}  placeholders"
    print(header)
    print("-" * len(header))
    total_words = 0
    total_est = 0.0
    total_actual = 0.0
    for seg in segments:
        total_words += seg.words
        total_est += seg.est_seconds
        actual = None
        if manifest:
            entry = manifest.get("segments", {}).get(seg.key)
            if entry:
                actual = entry.get("duration_seconds")
                total_actual += actual or 0
        ph = ",".join(seg.placeholders) if seg.placeholders else "-"
        print(
            f"{seg.index:>2}  {seg.slug[:22]:<22} {seg.words:>5}  {fmt_mmss(seg.est_seconds):>7}  "
            f"{fmt_mmss(seg.target_seconds):>6}  {fmt_mmss(actual):>6}  {ph}"
        )
    print("-" * len(header))
    print(
        f"{'':>2}  {'total':<22} {total_words:>5}  {fmt_mmss(total_est):>7}  "
        f"{fmt_mmss(sum(s.target_seconds or 0 for s in segments)):>6}  "
        f"{fmt_mmss(total_actual) if total_actual else '  --  ':>6}"
    )


def print_timeline(timeline: dict) -> None:
    print(f"\n{'#':>3}  {'section':<22} {'start':>7} {'end':>7} {'length':>7}")
    for key, sec in timeline["sections"].items():
        print(
            f"{int(key):>3}  {sec['title'][:22]:<22} {fmt_mmss(sec['start']):>7} "
            f"{fmt_mmss(sec['end']):>7} {sec['duration']:>7.1f}"
        )
    est = "  (estimated: silent placeholders)" if timeline.get("estimated") else ""
    print(f"     narration total {fmt_mmss(timeline['total_seconds'])}{est}")


# ---- entry ---------------------------------------------------------------------


def narrate(
    project: Project,
    *,
    only: list[int] | None = None,
    force: bool = False,
    allow_placeholders: bool = False,
    dry_run: bool = False,
    silent: bool = False,
    model: str = DEFAULT_MODEL,
) -> int:
    all_segments, spoken = load_segments(project)
    skipped = sorted(project.video_section_indexes)
    if skipped:
        print(f"skipping video sections (no TTS): {skipped}")
    voice_settings = dict(DEFAULT_VOICE_SETTINGS)
    voice_settings.update(project.data.get("voice_settings") or {})
    model = str(project.data.get("model") or model)
    targets = [s for s in spoken if not only or s.index in set(only)]

    if dry_run:
        for seg in targets:
            print(f"=== {seg.key} {seg.title}  ({seg.start or '?'} to {seg.end or '?'})  -> {seg.filename}")
            print(seg.tts_text)
            print()
        print(f"voice settings: model={model} {json.dumps(voice_settings)}  (+ previous_text/next_text)")
        print_table(targets)
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled:
            print(f"\nnote: unfilled placeholders {unfilled}; fill them before the real run.")
        return 0

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "script": str(project.script.relative_to(project.root)),
        "model": "silent-placeholder" if silent else model,
        "output_format": OUTPUT_FORMAT,
        "segments": {},
    }
    if silent:
        manifest["estimated"] = True
        manifest["estimate_basis"] = f"{SILENT_WORDS_PER_MINUTE} wpm + declared breaks"
    previous = project.manifest_data()
    if previous.get("estimated") == manifest.get("estimated", False):
        manifest["segments"] = previous.get("segments", {})

    api_key = voice_id = ""
    if not silent:
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled and not allow_placeholders:
            sys.exit(f"error: unfilled placeholders {unfilled} in script; fill them or pass --allow-placeholders")
        api_key, voice_id = project.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")

    by_index = {s.index: s for s in all_segments}
    order = [s.index for s in all_segments]
    for seg in targets:
        out_path = project.audio_dir / seg.filename
        words_path = project.audio_dir / seg.words_filename
        if silent:
            write_silence(out_path, seg.silent_seconds)
            duration = ffprobe_duration(out_path)
            words = estimated_words(seg, duration)
            words_path.write_text(json.dumps(words, indent=1) + "\n")
            print(f"[sil ] {seg.filename}  {seg.words} words -> {duration}s (estimated words)")
            manifest["segments"][seg.key] = {
                "index": seg.index,
                "title": seg.title,
                "file": seg.filename,
                "words_file": seg.words_filename,
                "hash": "silent",
                "words": seg.words,
                "est_seconds": seg.est_seconds,
                "target_seconds": seg.target_seconds,
                "duration_seconds": duration,
                "speech_end_seconds": words[-1]["end"] if words else None,
            }
            continue
        digest = text_hash(seg, model, voice_id, voice_settings)
        cached = manifest["segments"].get(seg.key)
        if (
            not force
            and cached
            and cached.get("hash") == digest
            and out_path.exists()
            and cached.get("file") == seg.filename
            and words_path.exists()
        ):
            added = ensure_tail(out_path)
            if added:
                cached["duration_seconds"] = ffprobe_duration(out_path)
                cached["tail_padded_seconds"] = round(cached.get("tail_padded_seconds", 0) + added, 3)
                print(f"[skip] {seg.filename}  unchanged; tail padded +{added}s -> {cached['duration_seconds']}s")
            else:
                print(f"[skip] {seg.filename}  unchanged ({cached['duration_seconds']}s)")
            continue
        pos = order.index(seg.index)
        prev_seg = by_index[order[pos - 1]] if pos > 0 else None
        next_seg = by_index[order[pos + 1]] if pos + 1 < len(order) else None
        print(f"[tts ] {seg.filename}  {seg.words} words, est {seg.est_seconds}s ...", end="", flush=True)
        words = synthesize(
            seg.tts_text,
            api_key,
            voice_id,
            model,
            voice_settings,
            out_path,
            previous_text=prev_seg.spoken if prev_seg else None,
            next_text=next_seg.spoken if next_seg else None,
        )
        added = ensure_tail(out_path)
        duration = ffprobe_duration(out_path)
        speech_end = words[-1]["end"] if words else None
        note = f", tail padded +{added}s" if added else ""
        print(f" {duration}s ({len(words)} words, speech ends {speech_end}s{note})")
        manifest["segments"][seg.key] = {
            "index": seg.index,
            "title": seg.title,
            "file": seg.filename,
            "words_file": seg.words_filename,
            "speech_end_seconds": speech_end,
            "tail_padded_seconds": added,
            "hash": digest,
            "words": seg.words,
            "est_seconds": seg.est_seconds,
            "target_seconds": seg.target_seconds,
            "duration_seconds": duration,
        }
        project.manifest.write_text(json.dumps(manifest, indent=2) + "\n")

    # Drop stale entries for sections no longer in the script, keep order.
    valid = {s.key for s in spoken}
    manifest["segments"] = {k: v for k, v in sorted(manifest["segments"].items()) if k in valid}
    manifest["total_seconds"] = round(sum(s["duration_seconds"] for s in manifest["segments"].values()), 3)
    project.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    missing = [s.key for s in spoken if s.key not in manifest["segments"]]
    if missing:
        print(f"\nnote: sections {missing} have no narration yet; timeline covers the rest")
    timeline = build_timeline(project, manifest, spoken)
    print()
    print_table(targets, manifest)
    print_timeline(timeline)
    print(f"\nmanifest: {project.manifest}")
    return 0
