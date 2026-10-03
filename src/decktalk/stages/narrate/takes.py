"""Writing a placeholder take, charging a voiced one, placing each, and joining every take into one narration track.

A take is the voice's own bytes and nothing else, and no stage ever rewrites it. Where a take lands
is placement, and placement is a pure function of the take and its own section's settings: its lead
is the section's `lead_seconds` or `[narration] lead_seconds`, the take plays to where its sound
ends, measured from its own bytes, and its tail is the section's `tail_seconds` or
`[narration] tail_seconds` after that. The join puts the lead before the take, cuts the take at
its sound end and puts the tail after it, so whatever the take holds past its sound end, such as a
breath after its last word, never plays, and the silence across every cut is one tail plus one lead.
Nothing about a neighbour, and nothing about whether this run voiced the take or found it on disk,
reaches those numbers, which is what lets a change to one sentence rebuild one section and no other.
None of them is part of the input digest either, so changing a lead or a tail voices nothing.

A run without voice writes a click track of the length the words and the declared pauses come to,
with evenly spaced estimated words, so cues resolve to plausible times and the whole pipeline runs
with no credential. It clicks at every word's start and once where the last word ends, then closes
on a moment of silence, so its sound ends where its words do and it is placed exactly as a voice is.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import (
    EstimatedWords,
    Take,
    Takes,
    words_file,
)
from decktalk.events import TakeCharged
from decktalk.inputs import Inputs
from decktalk.inputs.script import ScriptSection
from decktalk.machine.run import Run
from decktalk.media import audio, ffmpeg
from decktalk.page import SECOND_DIGITS
from decktalk.results import Word
from decktalk.speech import PUNCT, SpeechRequest, canonical_text, is_free
from decktalk.stages import billed, dollars_for

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
    end is measured once, and a row that carries it already keeps it, because the file its digest
    names holds the same bytes it was measured on.
    """
    end = row.sound_end_seconds
    path = inputs.take_places.find(row.digest).audio
    if end is None and path.exists():
        end = sound_end_of(inputs, path)
    return row.model_copy(
        update={
            "sound_end_seconds": end,
            "lead_seconds": inputs.lead_seconds(number),
            "tail_seconds": inputs.tail_seconds(number),
        }
    )


def estimated_words(section: ScriptSection, duration: float) -> list[Word]:
    """Evenly spaced words for a run without voice, so every cue resolves to a plausible time."""
    tokens = section.spoken.split()
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


def take_row(inputs: Inputs, section: ScriptSection, chapter: str, digest: str, *, voiced: bool) -> Take:
    """The take index row for one section, placed, with the fields every kind of take shares."""
    written = inputs.take_words(digest)
    row = Take(
        section=section.number,
        key=section.key,
        chapter=chapter,
        digest=digest,
        voiced=voiced,
        word_count=section.word_count,
        characters=len(canonical_text(section.pieces)),
        estimated_seconds=section.estimated_seconds(inputs.settings.narration),
        duration_seconds=ffmpeg.probe_duration(inputs.take_places.find(digest).audio),
        speech_end_seconds=written.end if written is not None else None,
        spoken=section.spoken,
    )
    return place(inputs, section.number, row)


def write_placeholder_take(
    inputs: Inputs, section: ScriptSection, chapter: str, digest: str
) -> tuple[Take, list[Path]]:
    """Write one click track and its estimated words, and give back the row and the files."""
    cfg = inputs.settings.narration
    home = inputs.workspace.narrate_dir
    out = home / inputs.workspace.take_file(digest)
    duration = section.placeholder_seconds(cfg)
    words = estimated_words(section, duration)
    clicks = [word.start for word in words] + ([words[-1].end] if words else [])
    audio.write_clicks(
        out,
        duration + PLACEHOLDER_CLOSE_SECONDS,
        clicks,
        sample_rate=inputs.settings.audio.sample_rate,
        bitrate=cfg.mp3_bitrate,
    )
    written = home / words_file(digest)
    EstimatedWords(words=tuple(words)).write(written)
    return take_row(inputs, section, chapter, digest, voiced=False), [out, written]


def charge_take(inputs: Inputs, run: Run, section: ScriptSection, digest: str, request: SpeechRequest) -> None:
    """Put the charge for one voiced take on the stream, once its voice has answered.

    A provider that bills is paid the moment it answers, so the charge goes on the stream before
    anything that could fail writes the take. A host that keeps its own ledger then records every
    take it paid for, even one whose file never reached the disk. The charge is the bill the provider
    declares, and a per-second bill is charged on the length the script gave the take, which is the
    figure the run was approved at. A provider that declares it bills nothing is paid nothing, so its
    take puts no charge on the stream, and every charge line is money paid, as `sound.charged` is.
    """
    voice = inputs.settings.voice.provider
    if is_free(voice):
        return
    characters = len(canonical_text(request.pieces))
    seconds = section.estimated_seconds(inputs.settings.narration)
    run.emit(
        TakeCharged,
        section=section.number,
        digest=digest,
        characters=characters,
        dollars=dollars_for(billed(characters, seconds, voice), inputs),
    )


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
                inputs.take_places.find(row.digest).audio,
                lead=row.lead_seconds,
                play=row.sound_seconds,
                tail=row.tail_seconds,
            )
            for row in takes.sections
        ],
        narration,
        bitrate=cfg.mp3_bitrate,
        sample_rate=inputs.settings.audio.sample_rate,
    )
    return narration


__all__ = [
    "PLACEHOLDER_CLOSE_SECONDS",
    "charge_take",
    "estimated_words",
    "join_takes",
    "place",
    "sound_end_of",
    "take_row",
    "write_placeholder_take",
]
