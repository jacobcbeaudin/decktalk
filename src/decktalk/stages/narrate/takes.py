"""Writing a placeholder take, charging a voiced one, placing each, and joining every take into one narration track.

A take is the voice's own bytes and nothing else, and no stage ever rewrites it. Each take reads one
turn of a section. Where a take lands is placement, and placement is a pure function of the take and
its own section's settings and script: a section's first turn carries its lead, the section's
`lead_seconds` or `[narration] lead_seconds`, each take plays to where its sound ends, measured from
its own bytes, each turn but the last is followed by the gap the script writes where the voice
changes or `[narration] turn_gap_seconds`, and the last is followed by the section's `tail_seconds` or
`[narration] tail_seconds`. The join puts the lead before the first take, cuts each take at its
sound end and puts its gap or the tail after it, so whatever a take holds past its sound end, such as
a breath after its last word, never plays, and the silence across every cut is one tail plus one
lead. Nothing about a neighbouring section, and nothing about whether this run voiced the take or
found it on disk, reaches those numbers, which is what lets a change to one sentence rebuild one
section and no other. None of them is part of the input digest either, so changing a lead, a gap or
a tail voices nothing, and a section of one turn lands exactly as one take always has.

A run without voice writes a placeholder, click audio of the length the words and the declared pauses come to,
with evenly spaced estimated words, so cues resolve to plausible times and the whole pipeline runs
with no credential. It clicks at every word's start and once where the last word ends, then closes
on a moment of silence, so its sound ends where its words do and it is placed exactly as a voice is.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import (
    PLACEHOLDER_SUFFIX,
    AlignedWords,
    EstimatedWords,
    ProviderWords,
    Take,
    Takes,
    TimedBy,
    Words,
    is_placeholder,
    take_file,
    words_file,
)
from decktalk.events import TakeCharged
from decktalk.inputs import Inputs
from decktalk.inputs.script import ScriptSection, Turn
from decktalk.machine.run import Run
from decktalk.media import audio, ffmpeg
from decktalk.speech import canonical_text
from decktalk.stages.cost import Buy, charge_of

PLACEHOLDER_CLOSE_SECONDS = 0.1
"""Calibration: the silence a placeholder's click audio ends on, long enough to measure where its sound ends."""


def sound_end_of(inputs: Inputs, path: Path) -> float:
    """Where one take's sound ends, read from its own bytes at the levels `[narration]` names."""
    cfg = inputs.settings.narration
    return audio.sound_end(
        path,
        noise_dbfs=cfg.sound_end_noise_dbfs,
        min_run_seconds=cfg.sound_end_min_run_seconds,
    )


def place(inputs: Inputs, row: Take, *, gap: float | None) -> Take:
    """The row with its placement filled in: its lead, where its sound ends, and its tail or gap.

    It reads the take's own bytes and the section's own settings and nothing else, so the same take
    under the same settings lands the same way on every run, whichever path wrote the row. A first
    turn takes the section's lead and any other none. `gap` is the silence after a turn that is not its
    section's last, and None for the last, which takes the section's tail. The sound end is measured
    once, and a row that carries it already keeps it, because the file its digest names holds the same
    bytes it was measured on.
    """
    end = row.sound_end_seconds
    path = inputs.take_places.find(row.digest).audio
    if end is None and path.exists():
        end = sound_end_of(inputs, path)
    return row.model_copy(
        update={
            "sound_end_seconds": end,
            "lead_seconds": inputs.lead_seconds(row.section) if row.turn == 1 else 0.0,
            "tail_seconds": inputs.tail_seconds(row.section) if gap is None else gap,
        }
    )


def gap_after(inputs: Inputs, section: ScriptSection, turn: Turn) -> float | None:
    """The silence after one turn of its section, or None for the last, which the section's tail follows."""
    return None if turn.number == len(section.turns) else inputs.gap_seconds(turn)


def _timing(words: Words) -> tuple[TimedBy, str | None]:
    """Who timed these words, and the key of the alignment they are when an aligner did."""
    if isinstance(words, AlignedWords):
        return TimedBy.ALIGNED, words.inputs.key
    return (TimedBy.ESTIMATED if words.estimated else TimedBy.PROVIDER), None


def take_row(
    inputs: Inputs,
    section: ScriptSection,
    turn: Turn,
    chapter: str,
    digest: str,
    words: Words,
    *,
    said: ProviderWords | None,
) -> Take:
    """The take index row for one turn, placed, with the fields every kind of take shares.

    `words` are the words the take plays, which say who timed it and where its last word ends, and
    `said` is the take's own words file, which names the model its voice served, or None for a
    placeholder. The row names the voice that reads the turn, as a label.
    """
    timed_by, alignment = _timing(words)
    row = Take(
        section=section.number,
        turn=turn.number,
        key=section.key,
        chapter=chapter,
        digest=digest,
        voiced=not is_placeholder(digest),
        word_count=turn.word_count,
        characters=len(canonical_text(turn.pieces)),
        estimated_seconds=turn.estimated_seconds(inputs.settings.narration),
        duration_seconds=ffmpeg.probe_duration(inputs.take_places.find(digest).audio),
        speech_end_seconds=words.end,
        spoken=turn.spoken,
        voice=inputs.voice_of(turn).name,
        timed_by=timed_by,
        alignment=alignment,
        model_digest=said.model_digest if said is not None else None,
    )
    return place(inputs, row, gap=gap_after(inputs, section, turn))


def retimed(row: Take, words: Words) -> Take:
    """The row playing `words` in place of what it played, which moves who timed it and where its last word ends."""
    timed_by, alignment = _timing(words)
    return row.model_copy(update={"timed_by": timed_by, "alignment": alignment, "speech_end_seconds": words.end})


def write_placeholder_take(
    inputs: Inputs, section: ScriptSection, turn: Turn, chapter: str, digest: str
) -> tuple[Take, list[Path]]:
    """Write one turn's placeholder click audio and its estimated words, and give back the row and the files."""
    cfg = inputs.settings.narration
    home = inputs.workspace.narrate_dir
    out = home / take_file(digest, PLACEHOLDER_SUFFIX)
    duration = turn.placeholder_seconds(cfg)
    words = turn.estimated_words(duration)
    clicks = [word.start for word in words] + ([words[-1].end] if words else [])
    audio.write_clicks(
        out,
        duration + PLACEHOLDER_CLOSE_SECONDS,
        clicks,
        sample_rate=inputs.settings.audio.sample_rate,
        bitrate=cfg.mp3_bitrate,
    )
    written = home / words_file(digest)
    estimated = EstimatedWords(words=tuple(words))
    estimated.write(written)
    return take_row(inputs, section, turn, chapter, digest, estimated, said=None), [out, written]


def charge_take(
    inputs: Inputs, run: Run, section: ScriptSection, turn: Turn, digest: str, buy: Buy, *, possibly: bool = False
) -> None:
    """Put the charge for one voiced take on the stream, once its voice has answered.

    A provider that bills is paid the moment it answers, so the charge goes on the stream before
    anything that could fail writes the take. A host that keeps its own ledger then records every
    take it paid for, even one whose file never reached the disk. The charge is `buy` at the bill the
    provider declares, the same buy the run was priced and approved at, so a per-second bill is
    charged on the length the script gave the take. A provider that declares it bills nothing is paid
    nothing, so its take puts no charge on the stream, and every charge line is money paid, as
    `sound.charged` is, unless it is `possibly` charged: its request was sent and its reply broke,
    so the provider may have billed it, and the line says so.
    """
    dollars = charge_of(inputs, buy)
    if dollars is None or buy.voice is None:
        return
    run.emit(
        TakeCharged,
        section=section.number,
        turn=turn.number,
        digest=digest,
        voice=buy.voice.name,
        provider=buy.voice.provider,
        characters=buy.characters,
        dollars=dollars,
        possibly_charged=possibly,
    )


def join_takes(inputs: Inputs, takes: Takes) -> Path:
    """Join the takes into one narration track, in the order the index holds them.

    The index is the one order the narration plays in, so the join reads its rows rather than the
    script, and the two can never disagree about which take follows which. Each take follows its
    row's lead of silence, plays to its sound end and is cut there, and is followed by its tail or its
    gap, so the track is exactly the arithmetic `Takes` does over the rows.
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
    "gap_after",
    "join_takes",
    "place",
    "retimed",
    "sound_end_of",
    "take_row",
    "write_placeholder_take",
]
