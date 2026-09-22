"""Writing one take, placing it, and joining every take into one narration track.

A take is the voice's own bytes and nothing else, and no stage ever rewrites it. Where a take lands
is placement, and placement is a pure function of the take and its own section's settings: its
lead is the section's `lead_seconds` or `[narration] lead_seconds`, the take plays to where its
sound ends, measured from its own bytes, and its tail is the section's `tail_seconds` or
`[narration] min_tail_seconds` after that. The join puts the lead before the take, cuts the take at
its sound end and puts the tail after it, so whatever the take holds past its sound end, such as a
breath after its last word, never plays, and the silence across every cut is one tail plus one lead. Nothing about a
neighbour, and nothing about whether this run voiced the take or found it cached, reaches those
numbers, which is what lets a change to one sentence rebuild one section and no other. None of them
is part of the content hash either, so changing a lead or a tail voices nothing.

A run without voice writes a click track of the length the words and the declared pauses come to,
with evenly spaced estimated words, so cues resolve to plausible times and the whole pipeline runs
with no API key. It clicks at every word's start and once where the last word ends, then closes on
a moment of silence, so its sound ends where its words do and is placed exactly as a voice is.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ...artifacts import Take, Takes, Word, read_words, write_words
from ...media import audio, ffmpeg
from ...model import Project
from ...model.script import PUNCT, Segment
from ...speech import SpeechProvider, SpeechRequest
from .plan import take_name, words_name

log = logging.getLogger(__name__)

PLACEHOLDER_CLOSE_SECONDS = 0.1  # Silence a click track ends on, long enough that where its sound ends is measured.


def placed(project: Project, key: str, row: Take) -> Take:
    """The row with its placement filled in: the lead before the take, where its sound ends, and the tail after.

    It reads the take's own bytes and the section's own settings and nothing else, so the same take
    under the same settings lands the same way on every run, whichever path wrote the row. The sound
    end is measured once, and a row that carries it already keeps it, because the file its hash
    names holds the same bytes it was measured on.
    """
    end = row.sound_end_seconds
    path = project.takes_dir / row.file
    if end is None and path.exists():
        end = audio.sound_end(path)
    return replace(
        row,
        sound_end_seconds=end,
        lead_seconds=project.lead_seconds(key),
        tail_seconds=project.tail_seconds(key),
    )


def estimated_words(segment: Segment, duration: float) -> list[Word]:
    """Evenly spaced words for runs without voice, so cues resolve to plausible times."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    per = max(0.1, duration) / len(tokens)
    return [
        Word(word=t.strip(PUNCT), start=round(i * per, 3), end=round((i + 1) * per - 0.02, 3))
        for i, t in enumerate(tokens)
    ]


def _row(project: Project, seg: Segment, chapter: str, digest: str, *, voiced: bool = True) -> Take:
    """The take index row for one section, placed, with the fields every kind of take shares."""
    words = read_words(project.takes_dir / words_name(digest))
    row = Take(
        index=seg.index,
        chapter=chapter,
        file=take_name(digest),
        words_file=words_name(digest),
        hash=digest,
        word_count=seg.word_count,
        estimated_seconds=seg.estimated_seconds(project.settings.narration),
        duration_seconds=ffmpeg.probe_duration(project.takes_dir / take_name(digest)),
        voiced=voiced,
        target_seconds=seg.target_seconds,
        speech_end_seconds=words[-1].end if words else None,
        spoken=seg.spoken,
    )
    return placed(project, seg.key, row)


def _placement(row: Take) -> str:
    return f"sound ends {row.sound_end_seconds}, lead {row.lead_seconds:g}s, tail {row.tail_seconds:g}s"


def write_silent_take(project: Project, seg: Segment, chapter: str, digest: str) -> Take:
    """Write one click track and its estimated words, and return the row that indexes them."""
    cfg = project.settings.narration
    out = project.takes_dir / take_name(digest)
    duration = seg.silent_seconds(cfg)
    words = estimated_words(seg, duration)
    clicks = [w.start for w in words] + ([words[-1].end] if words else [])
    audio.write_clicks(
        out,
        duration + PLACEHOLDER_CLOSE_SECONDS,
        clicks,
        sample_rate=project.settings.video.sample_rate,
        bitrate=cfg.mp3_bitrate,
    )
    write_words(project.takes_dir / words_name(digest), words)
    row = _row(project, seg, chapter, digest, voiced=False)
    log.info("[sil ] %s  %d words -> %.2fs (estimated words, %s)", seg.key, seg.word_count, row.duration_seconds,
             _placement(row))  # fmt: skip
    return row


def write_voiced_take(
    project: Project, provider: SpeechProvider, seg: Segment, chapter: str, digest: str, request: SpeechRequest
) -> Take:
    """Send one request, write the mp3 and its words as they came, and return the row that indexes them."""
    cfg = project.settings.narration
    out = project.takes_dir / take_name(digest)
    log.info("[tts ] %s  %d words, est %.1fs ...", seg.key, seg.word_count, seg.estimated_seconds(cfg))
    mp3, words = provider.speak(request)
    out.write_bytes(mp3)
    write_words(project.takes_dir / words_name(digest), words)
    row = _row(project, seg, chapter, digest)
    log.info("       %s  %.2fs (%d words, %s)", seg.key, row.duration_seconds, len(words), _placement(row))
    return row


def index_cached_take(project: Project, seg: Segment, chapter: str, digest: str, *, voiced: bool) -> Take:
    """The row for a take already on disk, placed by the same rule as a take this run wrote."""
    row = _row(project, seg, chapter, digest, voiced=voiced)
    log.info("[skip] %s  unchanged (%.2fs, %s)", seg.key, row.duration_seconds, _placement(row))
    return row


def join_takes(project: Project, takes: Takes, order: list[Segment]) -> Path:
    """Join the takes into one narration track, in the order the sections play, and give back its path.

    Each take follows its row's lead of silence, plays to its sound end and is cut there, and is
    followed by its tail of silence, so the track is exactly the arithmetic `Takes` does over the
    rows, the tail is as silent as the clock says, and nothing here writes a second file for a reader
    to disagree with.
    """
    cfg = project.settings.narration
    rows = [takes.sections[s.key] for s in order if s.key in takes.sections]
    narration = project.narration_path
    audio.concat_audio(
        [
            audio.Placement(
                project.takes_dir / row.file, lead=row.lead_seconds, play=row.sound_seconds, tail=row.tail_seconds
            )
            for row in rows
        ],
        narration,
        bitrate=cfg.mp3_bitrate,
        sample_rate=project.settings.video.sample_rate,
    )
    return narration
