"""Writing one take, placing it, and joining every take into one narration track.

A take is the voice's own bytes and nothing else, and no stage ever rewrites it. Where a take lands
is placement, and placement is a pure function of the take and its own section's settings: its lead
is the section's `lead_seconds` or `[narration] lead_seconds`, the take plays to where its sound
ends, measured from its own bytes, and its tail is the section's `tail_seconds` or
`[narration] tail_min_seconds` after that. The join puts the lead before the take, cuts the take at
its sound end and puts the tail after it, so whatever the take holds past its sound end, such as a
breath after its last word, never plays, and the silence across every cut is one tail plus one lead.
Nothing about a neighbour, and nothing about whether this run voiced the take or found it cached,
reaches those numbers, which is what lets a change to one sentence rebuild one section and no other.
None of them is part of the content hash either, so changing a lead or a tail voices nothing.

A run without voice writes a click track of the length the words and the declared pauses come to,
with evenly spaced estimated words, so cues resolve to plausible times and the whole pipeline runs
with no credential. It clicks at every word's start and once where the last word ends, then closes
on a moment of silence, so its sound ends where its words do and it is placed exactly as a voice is.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import Take, Takes, Words, take_file, words_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import PUNCT, Segment
from decktalk.media import audio, ffmpeg
from decktalk.results import Word
from decktalk.speech import SpeechProvider, SpeechRequest
from decktalk.stages import SECOND_DIGITS
from decktalk.stages.narrate.plan import TakePlan, is_cached

PLACEHOLDER_CLOSE_SECONDS = 0.1
"""Calibration: the silence a click track ends on, which is long enough that where its sound ends can be measured."""

WORD_GAP_SECONDS = 0.02
"""Calibration: the gap an estimated word leaves before the next one, so two clicks are never one sound."""

SHORTEST_PLACEHOLDER_SECONDS = 0.1
"""Calibration: the least a placeholder take may run, so a section of one short word still has room for a click."""


def sound_end_of(inputs: Inputs, path: Path) -> float:
    """Where one take's sound ends, read from its own bytes at the levels `[narration]` names."""
    cfg = inputs.settings.narration
    return audio.sound_end(
        path,
        noise_dbfs=cfg.sound_end_noise_dbfs,
        min_run_seconds=cfg.sound_end_min_run_seconds,
    )


def place(inputs: Inputs, number: int, row: Take) -> Take:
    """The row with its placement filled in: its lead, where its sound ends, and its tail.

    It reads the take's own bytes and the section's own settings and nothing else, so the same take
    under the same settings lands the same way on every run, whichever path wrote the row. The sound
    end is measured once, and a row that carries it already keeps it, because the file its hash
    names holds the same bytes it was measured on.
    """
    end = row.sound_end_seconds
    path = inputs.workspace.takes_dir / row.file
    if end is None and path.exists():
        end = sound_end_of(inputs, path)
    return row.model_copy(
        update={
            "sound_end_seconds": end,
            "lead_seconds": inputs.lead_seconds(number),
            "tail_seconds": inputs.tail_seconds(number),
        }
    )


def estimated_words(segment: Segment, duration: float) -> list[Word]:
    """Evenly spaced words for a run without voice, so every cue resolves to a plausible time."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    per = max(SHORTEST_PLACEHOLDER_SECONDS, duration) / len(tokens)
    return [
        Word(
            word=token.strip(PUNCT),
            start=round(at * per, SECOND_DIGITS),
            end=round((at + 1) * per - WORD_GAP_SECONDS, SECOND_DIGITS),
        )
        for at, token in enumerate(tokens)
    ]


def take_row(inputs: Inputs, segment: Segment, chapter: str, digest: str, *, voiced: bool) -> Take:
    """The take index row for one section, placed, with the fields every kind of take shares."""
    takes_dir = inputs.workspace.takes_dir
    written = Words.read(takes_dir / words_file(digest))
    row = Take(
        section=segment.index,
        key=segment.key,
        chapter=chapter,
        hash=digest,
        voiced=voiced,
        word_count=segment.word_count,
        characters=len(segment.tts_text),
        estimated_seconds=segment.estimated_seconds(inputs.settings.narration),
        duration_seconds=ffmpeg.probe_duration(takes_dir / take_file(digest)),
        speech_end_seconds=written.end if written is not None else None,
        spoken=segment.spoken,
    )
    return place(inputs, segment.index, row)


def write_placeholder_take(inputs: Inputs, segment: Segment, chapter: str, digest: str) -> tuple[Take, list[Path]]:
    """Write one click track and its estimated words, and give back the row and the files."""
    cfg = inputs.settings.narration
    takes_dir = inputs.workspace.takes_dir
    out = takes_dir / take_file(digest)
    duration = segment.silent_seconds(cfg)
    words = estimated_words(segment, duration)
    clicks = [word.start for word in words] + ([words[-1].end] if words else [])
    audio.write_clicks(
        out,
        duration + PLACEHOLDER_CLOSE_SECONDS,
        clicks,
        sample_rate=inputs.settings.video.sample_rate,
        bitrate=cfg.mp3_bitrate,
    )
    written = takes_dir / words_file(digest)
    Words(words=tuple(words)).write(written)
    return take_row(inputs, segment, chapter, digest, voiced=False), [out, written]


def write_voiced_take(
    inputs: Inputs,
    provider: SpeechProvider,
    segment: Segment,
    chapter: str,
    digest: str,
    request: SpeechRequest,
) -> tuple[Take, list[Path]]:
    """Send one request, write the mp3 and its words as they came, and give back the row and the files."""
    takes_dir = inputs.workspace.takes_dir
    out = takes_dir / take_file(digest)
    spoken, words = provider.speak(request)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(spoken)
    written = takes_dir / words_file(digest)
    Words(words=tuple(words)).write(written)
    return take_row(inputs, segment, chapter, digest, voiced=True), [out, written]


def index_cached_take(inputs: Inputs, segment: Segment, chapter: str, digest: str, *, voiced: bool) -> Take:
    """The row for a take already on disk, placed by the same rule as a take this run wrote."""
    return take_row(inputs, segment, chapter, digest, voiced=voiced)


def join_takes(inputs: Inputs, takes: Takes) -> Path:
    """Join the takes into one narration track, in the order the index holds them.

    The index is the one order the narration plays in, so the join reads its rows rather than the
    script, and the two can never disagree about which take follows which. Each take follows its
    row's lead of silence, plays to its sound end and is cut there, and is followed by its tail, so
    the track is exactly the arithmetic `Takes` does over the rows.
    """
    cfg = inputs.settings.narration
    narration = inputs.workspace.narration_path
    narration.parent.mkdir(parents=True, exist_ok=True)
    audio.concat_audio(
        [
            audio.Placement(
                inputs.workspace.takes_dir / row.file,
                lead=row.lead_seconds,
                play=row.sound_seconds,
                tail=row.tail_seconds,
            )
            for row in takes.sections
        ],
        narration,
        bitrate=cfg.mp3_bitrate,
        sample_rate=inputs.settings.video.sample_rate,
    )
    return narration


def planned_words(inputs: Inputs, plan: TakePlan) -> tuple[tuple[Word, ...], float, bool]:
    """(the words, the span, whether they are estimated) a section will have after a voiced run.

    The words count from the section's start, which is after its lead, and the span is placed by the
    one rule every take is placed by: the lead, the take to where its sound ends, and the tail. This
    is what lets `check` resolve every cue against the words a section will have before anything is
    voiced.
    """
    segment = plan.segment
    number = segment.index
    lead, tail = inputs.lead_seconds(number), inputs.tail_seconds(number)
    index = inputs.takes()
    row = index.of(number) if index is not None else None
    paid = row if row is not None and row.voiced else None
    if plan.cached and plan.digest is not None and is_cached(plan.digest, inputs.workspace.takes_dir):
        # The take of this exact text is on disk, so the cues land on the words it already carries.
        words = inputs.words(number, plan.digest)
        if paid is not None and paid.hash == plan.digest:
            return words, place(inputs, number, paid).span_seconds, False
        end = sound_end_of(inputs, inputs.workspace.takes_dir / take_file(plan.digest))
        return words, round(lead + end + tail, SECOND_DIGITS), False
    if plan.unchecked and paid is not None and paid.spoken == segment.spoken:
        # There is no voice to ask, and the take on disk was voiced from this exact text.
        return inputs.words(number, paid.hash), place(inputs, number, paid).span_seconds, False
    length = segment.silent_seconds(inputs.settings.narration)
    shifted = tuple(
        Word(word=word.word, start=round(word.start + lead, SECOND_DIGITS), end=round(word.end + lead, SECOND_DIGITS))
        for word in estimated_words(segment, length)
    )
    return shifted, round(lead + length + tail, SECOND_DIGITS), True


__all__ = [
    "PLACEHOLDER_CLOSE_SECONDS",
    "estimated_words",
    "index_cached_take",
    "join_takes",
    "place",
    "planned_words",
    "sound_end_of",
    "take_row",
    "write_placeholder_take",
    "write_voiced_take",
]
