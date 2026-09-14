"""Stage 1: the script becomes one mp3 per section with word timestamps, plus the continuous track.

The script is markdown with "## N. Title" sections. Bracketed directions such as
[Deck. The curve draws.] or [beat] are not spoken and become a short pause, and a
[pause N] direction becomes a pause of N seconds instead. Markdown formatting is
stripped, and ALL-CAPS placeholders like [NUMBER] refuse a real run. Sections that the
project maps to a clip are skipped.

Each section is synthesized with word timestamps, cached by a hash of model, voice,
settings and text, padded so speech ends at least min_tail_seconds before the file
ends, then every section is concatenated with no gaps into build/audio/narration.mp3.
build/audio/timeline.json records each section's absolute start and end and every
word at absolute time; the recorder and the assembler cut the visuals to it.

silent=True needs no API key: silent placeholders sized at silent_words_per_minute
plus the declared pauses, with evenly spaced estimated words, so the whole pipeline
runs offline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import Manifest, ManifestSegment, Timeline, TimelineSection, Word, read_words, write_words
from ..config import NarrationConfig
from ..errors import ConfigError, MissingInputError
from ..media import ffmpeg
from ..project import Project
from ..providers import elevenlabs as _elevenlabs  # noqa: F401  (registers the default provider)
from ..providers.speech import SpeechProvider, SpeechRequest, get_provider

log = logging.getLogger(__name__)

DIRECTION_MARK = "\x00DIR\x00"
# "## 3. The demo — 1:40 to 3:40"  (the dash and time range are optional)
SECTION_RE = re.compile(
    r"^##\s+(?P<num>\d+)\.\s+(?P<title>.+?)(?:\s+[—–-]+\s+(?P<start>\d+:\d{2})\s+to\s+(?P<end>\d+:\d{2}))?\s*$"
)
DIRECTION_RE = re.compile(r"\[(?![A-Z][A-Z0-9_]*\])[^\]]*\]")
# "[pause 3]" or "[pause 2.5]": a timed pause, in seconds, in place of the default direction pause.
PAUSE_RE = re.compile(r"\[\s*pause\s+(?P<seconds>\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_]*)\]")
BREAK_RE = re.compile(r'<break time="([0-9.]+)s"\s*/>')
PUNCT = "\"'“”‘’.,;:!?()[]—–-…"


def break_tag(seconds: float) -> str:
    return f'<break time="{seconds:g}s" />'


@dataclass
class Segment:
    """One "## N. Title" section of the script, cleaned for speech."""

    index: int
    title: str
    slug: str
    text: str  # prose with direction pauses as <break/> tags
    start: str | None = None
    end: str | None = None
    lead_break: bool = False

    @property
    def key(self) -> str:
        return f"{self.index:02d}"

    @property
    def spoken(self) -> str:
        """The words the voice says, without break tags or the dashes that mark a beat."""
        text = re.sub(r"\s*<break[^>]*/>\s*", " ", self.text)
        text = re.sub(r"\s+—(?=\s|$)", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @property
    def word_count(self) -> int:
        return len(self.spoken.split())

    @property
    def placeholders(self) -> list[str]:
        return sorted(set(PLACEHOLDER_RE.findall(self.text)))

    @property
    def filename(self) -> str:
        return f"{self.key}-{self.slug}.mp3"

    @property
    def words_filename(self) -> str:
        return f"{self.key}-{self.slug}.words.json"

    @property
    def target_seconds(self) -> float | None:
        if self.start and self.end:
            return _mmss(self.end) - _mmss(self.start)
        return None

    def tts_text(self, cfg: NarrationConfig) -> str:
        """The text sent to the voice. Silence before the first section and after every last
        word is added to the audio afterwards rather than requested with break tags."""
        return self.text

    def est_seconds(self, cfg: NarrationConfig) -> float:
        return round(self.word_count / cfg.words_per_minute * 60, 1)

    def silent_seconds(self, cfg: NarrationConfig) -> float:
        breaks = sum(float(t) for t in BREAK_RE.findall(self.text))
        beats = self.text.count(" —") * cfg.direction_break_seconds
        lead = cfg.lead_break_seconds if self.lead_break else 0.0
        return round(
            self.word_count / cfg.silent_words_per_minute * 60 + breaks + beats + lead + cfg.min_tail_seconds, 3
        )


def _mmss(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def strip_markdown(text: str, *, direction_break_seconds: float) -> str:
    """Prose ready for speech. Every direction becomes a break tag appended to the paragraph before it."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links, before directions
    # A timed pause carries its own length through the direction marker, and every other
    # direction carries the default.
    text = PAUSE_RE.sub(lambda m: f"\n\n{DIRECTION_MARK}{float(m.group('seconds')):g}\n\n", text)
    text = DIRECTION_RE.sub(f"\n\n{DIRECTION_MARK}beat\n\n", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    out: list[str] = []
    pending: float | None = None  # the pause owed to the paragraph before the next one
    for p in paragraphs:
        if p.startswith(DIRECTION_MARK):
            # A direction before any prose has nothing to pause after, and back-to-back
            # directions keep the longest pause rather than stacking. A plain direction
            # is a beat, which the voice reads as a dash, and only a timed pause becomes
            # a break tag, because break tags unsettle the voice when they are frequent.
            value = p.removeprefix(DIRECTION_MARK)
            seconds = 0.0 if value == "beat" else float(value)
            if out:
                pending = seconds if pending is None else max(pending, seconds)
            continue
        if pending is not None:
            out[-1] = f"{out[-1]} {break_tag(pending)}" if pending > 0 else f"{out[-1]} —"
            pending = None
        out.append(p)
    return "\n\n".join(out)


def parse_script(markdown: str, *, direction_break_seconds: float = 0.7) -> list[Segment]:
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
                text=strip_markdown("\n".join(body), direction_break_seconds=direction_break_seconds),
                start=current["start"],
                end=current["end"],
            )
        )

    for line in markdown.splitlines():
        match = SECTION_RE.match(line)
        if match:
            flush()
            current = match.groupdict()
            body = []
            continue
        if line.startswith("## ") or line.startswith("# ") or line.strip() == "---":
            flush()
            current = None
            body = []
            continue
        if current is not None:
            body.append(line)
    flush()
    return segments


def script_segments(project: Project) -> tuple[list[Segment], list[Segment]]:
    """(every section in the script, the spoken ones in order with lead_break set)."""
    if not project.script.exists():
        raise MissingInputError(f"script not found: {project.script}")
    cfg = project.settings.narration
    all_segments = parse_script(
        project.script.read_text(encoding="utf-8"), direction_break_seconds=cfg.direction_break_seconds
    )
    if not all_segments:
        raise ConfigError(f"no '## N. Title' sections found in {project.script}")
    declared = {s.number for s in project.sections}
    undeclared = [s.index for s in all_segments if s.index not in declared]
    if undeclared:
        raise ConfigError(f"script sections {undeclared} have no [[section]] in decktalk.toml")
    spoken = [s for s in all_segments if s.index not in project.clip_numbers]
    for i, seg in enumerate(spoken):
        seg.lead_break = i == 0
    return all_segments, spoken


# ---- audio ---------------------------------------------------------------------------


def ensure_tail(path: Path, cfg: NarrationConfig) -> float:
    """Pad with silence so speech ends at least min_tail_seconds before the file ends. Returns seconds added."""
    tail = ffmpeg.trailing_silence(path)
    if tail >= cfg.min_tail_seconds:
        return 0.0
    add = round(cfg.min_tail_seconds - tail + cfg.tail_slack_seconds, 3)
    ffmpeg.pad_tail(path, add, bitrate=cfg.mp3_bitrate)
    return add


def estimated_words(segment: Segment, duration: float, cfg: NarrationConfig) -> list[Word]:
    """Evenly spaced words for silent runs, so cues resolve to plausible times."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    lead = cfg.lead_break_seconds if segment.lead_break else 0.0
    span = max(0.1, duration - lead - cfg.min_tail_seconds)
    per = span / len(tokens)
    return [
        Word(word=t.strip(PUNCT), start=round(lead + i * per, 3), end=round(lead + (i + 1) * per - 0.02, 3))
        for i, t in enumerate(tokens)
    ]


def text_hash(segment: Segment, cfg: NarrationConfig, provider_key: str, settings: dict[str, Any]) -> str:
    """Cache key for one section: provider identity, voice settings and the exact text sent."""
    payload = f"{provider_key}\n{json.dumps(settings, sort_keys=True)}\n{segment.tts_text(cfg)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_timeline(project: Project, manifest: Manifest, order: list[Segment]) -> Timeline:
    cfg = project.settings.narration
    keys = [s.key for s in order if s.key in manifest.segments]
    files = [project.audio_dir / manifest.segments[k].file for k in keys]
    narration = project.audio_dir / "narration.mp3"
    ffmpeg.concat_audio(files, narration, bitrate=cfg.mp3_bitrate, sample_rate=project.settings.video.sample_rate)
    t = 0.0
    sections: dict[str, TimelineSection] = {}
    for k, f in zip(keys, files, strict=True):
        dur = ffmpeg.decoded_duration(f, sample_rate=project.settings.video.sample_rate)
        words = read_words(project.audio_dir / manifest.segments[k].words_file)
        sections[k] = TimelineSection(
            title=manifest.segments[k].title,
            start=round(t, 3),
            end=round(t + dur, 3),
            duration=round(dur, 3),
            speech_end=round(t + words[-1].end, 3) if words else None,
            words=[Word(w.word, round(t + w.start, 3), round(t + w.end, 3)) for w in words],
        )
        t += dur
    timeline = Timeline(
        narration=narration.name,
        estimated=manifest.estimated,
        total_seconds=ffmpeg.decoded_duration(narration, sample_rate=project.settings.video.sample_rate),
        sections=sections,
    )
    timeline.save(project.timeline_path)
    return timeline


# ---- entry -----------------------------------------------------------------------------


@dataclass
class NarrateResult:
    manifest: Manifest
    timeline: Timeline
    segments: list[Segment]  # the sections this run considered
    synthesized: list[str]  # keys that hit the API (or were regenerated silently)
    cached: list[str]


def narrate(
    project: Project,
    *,
    only: list[int] | None = None,
    force: bool = False,
    allow_placeholders: bool = False,
    silent: bool = False,
    model: str | None = None,
) -> NarrateResult:
    cfg = project.settings.narration
    all_segments, spoken = script_segments(project)
    model = model or project.voice.model or cfg.model
    voice_settings = project.voice.api_settings()
    targets = [s for s in spoken if not only or s.index in set(only)]
    if not targets:
        raise ConfigError(f"no spoken sections match {only}; spoken sections are {[s.index for s in spoken]}")
    if project.clip_numbers:
        log.info("skipping clip sections (no narration): %s", sorted(project.clip_numbers))

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        script=str(project.script.relative_to(project.root))
        if project.script.is_relative_to(project.root)
        else str(project.script),
        model="silent-placeholder" if silent else model,
        output_format=cfg.output_format,
        estimated=silent,
        estimate_basis=f"{cfg.silent_words_per_minute} wpm + declared pauses" if silent else "",
    )
    previous = project.manifest()
    if previous is not None and previous.estimated == silent:
        manifest.segments = dict(previous.segments)

    provider: SpeechProvider | None = None
    if not silent:
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled and not allow_placeholders:
            raise ConfigError(f"unfilled placeholders {unfilled} in the script; fill them or pass allow_placeholders")
        provider = get_provider(project)

    by_index = {s.index: s for s in all_segments}
    order = [s.index for s in all_segments]
    synthesized: list[str] = []
    cached: list[str] = []
    for seg in targets:
        out_path = project.audio_dir / seg.filename
        words_path = project.audio_dir / seg.words_filename
        if silent:
            duration = seg.silent_seconds(cfg)
            words = estimated_words(seg, duration, cfg)
            ffmpeg.write_clicks(
                out_path,
                duration,
                [w.start for w in words],
                sample_rate=project.settings.video.sample_rate,
                bitrate=cfg.mp3_bitrate,
            )
            duration = ffmpeg.probe_duration(out_path)
            write_words(words_path, words)
            log.info("[sil ] %s  %d words -> %.2fs (estimated words)", seg.filename, seg.word_count, duration)
            manifest.segments[seg.key] = ManifestSegment(
                index=seg.index,
                title=seg.title,
                file=seg.filename,
                words_file=seg.words_filename,
                hash="silent",
                words=seg.word_count,
                est_seconds=seg.est_seconds(cfg),
                duration_seconds=duration,
                target_seconds=seg.target_seconds,
                speech_end_seconds=words[-1].end if words else None,
                spoken=seg.spoken,
            )
            synthesized.append(seg.key)
            continue
        assert provider is not None
        request = SpeechRequest(
            text=seg.tts_text(cfg),
            model=model,
            voice_settings=voice_settings,
            output_format=cfg.output_format,
        )
        digest = text_hash(seg, cfg, provider.cache_key(request), voice_settings)
        entry = manifest.segments.get(seg.key)
        if (
            not force
            and entry is not None
            and entry.hash == digest
            and entry.file == seg.filename
            and out_path.exists()
            and words_path.exists()
        ):
            # min_tail_seconds is not part of the hash, so a cached take made under a shorter
            # tail is padded here. ensure_tail measures the silence first, so a take that
            # already has enough is left untouched.
            added = ensure_tail(out_path, cfg)
            if added:
                entry.duration_seconds = ffmpeg.probe_duration(out_path)
                entry.tail_padded_seconds = round((entry.tail_padded_seconds or 0.0) + added, 3)
                log.info("[skip] %s  unchanged, tail +%ss (%.2fs)", seg.filename, added, entry.duration_seconds)
            else:
                log.info("[skip] %s  unchanged (%.2fs)", seg.filename, entry.duration_seconds)
            # A manifest written before the spoken text was recorded gains it here, since the
            # text is part of the hash and so cannot have changed.
            entry.spoken = seg.spoken
            cached.append(seg.key)
            continue
        pos = order.index(seg.index)
        prev_seg = by_index[order[pos - 1]] if pos > 0 else None
        next_seg = by_index[order[pos + 1]] if pos + 1 < len(order) else None
        log.info("[tts ] %s  %d words, est %.1fs ...", seg.filename, seg.word_count, seg.est_seconds(cfg))
        request = SpeechRequest(
            text=request.text,
            model=model,
            voice_settings=voice_settings,
            output_format=cfg.output_format,
            previous_text=prev_seg.spoken if prev_seg else None,
            next_text=next_seg.spoken if next_seg else None,
        )
        audio, words = provider.speak(request)
        out_path.write_bytes(audio)
        if seg.lead_break and cfg.lead_break_seconds > 0:
            ffmpeg.pad_head(out_path, cfg.lead_break_seconds, bitrate=cfg.mp3_bitrate)
            words = [
                Word(w.word, round(w.start + cfg.lead_break_seconds, 3), round(w.end + cfg.lead_break_seconds, 3))
                for w in words
            ]
        write_words(words_path, words)
        added = ensure_tail(out_path, cfg)
        duration = ffmpeg.probe_duration(out_path)
        speech_end = words[-1].end if words else None
        log.info(
            "       %.2fs (%d words, speech ends %s%s)",
            duration,
            len(words),
            speech_end,
            f", tail +{added}s" if added else "",
        )
        manifest.segments[seg.key] = ManifestSegment(
            index=seg.index,
            title=seg.title,
            file=seg.filename,
            words_file=seg.words_filename,
            hash=digest,
            words=seg.word_count,
            est_seconds=seg.est_seconds(cfg),
            duration_seconds=duration,
            target_seconds=seg.target_seconds,
            speech_end_seconds=speech_end,
            tail_padded_seconds=added,
            spoken=seg.spoken,
        )
        manifest.save(project.manifest_path)  # checkpoint after every paid call
        synthesized.append(seg.key)

    valid = {s.key for s in spoken}
    manifest.segments = {k: v for k, v in manifest.segments.items() if k in valid}
    manifest.save(project.manifest_path)
    missing = [s.key for s in spoken if s.key not in manifest.segments]
    if missing:
        log.warning("sections %s have no narration yet; the timeline covers the rest", missing)
    timeline = build_timeline(project, manifest, spoken)
    return NarrateResult(manifest=manifest, timeline=timeline, segments=targets, synthesized=synthesized, cached=cached)
