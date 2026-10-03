"""Writing one take, placing it, and joining every take into one narration track.

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

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from decktalk.artifacts import (
    AudioPrint,
    EstimatedWords,
    ProviderWords,
    Take,
    Takes,
    Words,
    is_placeholder,
    words_file,
)
from decktalk.errors import ProjectLocked
from decktalk.events import Level, TakeCharged
from decktalk.files import replace_all
from decktalk.inputs import Inputs
from decktalk.inputs.script import ScriptSection
from decktalk.machine.run import Run
from decktalk.media import audio, ffmpeg
from decktalk.page import SECOND_DIGITS
from decktalk.results import Word
from decktalk.speech import PUNCT, SpeechProvider, SpeechRequest, canonical_text, is_free
from decktalk.stages import billed, dollars_for
from decktalk.stages.narrate.plan import TakePlan, damaged_refusal, is_held

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
    path = inputs.workspace.take_path(row.digest)
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
    workspace = inputs.workspace
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
        duration_seconds=ffmpeg.probe_duration(workspace.take_path(digest)),
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


def write_voiced_take(
    inputs: Inputs,
    run: Run,
    provider: SpeechProvider,
    section: ScriptSection,
    chapter: str,
    digest: str,
    request: SpeechRequest,
) -> tuple[Take, list[Path]]:
    """Send one request, write the audio and its words as one pair, and give back the row and the files.

    A provider that bills is paid the moment it answers, so the charge goes on the stream before
    anything that could fail writes the take. A host that keeps its own ledger then records every
    take it paid for, even one whose file never reached the disk. The charge is the bill the provider
    declares, and a per-second bill is charged on the length the script gave the take, which is the
    figure the run was approved at. A provider that declares it bills nothing is paid nothing, so its
    take puts no charge on the stream, and every charge line is money paid, as `sound.charged` is.
    """
    home = inputs.workspace.takes
    out = home / inputs.workspace.take_file(digest)
    spoken, words = provider.speak(request)
    voice = inputs.settings.voice.provider
    if not is_free(voice):
        characters = len(canonical_text(request.pieces))
        seconds = section.estimated_seconds(inputs.settings.narration)
        run.emit(
            TakeCharged,
            section=section.number,
            digest=digest,
            characters=characters,
            dollars=dollars_for(billed(characters, seconds, voice), inputs),
        )
    written = home / words_file(digest)
    pair = {
        out: spoken,
        written: ProviderWords(words=tuple(words), audio=AudioPrint.of(spoken, suffix=inputs.workspace.suffix)).text,
    }
    # The audio and its words are replaced as one pair, and the words are moved last, so a run
    # stopped while it writes leaves both or neither and never audio with no words to vouch for it.
    replace_all(pair)
    keep_in_store(inputs, run, digest, {path.name: content for path, content in pair.items()})
    return take_row(inputs, section, chapter, digest, voiced=True), [out, written]


def keep_in_store(inputs: Inputs, run: Run, digest: str, pair: dict[str, str | bytes]) -> None:
    """Write a take this run just made through to the machine's take store, once per take.

    The store is a second copy of every take this machine bought, so a purchase nobody committed yet
    survives a `git clean`, and another project with the same words finds it. It is written once: a
    store that already holds a good copy keeps it, so a re-buy changes this project's takes directory
    and no other project's take. A damaged copy there is moved aside first, never deleted. A store
    that cannot be written loses nothing, because the takes directory already holds the pair, so
    the run says so and goes on.
    """
    workspace = inputs.workspace
    store = workspace.store
    if store is None or workspace.fault_at(store, digest) is None:
        return
    try:
        for name in pair:
            stale = store / name
            if stale.exists():
                stale.replace(stale.with_name(name + UNREADABLE_SUFFIX))
        replace_all({store / name: content for name, content in pair.items()})
    except OSError as failed:
        run.note(
            f"Take {digest} could not be copied into the take store at {store} ({failed.strerror or failed}). "
            f"It is safe in {inputs.relative(workspace.takes).as_posix()}.",
            level=Level.WARNING,
        )


STORE_LOCK_SUFFIX = ".lock"
"""What the take store's lock on one take is called after its digest, which a buyer holds while it buys."""

STORE_POLL_SECONDS = 0.1
"""Calibration: how often a run waiting on another's purchase asks again, which no person waits on."""


@contextmanager
def buying_alone(inputs: Inputs, run: Run, digest: str, *, wait_seconds: float) -> Iterator[bool]:
    """Hold the take store's lock on this take while this run buys it, and say whether another run bought it first.

    Two projects on one machine that buy the same take at once would pay twice. The lock is the
    operating system's own, taken through `filelock`, so the system releases it when its holder dies
    and no stale lock outlives a run. A run that finds the lock held waits in short turns, stopping
    when it is cancelled and refusing after `wait_seconds`, and once its turn comes it yields True
    when the store holds a good copy it did not hold before, so the run plays that copy and buys
    nothing. A machine with no store holds no lock and buys as it would.
    """
    store = inputs.workspace.store
    if store is None:
        yield False
        return
    store.mkdir(parents=True, exist_ok=True)
    lock = FileLock(store / f"{digest}{STORE_LOCK_SUFFIX}")
    # A run that replaces a take the store already holds buys it whatever the store holds, so only a
    # copy that arrived while this run waited is another run's purchase.
    held_before = inputs.workspace.fault_at(store, digest) is None
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            lock.acquire(timeout=STORE_POLL_SECONDS)
            break
        except Timeout:
            run.check()
            if time.monotonic() > deadline:
                raise ProjectLocked(
                    f"another run has been buying take {digest} into the take store at {store} for longer than "
                    f"the {wait_seconds:g} seconds narration.timeout_seconds allows, so this run bought nothing.",
                    hint="Let the other run finish, or stop it, then run this again.",
                ) from None
    try:
        yield not held_before and inputs.workspace.fault_at(store, digest) is None
    finally:
        lock.release()


UNREADABLE_SUFFIX = ".unreadable"
"""What a damaged copy of a voiced take is renamed to end in, which moves it aside and never deletes it."""


def copy_from_store(inputs: Inputs, run: Run, number: int, digest: str, checked: set[str]) -> list[Path]:
    """Make sure the takes directory holds a verified copy of this voiced take, and give back what it wrote.

    The takes directory is committed, so every take the film plays must be there for a clone to play
    it too. Each place is verified in order, its BLAKE3 included, once per take per run, which
    `checked` remembers. The first good copy wins. One found in the machine's take store is copied into
    the takes directory as one pair, after any copy already there is moved aside as `.unreadable`,
    so a damaged copy is never copied, never deleted and never hides a good one, and the run says so
    once. When every copy is damaged, the run is refused with the sentence a paid record is refused
    with, because the next step would buy the take again.
    """
    workspace = inputs.workspace
    if is_placeholder(digest) or digest in checked:
        return []
    checked.add(digest)
    home = workspace.takes
    damaged: list[tuple[Path, str]] = []
    for place in workspace.take_places:
        if not workspace.held_at(place, digest):
            continue
        fault = workspace.fault_at(place, digest, whole=True)
        if fault is None:
            return [] if place == home else _copied_in(inputs, run, digest, place, damaged)
        damaged.append((place, fault))
    if damaged:
        raise damaged_refusal(inputs, number, digest, *damaged[0])
    return []


def _copied_in(inputs: Inputs, run: Run, digest: str, found: Path, damaged: list[tuple[Path, str]]) -> list[Path]:
    """Copy the verified pair in `found` into the takes directory as one pair, moving any copy there aside."""
    workspace = inputs.workspace
    home = workspace.takes
    names = (workspace.take_file_in(found, digest), words_file(digest))
    moved = [home / name for name in names if (home / name).exists()]
    for stale in moved:
        stale.replace(stale.with_name(stale.name + UNREADABLE_SUFFIX))
    if moved:
        why = next((fault for place, fault in damaged if place == home), "it was not a whole pair")
        run.note(
            f"The copy of take {digest} in {inputs.relative(home).as_posix()} is damaged: {why} It was moved "
            f"aside as {', '.join(path.name + UNREADABLE_SUFFIX for path in moved)}, and the good copy in "
            f"{found} was copied in its place.",
            level=Level.WARNING,
        )
    copied = [home / name for name in names]
    replace_all({home / name: (found / name).read_bytes() for name in names})
    return copied


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
                inputs.workspace.take_path(row.digest),
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


def planned_words(inputs: Inputs, plan: TakePlan) -> tuple[tuple[Word, ...], float, bool]:
    """(the words, the span, whether they are estimated) a section will have after a voiced run.

    The words count from the section's start, which is after its lead, and the span is placed by the
    one rule every take is placed by: the lead, the take to where its sound ends, and the tail. This
    is what lets `check` resolve every cue against the words a section will have before anything is
    voiced.
    """
    section = plan.section
    number = section.number
    lead, tail = inputs.lead_seconds(number), inputs.tail_seconds(number)
    index = inputs.takes()
    row = index.of(number) if index is not None else None
    paid = row if row is not None and row.voiced else None
    if plan.held and plan.digest is not None and is_held(plan.digest, inputs.workspace):
        # The take of this exact text is on disk, so the cues land on the words it already carries.
        words = inputs.section_words(number, plan.digest)
        if paid is not None and paid.digest == plan.digest:
            return words, place(inputs, number, paid).span_seconds, False
        end = sound_end_of(inputs, inputs.workspace.take_path(plan.digest))
        return words, round(lead + end + tail, SECOND_DIGITS), False
    if plan.unchecked and paid is not None and paid.spoken == section.spoken:
        # There is no voice to ask, and the take on disk was voiced from this exact text.
        return inputs.section_words(number, paid.digest), place(inputs, number, paid).span_seconds, False
    length = section.placeholder_seconds(inputs.settings.narration)
    shifted = Words(words=tuple(estimated_words(section, length))).shifted(lead)
    return shifted, round(lead + length + tail, SECOND_DIGITS), True


__all__ = [
    "PLACEHOLDER_CLOSE_SECONDS",
    "estimated_words",
    "join_takes",
    "STORE_LOCK_SUFFIX",
    "UNREADABLE_SUFFIX",
    "buying_alone",
    "copy_from_store",
    "keep_in_store",
    "place",
    "planned_words",
    "sound_end_of",
    "take_row",
    "write_placeholder_take",
    "write_voiced_take",
]
