"""Writing one take, and joining every take into one narration track.

A take is the voice's own bytes and nothing else. Silence before the first word is joined in when
the takes are joined, so it is never part of a take and never part of its content hash, which is
what lets one file serve a section wherever the section is numbered. A take in this project's own
build directory carries its tail inside it: `min_tail_seconds` is padded onto the file, measured
first, so raising the key pads every cached take and sends no request. A take in a shared
`[narration] cache_dir` belongs to every project that reads it and is never rewritten, so its tail
is joined in after it instead, exactly as its lead is joined in before it.

A run without voice writes a click track of the length the words and the declared pauses come to,
with evenly spaced estimated words, so cues resolve to plausible times and the whole pipeline runs
with no API key.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ...artifacts import Take, Takes, Timeline, TimelineSection, Word, read_words, write_words
from ...media import audio, ffmpeg
from ...model import PageSection, Project
from ...model.script import PUNCT, Segment
from ...settings import NarrationConfig
from ...speech import SpeechProvider, SpeechRequest
from .plan import take_name, words_name

log = logging.getLogger(__name__)


def section_config(project: Project, seg: Segment) -> NarrationConfig:
    """The narration settings for one section: its own `tail_seconds`, when it sets one, replaces min_tail_seconds."""
    cfg = project.settings.narration
    sec = project.section(seg.index)
    tail = sec.tail_seconds if isinstance(sec, PageSection) else None
    return cfg if tail is None else replace(cfg, min_tail_seconds=tail)


def tail_shortfall(path: Path, cfg: NarrationConfig, *, tolerance: float = 0.0) -> float:
    """The silence this file still needs after its last word, which is zero when it has enough already.

    A tail within `tolerance` of min_tail_seconds counts as long enough.
    """
    tail = audio.trailing_silence(path)
    if tail >= cfg.min_tail_seconds - tolerance:
        return 0.0
    return round(cfg.min_tail_seconds - tail + cfg.tail_slack_seconds, 3)


def ensure_tail(path: Path, cfg: NarrationConfig, *, tolerance: float = 0.0) -> float:
    """Pad with silence so speech ends at least min_tail_seconds before the file ends. Returns seconds added.

    A tail within `tolerance` of min_tail_seconds counts as long enough.
    """
    add = tail_shortfall(path, cfg, tolerance=tolerance)
    if add:
        audio.pad_tail(path, add, bitrate=cfg.mp3_bitrate)
    return add


def add_tail(project: Project, path: Path, cfg: NarrationConfig, *, tolerance: float = 0.0) -> tuple[float, float]:
    """(seconds padded into the file, seconds to join after it) for one take's tail.

    A take in this project's own build directory is padded in place, which is what lets a raised
    `min_tail_seconds` cost no request. A take in a shared `[narration] cache_dir` belongs to every
    project that reads it, and `min_tail_seconds` is not part of the content hash, so the file is
    never rewritten there and the silence is joined in after it instead, exactly as a lead is
    joined in before it.
    """
    if project.takes_dir == project.narration_dir:
        return ensure_tail(path, cfg, tolerance=tolerance), 0.0
    return 0.0, tail_shortfall(path, cfg, tolerance=tolerance)


def estimated_words(segment: Segment, duration: float, cfg: NarrationConfig) -> list[Word]:
    """Evenly spaced words for runs without voice, so cues resolve to plausible times."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    span = max(0.1, duration - cfg.min_tail_seconds)
    per = span / len(tokens)
    return [
        Word(word=t.strip(PUNCT), start=round(i * per, 3), end=round((i + 1) * per - 0.02, 3))
        for i, t in enumerate(tokens)
    ]


def _row(
    project: Project,
    seg: Segment,
    chapter: str,
    digest: str,
    *,
    voiced: bool = True,
    duration_seconds: float,
    speech_end_seconds: float | None,
    tail_padded_seconds: float = 0.0,
    tail_joined_seconds: float = 0.0,
) -> Take:
    """The take index row for one section, with the fields every kind of take shares."""
    return Take(
        index=seg.index,
        chapter=chapter,
        file=take_name(digest),
        words_file=words_name(digest),
        hash=digest,
        word_count=seg.word_count,
        estimated_seconds=seg.estimated_seconds(project.settings.narration),
        duration_seconds=duration_seconds,
        voiced=voiced,
        target_seconds=seg.target_seconds,
        speech_end_seconds=speech_end_seconds,
        tail_padded_seconds=tail_padded_seconds,
        tail_joined_seconds=tail_joined_seconds,
        lead_seconds=project.lead_seconds(seg.key),
        spoken=seg.spoken,
    )


def write_silent_take(project: Project, seg: Segment, chapter: str, digest: str) -> Take:
    """Write one click track and its estimated words, and return the row that indexes them."""
    cfg = section_config(project, seg)
    out = project.takes_dir / take_name(digest)
    duration = seg.silent_seconds(cfg)
    words = estimated_words(seg, duration, cfg)
    audio.write_clicks(
        out,
        duration,
        [w.start for w in words],
        sample_rate=project.settings.video.sample_rate,
        bitrate=cfg.mp3_bitrate,
    )
    duration = ffmpeg.probe_duration(out)
    write_words(project.takes_dir / words_name(digest), words)
    log.info("[sil ] %s  %d words -> %.2fs (estimated words)", seg.key, seg.word_count, duration)
    return _row(
        project,
        seg,
        chapter,
        digest,
        voiced=False,
        duration_seconds=duration,
        speech_end_seconds=words[-1].end if words else None,
    )


def write_voiced_take(
    project: Project, provider: SpeechProvider, seg: Segment, chapter: str, digest: str, request: SpeechRequest
) -> Take:
    """Send one request, write the mp3 and its words, pad the tail, and return the row that indexes them."""
    cfg = section_config(project, seg)
    out = project.takes_dir / take_name(digest)
    log.info("[tts ] %s  %d words, est %.1fs ...", seg.key, seg.word_count, seg.estimated_seconds(cfg))
    mp3, words = provider.speak(request)
    out.write_bytes(mp3)
    write_words(project.takes_dir / words_name(digest), words)
    added, joined = add_tail(project, out, cfg)
    duration = ffmpeg.probe_duration(out)
    speech_end = words[-1].end if words else None
    log.info(
        "       %s  %.2fs (%d words, speech ends %s%s)",
        seg.key,
        duration,
        len(words),
        speech_end,
        f", tail +{added}s" if added else "",
    )
    return _row(
        project, seg, chapter, digest, duration_seconds=duration, speech_end_seconds=speech_end,
        tail_padded_seconds=added, tail_joined_seconds=joined,
    )  # fmt: skip


def index_cached_take(
    project: Project, seg: Segment, chapter: str, digest: str, *, voiced: bool, previous: Takes | None
) -> Take:
    """The row for a take already on disk, padded to this section's tail if the key has been raised since.

    The tail is measured before it is padded, so a take that already has enough is left alone, and a
    take this stage padded before keeps its tail when the measurement lands within one mp3 frame of
    min_tail_seconds, so a second run never pads it again. That earlier padding is looked up by the
    digest rather than by the section number, because a take belongs to its content and two sections
    of the same words share one file.
    """
    cfg = section_config(project, seg)
    out = project.takes_dir / take_name(digest)
    rows = previous.sections.values() if previous is not None else ()
    padded = next((row.tail_padded_seconds for row in rows if row.hash == digest), 0.0)
    tolerance = audio.SILENCE_END_TOLERANCE_SECONDS if padded else 0.0
    added, joined = add_tail(project, out, cfg, tolerance=tolerance) if voiced else (0.0, 0.0)
    duration = ffmpeg.probe_duration(out)
    words = read_words(project.takes_dir / words_name(digest))
    log.info("[skip] %s  unchanged (%.2fs%s)", seg.key, duration, f", tail +{added}s" if added else "")
    return _row(
        project,
        seg,
        chapter,
        digest,
        voiced=voiced,
        duration_seconds=duration,
        speech_end_seconds=words[-1].end if words else None,
        tail_padded_seconds=round(padded + added, 3),
        tail_joined_seconds=joined,
    )


def build_timeline(project: Project, takes: Takes, order: list[Segment]) -> Timeline:
    """Join the takes into one narration track, and record where each section and each word lands in it.

    Each section's `lead_seconds` is silence joined in before its take, which is what keeps the
    take itself free of the project's own silence and therefore free to serve any section number.
    `tail_joined_seconds` is the same for the tail of a take that sits in a shared cache, which no
    project may rewrite.
    """
    cfg = project.settings.narration
    keys = [s.key for s in order if s.key in takes.sections]
    files = [project.takes_dir / takes.sections[k].file for k in keys]
    leads = [project.lead_seconds(k) for k in keys]
    tails = [takes.sections[k].tail_joined_seconds for k in keys]
    narration = project.narration_dir / "narration.mp3"
    audio.concat_audio(
        files,
        narration,
        bitrate=cfg.mp3_bitrate,
        sample_rate=project.settings.video.sample_rate,
        leads=leads,
        tails=tails,
    )
    at = 0.0
    sections: dict[str, TimelineSection] = {}
    for key, file, lead, tail in zip(keys, files, leads, tails, strict=True):
        span = lead + tail + ffmpeg.decoded_duration(file, sample_rate=project.settings.video.sample_rate)
        words = project.section_words(key, takes.sections[key].words_file)
        sections[key] = TimelineSection(
            title=takes.sections[key].chapter,
            start=round(at, 3),
            end=round(at + span, 3),
            duration=round(span, 3),
            speech_end=round(at + words[-1].end, 3) if words else None,
            words=[Word(w.word, round(at + w.start, 3), round(at + w.end, 3)) for w in words],
            lead_seconds=lead,
        )
        at += span
    timeline = Timeline(
        narration=narration.name,
        estimated=takes.estimated,
        total_seconds=ffmpeg.decoded_duration(narration, sample_rate=project.settings.video.sample_rate),
        sections=sections,
    )
    timeline.save(project.timeline_path)
    return timeline
