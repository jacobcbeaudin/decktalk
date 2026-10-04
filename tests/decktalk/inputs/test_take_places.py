"""Every place a voiced take is kept, and every rule about its copies there.

Each test builds a workspace on `tmp_path` and drives `TakePlaces` through its three calls. Nothing is
patched: a place that cannot be written is a file or a folder where the other is expected, which fails
the same way on every platform, and two runs voicing one take are two threads meeting on `Event`s.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.artifacts import PLACEHOLDER_PREFIX, AudioPrint, ProviderWords, take_file, words_file
from decktalk.errors import InputError, ProjectLocked, ProviderError
from decktalk.events import Level
from decktalk.inputs.paths import at
from decktalk.inputs.take_places import TakeFiles, TakePlaces, Witness
from decktalk.inputs.workspace import Workspace
from decktalk.results import TakeOutcome, Word
from support.takes import TAKE_SUFFIX

DIGEST = "00000000000000af"
"""The voiced take every test here keeps, voices or finds."""

PLACEHOLDER = f"{PLACEHOLDER_PREFIX}00000000000000af"
"""A placeholder's digest, which names a placeholder under the build and never a voiced take."""

WAIT = 10.0
"""How long a run here waits on another's voicing, which no test reaches unless it says so."""

SHORT_WAIT = 0.2
"""How long a run here waits when the test is that it gives up."""

MEET = 10
"""How long a thread here waits on an `Event` before the test fails, which a healthy test never reaches."""

WORDS = (Word(word="hello", start=0.0, end=0.5),)
"""What every voice here says with its take."""


class Stopped(Exception):
    """The cancel a `Witnessed` raises from `check`, standing in for the run's own."""


class Billed(Exception):
    """A charge that could not be put on the stream."""


@dataclass
class Witnessed:
    """A run as the take places see it, recording every sentence, every file written and every check."""

    notes: list[tuple[str, Level]] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    checks: int = 0
    polled: threading.Event = field(default_factory=threading.Event)
    stop_at: int | None = None

    def note(self, message: str, *, level: Level = Level.INFO) -> None:
        self.notes.append((message, level))

    def wrote(self, path: Path) -> Path:
        self.written.append(path)
        return path

    def check(self) -> None:
        self.checks += 1
        self.polled.set()
        if self.stop_at is not None and self.checks >= self.stop_at:
            raise Stopped

    def said(self) -> str:
        return "\n".join(message for message, _level in self.notes)


@dataclass
class Voice:
    """A voice and its bill, counting each call, which may hold its answer until `gate` is set or fail."""

    audio: bytes = b"VOICED"
    spoken: int = 0
    charged: int = 0
    gate: threading.Event | None = None
    speaking: threading.Event = field(default_factory=threading.Event)
    fails: Exception | None = None
    bill_fails: Exception | None = None

    def speak(self) -> tuple[bytes, Sequence[Word]]:
        self.spoken += 1
        self.speaking.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=MEET)
        if self.fails is not None:
            raise self.fails
        return self.audio, WORDS

    def charge(self) -> None:
        self.charged += 1
        if self.bill_fails is not None:
            raise self.bill_fails


def a_workspace(root: Path, *, store: Path | None) -> Workspace:
    """One project's workspace under `root`, with its takes directory and build made, on a machine with `store`."""
    for made in (root / "takes", root / "build", *([store] if store is not None else [])):
        made.mkdir(parents=True, exist_ok=True)
    return Workspace(
        root=root,
        build=root / "build",
        name="demo",
        suffix=TAKE_SUFFIX,
        takes=root / "takes",
        score_dir=root / "score",
        store=store,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return a_workspace(tmp_path / "proj", store=tmp_path / "store")


@pytest.fixture
def no_store(tmp_path: Path) -> Workspace:
    return a_workspace(tmp_path / "proj", store=None)


def places(workspace: Workspace, *, wait_seconds: float = WAIT) -> TakePlaces:
    return TakePlaces(workspace, wait_seconds=wait_seconds)


def store_of(workspace: Workspace) -> Path:
    assert workspace.store is not None
    return workspace.store


def _pair(place: Path, digest: str, audio: bytes, *, suffix: str = TAKE_SUFFIX) -> tuple[Path, Path]:
    """Write one good copy of a voiced take into `place`: its audio and the words that record it."""
    place.mkdir(parents=True, exist_ok=True)
    audio_file, words = place / take_file(digest, suffix), place / words_file(digest)
    audio_file.write_bytes(audio)
    ProviderWords(words=WORDS, audio=AudioPrint.of(audio, suffix=suffix)).write(words)
    return audio_file, words


def contents(place: Path, digest: str, *, suffix: str = TAKE_SUFFIX) -> tuple[bytes, bytes]:
    return (place / take_file(digest, suffix)).read_bytes(), (place / words_file(digest)).read_bytes()


def unreadable(place: Path) -> set[str]:
    return {path.name for path in place.glob("*.unreadable")}


def voice(
    take_places: TakePlaces, spoken: Voice, witness: Witness, *, replacing: bool = False, section: int = 1
) -> TakeOutcome:
    return take_places.voice(DIGEST, spoken.speak, spoken.charge, witness, section=section, replacing=replacing)


@pytest.fixture
def pool() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        yield executor


# ---- find ---------------------------------------------------------------------------------------------


def test_a_voiced_take_is_found_in_the_takes_directory_before_the_take_store_and_never_under_the_build(
    workspace: Workspace,
) -> None:
    _pair(workspace.narrate_dir, DIGEST, b"BUILD")
    assert places(workspace).find(DIGEST).held is False
    _pair(store_of(workspace), DIGEST, b"STORE")
    found = places(workspace).find(DIGEST)
    assert (found.audio, found.words, found.held) == (
        store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX),
        store_of(workspace) / words_file(DIGEST),
        True,
    )
    _pair(workspace.takes, DIGEST, b"TAKES")
    found = places(workspace).find(DIGEST)
    assert (found.audio, found.words, found.held) == (
        workspace.takes / take_file(DIGEST, TAKE_SUFFIX),
        workspace.takes / words_file(DIGEST),
        True,
    )


@pytest.mark.parametrize("damage", ["words-that-do-not-read", "short-audio", "empty-audio", "no-audio"])
def test_a_copy_is_held_only_when_its_words_read_and_its_audio_holds_the_bytes_they_recorded(
    no_store: Workspace, damage: str
) -> None:
    audio, words = _pair(no_store.takes, DIGEST, b"AUDIO")
    assert places(no_store).find(DIGEST).held is True
    if damage == "words-that-do-not-read":
        words.write_text("{", encoding="utf-8")
    elif damage == "short-audio":
        audio.write_bytes(b"AUD")
    elif damage == "empty-audio":
        audio.write_bytes(b"")
    else:
        audio.unlink()
    assert places(no_store).find(DIGEST).held is False


def test_a_damaged_takes_directory_copy_never_hides_a_good_copy_in_the_take_store(workspace: Workspace) -> None:
    _pair(workspace.takes, DIGEST, b"TAKES")[1].write_text("{", encoding="utf-8")
    _pair(store_of(workspace), DIGEST, b"STORE")
    found = places(workspace).find(DIGEST)
    assert found.held is True
    assert found.audio == store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX)
    assert found.in_takes_directory is True


def test_a_take_is_found_under_the_suffix_its_words_recorded_after_the_voice_changes_format(
    no_store: Workspace,
) -> None:
    _pair(no_store.takes, DIGEST, b"WAVE", suffix=".wav")
    found = places(no_store).find(DIGEST)
    assert found.held is True
    assert found.audio == no_store.takes / take_file(DIGEST, ".wav")


def test_a_placeholder_is_found_under_the_build_and_held_when_both_files_are_there_whatever_they_hold(
    workspace: Workspace,
) -> None:
    home = workspace.narrate_dir
    assert places(workspace).find(PLACEHOLDER).held is False
    home.mkdir(parents=True)
    (home / take_file(PLACEHOLDER, TAKE_SUFFIX)).write_bytes(b"")
    (home / words_file(PLACEHOLDER)).write_text("{", encoding="utf-8")
    found = places(workspace).find(PLACEHOLDER)
    assert (found.audio, found.words, found.held, found.in_takes_directory) == (
        home / take_file(PLACEHOLDER, TAKE_SUFFIX),
        home / words_file(PLACEHOLDER),
        True,
        False,
    )
    assert found.refusal(1) is None


def test_a_take_no_place_holds_names_the_takes_directory_as_where_it_would_be_written(workspace: Workspace) -> None:
    found = places(workspace).find(DIGEST)
    assert found == TakeFiles(
        digest=DIGEST,
        audio=workspace.takes / take_file(DIGEST, TAKE_SUFFIX),
        words=workspace.takes / words_file(DIGEST),
        held=False,
        in_takes_directory=False,
    )
    assert found.refusal(1) is None


def test_the_takes_directory_counts_as_holding_a_take_whose_files_are_there_whatever_they_hold(
    no_store: Workspace,
) -> None:
    (no_store.takes / words_file(DIGEST)).write_text("{", encoding="utf-8")
    assert places(no_store).find(DIGEST).in_takes_directory is False
    (no_store.takes / take_file(DIGEST, TAKE_SUFFIX)).write_bytes(b"")
    found = places(no_store).find(DIGEST)
    assert (found.held, found.in_takes_directory) == (False, True)


def test_a_take_damaged_in_every_place_is_refused_with_the_paid_record_sentence_naming_its_section(
    workspace: Workspace,
) -> None:
    audio, _words = _pair(workspace.takes, DIGEST, b"AUDIO!")
    audio.write_bytes(b"SHORT")
    _pair(store_of(workspace), DIGEST, b"STORE")[1].write_text("{", encoding="utf-8")
    refused = places(workspace).find(DIGEST).refusal(3)
    assert isinstance(refused, InputError)
    assert str(refused) == (
        f"{DIGEST}.mp3 holds 5 bytes where {DIGEST}.words.json recorded 6. No place holds a good copy of the take "
        "section 3 plays, and only voicing it again gives it back, so DeckTalk neither buys it again nor deletes it."
    )
    assert refused.hint == (
        f"Put a good copy of {DIGEST}.mp3 and {DIGEST}.words.json in takes, or run decktalk narrate --section 3 "
        "--replace-voiced --spend knowing that it buys the take again."
    )
    assert refused.location == at(workspace.takes / words_file(DIGEST), workspace.root, section=3)
    _pair(store_of(workspace), DIGEST, b"STORE")
    assert places(workspace).find(DIGEST).refusal(3) is None, "a take held somewhere is not refused"
    assert places(workspace).find("00000000000000bc").refusal(3) is None, "a missing take is not refused"
    assert places(workspace).find(PLACEHOLDER).refusal(3) is None


# ---- keep ---------------------------------------------------------------------------------------------


def test_keep_copies_the_take_stores_verified_pair_into_the_takes_directory_and_records_both_files(
    workspace: Workspace,
) -> None:
    _pair(store_of(workspace), DIGEST, b"STORE")
    witness = Witnessed()
    places(workspace).keep(DIGEST, witness, section=1)
    assert contents(workspace.takes, DIGEST) == contents(store_of(workspace), DIGEST)
    assert witness.written == [workspace.takes / take_file(DIGEST, TAKE_SUFFIX), workspace.takes / words_file(DIGEST)]
    assert witness.notes == []


def test_keep_moves_a_damaged_takes_directory_copy_aside_and_says_so_once(workspace: Workspace) -> None:
    _pair(workspace.takes, DIGEST, b"TAKES")[1].write_text("{", encoding="utf-8")
    _pair(store_of(workspace), DIGEST, b"STORE")
    witness = Witnessed()
    places(workspace).keep(DIGEST, witness, section=1)
    assert contents(workspace.takes, DIGEST) == contents(store_of(workspace), DIGEST)
    audio_name, words_name = take_file(DIGEST, TAKE_SUFFIX), words_file(DIGEST)
    assert unreadable(workspace.takes) == {f"{audio_name}.unreadable", f"{words_name}.unreadable"}
    assert (workspace.takes / f"{words_name}.unreadable").read_text(encoding="utf-8") == "{"
    assert witness.said().count("moved aside") == 1
    assert f"the good copy in {store_of(workspace)} was copied in its place." in witness.said()
    assert [level for _message, level in witness.notes] == [Level.WARNING]


@pytest.mark.parametrize("damage", ["words", "audio-cut-short"])
def test_keep_refuses_a_store_copy_with_damaged_words_or_audio_cut_short_and_moves_nothing(
    workspace: Workspace, damage: str
) -> None:
    audio, words = _pair(store_of(workspace), DIGEST, b"STORE")
    if damage == "words":
        words.write_text("{", encoding="utf-8")
    else:
        audio.write_bytes(b"ST")
    before = contents(store_of(workspace), DIGEST)
    with pytest.raises(InputError, match="No place holds a good copy"):
        places(workspace).keep(DIGEST, Witnessed(), section=1)
    assert contents(store_of(workspace), DIGEST) == before
    assert list(workspace.takes.iterdir()) == []
    assert unreadable(store_of(workspace)) == set()


def test_keep_heals_a_damaged_store_copy_from_a_whole_takes_directory_copy(workspace: Workspace) -> None:
    _pair(workspace.takes, DIGEST, b"TAKES")
    _pair(store_of(workspace), DIGEST, b"TAKES")[1].write_text("{", encoding="utf-8")
    witness = Witnessed()
    places(workspace).keep(DIGEST, witness, section=1)
    assert contents(store_of(workspace), DIGEST) == contents(workspace.takes, DIGEST)
    assert (store_of(workspace) / f"{words_file(DIGEST)}.unreadable").read_text(encoding="utf-8") == "{"
    assert witness.said().count("moved aside") == 1
    assert f"in the take store at {store_of(workspace)} is damaged" in witness.said()
    assert witness.written == [], "the take store is never one of the project's files"


def test_keep_returns_at_once_without_healing_while_another_voicing_holds_the_store_lock(
    tmp_path: Path, pool: ThreadPoolExecutor
) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    _pair(a.takes, DIGEST, b"GOOD")
    _pair(store, DIGEST, b"GOOD")[1].write_text("{", encoding="utf-8")
    gate = threading.Event()
    held = Voice(audio=b"GOOD", gate=gate)
    voicing = pool.submit(voice, places(b), held, Witnessed())
    try:
        assert held.speaking.wait(timeout=MEET)
        places(a).keep(DIGEST, Witnessed(), section=1)
        assert (store / words_file(DIGEST)).read_text(encoding="utf-8") == "{", "keep never waits on the lock"
    finally:
        gate.set()
    assert voicing.result(timeout=MEET) is TakeOutcome.VOICED


def test_two_projects_healing_one_store_copy_in_turn_leave_one_aside_pair(tmp_path: Path) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    _pair(a.takes, DIGEST, b"GOOD")
    _pair(b.takes, DIGEST, b"GOOD")
    audio, _words = _pair(store, DIGEST, b"GOOD")
    audio.write_bytes(b"BAD!")
    places(a).keep(DIGEST, Witnessed(), section=1)
    places(b).keep(DIGEST, Witnessed(), section=1)
    assert unreadable(store) == {f"{take_file(DIGEST, TAKE_SUFFIX)}.unreadable", f"{words_file(DIGEST)}.unreadable"}
    assert contents(store, DIGEST) == contents(a.takes, DIGEST)


def test_keep_says_once_that_a_damaged_store_copy_could_not_be_replaced_when_its_lock_cannot_be_made(
    workspace: Workspace,
) -> None:
    store = store_of(workspace)
    _pair(workspace.takes, DIGEST, b"TAKES")
    _pair(store, DIGEST, b"TAKES")[1].write_text("{", encoding="utf-8")
    (store / f"{DIGEST}.lock").mkdir()
    witness = Witnessed()
    places(workspace).keep(DIGEST, witness, section=1)
    assert witness.said().count("is damaged and could not be replaced") == 1
    assert (store / words_file(DIGEST)).read_text(encoding="utf-8") == "{"
    assert witness.written == []


def test_keep_leaves_an_empty_store_empty(workspace: Workspace) -> None:
    _pair(workspace.takes, DIGEST, b"TAKES")
    places(workspace).keep(DIGEST, Witnessed(), section=1)
    assert list(store_of(workspace).iterdir()) == []


def test_keep_catches_audio_swapped_for_bytes_of_the_same_length(workspace: Workspace) -> None:
    audio, _words = _pair(store_of(workspace), DIGEST, b"FIRST")
    audio.write_bytes(b"XXXXX")
    assert places(workspace).find(DIGEST).held is True, "the size check alone passes it"
    with pytest.raises(InputError, match="does not hold the bytes"):
        places(workspace).keep(DIGEST, Witnessed(), section=1)
    assert list(workspace.takes.iterdir()) == []


def test_keep_hashes_a_rewritten_take_again_even_when_its_mtime_is_put_back(no_store: Workspace) -> None:
    audio, _words = _pair(no_store.takes, DIGEST, b"FIRST")
    take_places = places(no_store)
    take_places.keep(DIGEST, Witnessed(), section=1)
    take_places.keep(DIGEST, Witnessed(), section=1)
    stamp = audio.stat()
    audio.write_bytes(b"XXXXX")
    os.utime(audio, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(InputError, match="does not hold the bytes"):
        take_places.keep(DIGEST, Witnessed(), section=1)


def test_keep_copies_back_a_takes_copy_deleted_between_two_calls_on_one_instance(workspace: Workspace) -> None:
    _pair(store_of(workspace), DIGEST, b"STORE")
    take_places = places(workspace)
    take_places.keep(DIGEST, Witnessed(), section=1)
    for name in (take_file(DIGEST, TAKE_SUFFIX), words_file(DIGEST)):
        (workspace.takes / name).unlink()
    witness = Witnessed()
    take_places.keep(DIGEST, witness, section=1)
    assert contents(workspace.takes, DIGEST) == contents(store_of(workspace), DIGEST)
    assert len(witness.written) == 2


def test_keep_writes_nothing_for_a_placeholder(workspace: Workspace) -> None:
    witness = Witnessed()
    places(workspace).keep(PLACEHOLDER, witness, section=1)
    assert (witness.written, witness.notes) == ([], [])
    assert list(workspace.takes.iterdir()) == list(store_of(workspace).iterdir()) == []
    assert not workspace.narrate_dir.exists()


def test_a_second_copy_moved_aside_never_overwrites_the_first_and_a_pair_shares_one_number(
    workspace: Workspace,
) -> None:
    _pair(store_of(workspace), DIGEST, b"STORE")
    takes = workspace.takes
    audio_name, words_name = take_file(DIGEST, TAKE_SUFFIX), words_file(DIGEST)
    (takes / words_name).write_text("{", encoding="utf-8")
    places(workspace).keep(DIGEST, Witnessed(), section=1)
    assert unreadable(takes) == {f"{words_name}.unreadable"}
    (takes / audio_name).write_bytes(b"ZZ")
    (takes / words_name).write_text("{{", encoding="utf-8")
    places(workspace).keep(DIGEST, Witnessed(), section=1)
    assert unreadable(takes) == {f"{words_name}.unreadable", f"{audio_name}.1.unreadable", f"{words_name}.1.unreadable"}
    assert (takes / f"{words_name}.unreadable").read_text(encoding="utf-8") == "{"
    assert (takes / f"{words_name}.1.unreadable").read_text(encoding="utf-8") == "{{"
    assert (takes / f"{audio_name}.1.unreadable").read_bytes() == b"ZZ"


# ---- voice --------------------------------------------------------------------------------------------


def test_voice_writes_the_pair_with_its_audio_print_words_last_and_through_to_the_take_store(
    tmp_path: Path, workspace: Workspace
) -> None:
    witness = Witnessed()
    spoken = Voice()
    assert voice(places(workspace), spoken, witness) is TakeOutcome.VOICED
    assert (spoken.spoken, spoken.charged) == (1, 1)
    audio_file, words = workspace.takes / take_file(DIGEST, TAKE_SUFFIX), workspace.takes / words_file(DIGEST)
    assert witness.written == [audio_file, words]
    assert audio_file.read_bytes() == b"VOICED"
    said = ProviderWords.read(words)
    assert said is not None
    assert said.words == WORDS
    assert said.audio == AudioPrint.of(b"VOICED", suffix=TAKE_SUFFIX)
    assert contents(store_of(workspace), DIGEST) == contents(workspace.takes, DIGEST)
    fresh = a_workspace(tmp_path / "fresh", store=store_of(workspace))
    places(fresh).keep(DIGEST, Witnessed(), section=1)
    assert contents(fresh.takes, DIGEST) == contents(workspace.takes, DIGEST)


def test_voice_while_replacing_keeps_the_stores_first_pair(workspace: Workspace) -> None:
    _pair(workspace.takes, DIGEST, b"FIRST")
    _pair(store_of(workspace), DIGEST, b"FIRST")
    outcome = voice(places(workspace), Voice(audio=b"SECOND"), Witnessed(), replacing=True)
    assert outcome is TakeOutcome.VOICED
    assert (workspace.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"SECOND"
    assert (store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"FIRST"
    assert unreadable(workspace.takes) == unreadable(store_of(workspace)) == set()


def test_voice_keeps_a_good_copy_that_arrived_before_the_lock_and_never_speaks(workspace: Workspace) -> None:
    _pair(store_of(workspace), DIGEST, b"FIRST")
    spoken, witness = Voice(), Witnessed()
    assert voice(places(workspace), spoken, witness) is TakeOutcome.KEPT
    assert (spoken.spoken, spoken.charged) == (0, 0)
    assert contents(workspace.takes, DIGEST) == contents(store_of(workspace), DIGEST)
    assert len(witness.written) == 2


@pytest.mark.parametrize("held", ["good", "damaged"])
def test_voice_while_replacing_speaks_over_a_good_copy_and_moves_a_damaged_one_aside(
    no_store: Workspace, held: str
) -> None:
    audio, _words = _pair(no_store.takes, DIGEST, b"FIRST")
    if held == "damaged":
        audio.write_bytes(b"XXXXX")
    witness = Witnessed()
    spoken = Voice(audio=b"SECOND")
    assert voice(places(no_store), spoken, witness, replacing=True) is TakeOutcome.VOICED
    assert spoken.spoken == 1
    assert audio.read_bytes() == b"SECOND"
    if held == "good":
        assert unreadable(no_store.takes) == set()
        assert "moved aside" not in witness.said()
    else:
        assert (no_store.takes / f"{audio.name}.unreadable").read_bytes() == b"XXXXX"
        assert witness.said().count("moved aside") == 1
        assert "before the take voiced again was written." in witness.said()


def test_voice_while_replacing_a_good_copy_under_another_suffix_leaves_the_new_take_alone(
    no_store: Workspace,
) -> None:
    """A good copy replaced on request is replaced whole, so its audio under the old suffix goes with its words."""
    _pair(no_store.takes, DIGEST, b"FIRST", suffix=".wav")
    assert voice(places(no_store), Voice(audio=b"SECOND"), Witnessed(), replacing=True) is TakeOutcome.VOICED
    assert sorted(path.name for path in no_store.takes.iterdir()) == sorted(
        [take_file(DIGEST, TAKE_SUFFIX), words_file(DIGEST)]
    )
    assert places(no_store).find(DIGEST).audio == no_store.takes / take_file(DIGEST, TAKE_SUFFIX)


def test_voice_replaces_a_store_copy_damaged_under_the_whole_hash_and_moves_it_aside(workspace: Workspace) -> None:
    store = store_of(workspace)
    audio, _words = _pair(store, DIGEST, b"FIRST")
    audio.write_bytes(b"XXXXX")
    witness = Witnessed()
    assert voice(places(workspace), Voice(), witness) is TakeOutcome.VOICED
    assert audio.read_bytes() == b"VOICED"
    assert (store / f"{audio.name}.unreadable").read_bytes() == b"XXXXX"
    assert f"The copy of take {DIGEST} in the take store at {store} is damaged" in witness.said()


def test_a_second_voicing_of_one_take_waits_its_turn_and_keeps_the_first_ones_copy(
    tmp_path: Path, pool: ThreadPoolExecutor
) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    gate = threading.Event()
    first, second = Voice(audio=b"FIRST", gate=gate), Voice(audio=b"SECOND")
    waiting = Witnessed()
    voicing = pool.submit(voice, places(a), first, Witnessed())
    assert first.speaking.wait(timeout=MEET)
    kept = pool.submit(voice, places(b), second, waiting)
    assert waiting.polled.wait(timeout=MEET)
    gate.set()
    assert voicing.result(timeout=MEET) is TakeOutcome.VOICED
    assert kept.result(timeout=MEET) is TakeOutcome.KEPT
    assert (second.spoken, second.charged) == (0, 0)
    assert contents(b.takes, DIGEST) == contents(a.takes, DIGEST)
    assert (b.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"FIRST"


def test_voice_refuses_after_the_wait_naming_store_wait_seconds_and_never_speaks(
    tmp_path: Path, pool: ThreadPoolExecutor
) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    gate = threading.Event()
    first, second = Voice(gate=gate), Voice()
    voicing = pool.submit(voice, places(a), first, Witnessed())
    try:
        assert first.speaking.wait(timeout=MEET)
        with pytest.raises(ProjectLocked, match=r"\[narration\] store_wait_seconds") as refused:
            voice(places(b, wait_seconds=SHORT_WAIT), second, Witnessed())
        assert refused.value.hint is not None
        assert str(store / f"{DIGEST}.lock") in refused.value.hint
        assert second.spoken == 0
    finally:
        gate.set()
    voicing.result(timeout=MEET)


@pytest.mark.parametrize("lock", ["held-by-another", "not-held"])
def test_a_cancel_during_the_wait_or_once_the_lock_is_won_never_speaks(
    tmp_path: Path, pool: ThreadPoolExecutor, lock: str
) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    gate = threading.Event()
    first, second = Voice(gate=gate), Voice()
    voicing = pool.submit(voice, places(a), first, Witnessed()) if lock == "held-by-another" else None
    try:
        if voicing is not None:
            assert first.speaking.wait(timeout=MEET)
        with pytest.raises(Stopped):
            voice(places(b), second, Witnessed(stop_at=1))
        assert second.spoken == 0
    finally:
        gate.set()
    if voicing is not None:
        voicing.result(timeout=MEET)


def test_a_speak_that_raises_writes_nothing_and_leaves_the_lock_free(workspace: Workspace) -> None:
    take_places = places(workspace, wait_seconds=SHORT_WAIT)
    with pytest.raises(ProviderError):
        voice(take_places, Voice(fails=ProviderError("the voice failed the request.")), Witnessed())
    assert list(workspace.takes.iterdir()) == []
    assert voice(take_places, Voice(), Witnessed()) is TakeOutcome.VOICED


def test_a_free_voice_that_is_down_leaves_no_draft_behind(workspace: Workspace) -> None:
    down = Voice(fails=ProviderError("nothing answered.", reached=False))
    with pytest.raises(ProviderError):
        voice(places(workspace), down, Witnessed())
    assert list(workspace.takes.iterdir()) == []
    assert [path.suffix for path in store_of(workspace).iterdir()] == [".lock"]


def test_a_charge_that_raises_still_leaves_the_pair_in_both_places_and_is_raised_after(workspace: Workspace) -> None:
    witness = Witnessed()
    with pytest.raises(Billed):
        voice(places(workspace), Voice(bill_fails=Billed()), witness)
    assert (workspace.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"VOICED"
    assert contents(store_of(workspace), DIGEST) == contents(workspace.takes, DIGEST)
    assert len(witness.written) == 2


def squat(place: Path, *, suffix: str = TAKE_SUFFIX) -> Path:
    """A folder where the take's audio would be written, which makes only the final move fail."""
    squatter = place / take_file(DIGEST, suffix)
    squatter.mkdir(parents=True)
    return squatter


def test_a_takes_write_that_fails_after_speaking_leaves_the_take_in_the_store_and_says_so(
    workspace: Workspace,
) -> None:
    squat(workspace.takes)
    spoken = Voice()
    with pytest.raises(InputError, match="A good copy of it is in the take store") as refused:
        voice(places(workspace), spoken, Witnessed())
    assert spoken.spoken == 1
    assert (store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"VOICED"
    assert refused.value.location == at(workspace.takes, workspace.root, section=1)


def test_a_takes_write_failure_while_replacing_over_the_stores_first_pair_says_the_take_was_not_kept(
    workspace: Workspace,
) -> None:
    _pair(store_of(workspace), DIGEST, b"FIRST")
    squat(workspace.takes)
    with pytest.raises(InputError) as refused:
        voice(places(workspace), Voice(audio=b"SECOND"), Witnessed(), replacing=True, section=2)
    assert "no other place kept it" in str(refused.value)
    assert "A good copy of it" not in str(refused.value)
    assert refused.value.hint is not None
    assert "decktalk narrate --section 2 --replace-voiced --spend" in refused.value.hint
    assert (store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"FIRST"


def test_a_takes_write_failure_while_replacing_a_good_takes_copy_says_the_old_take_still_plays(
    workspace: Workspace,
) -> None:
    _pair(workspace.takes, DIGEST, b"FIRST", suffix=".wav")
    before = contents(workspace.takes, DIGEST, suffix=".wav")
    squat(workspace.takes)
    with pytest.raises(InputError) as refused:
        voice(places(workspace), Voice(audio=b"SECOND"), Witnessed(), replacing=True)
    message = str(refused.value)
    assert "still holds the take it replaces" in message
    assert "copies it in for nothing" not in message
    assert refused.value.hint is not None
    assert f"{DIGEST}.wav" in refused.value.hint
    assert (store_of(workspace) / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"SECOND"
    assert contents(workspace.takes, DIGEST, suffix=".wav") == before


def test_a_store_aside_whose_write_fails_puts_the_damaged_copy_back(workspace: Workspace) -> None:
    store = store_of(workspace)
    (store / words_file(DIGEST)).write_text("{", encoding="utf-8")
    squat(store)
    witness = Witnessed()
    assert voice(places(workspace), Voice(), witness) is TakeOutcome.VOICED
    assert (store / words_file(DIGEST)).read_text(encoding="utf-8") == "{"
    assert unreadable(store) == set()
    assert witness.said().count("could not be copied into the take store") == 1
    assert (workspace.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"VOICED"


def test_a_takes_directory_that_cannot_be_written_refuses_before_speaking(workspace: Workspace) -> None:
    workspace.takes.rmdir()
    workspace.takes.write_bytes(b"")
    spoken = Voice()
    with pytest.raises(InputError, match="cannot be written") as refused:
        voice(places(workspace), spoken, Witnessed())
    assert "nothing was paid" in str(refused.value)
    assert (spoken.spoken, spoken.charged) == (0, 0)


def test_a_store_that_cannot_be_made_refuses_naming_store_dir_before_speaking(tmp_path: Path) -> None:
    store = tmp_path / "store"
    workspace = a_workspace(tmp_path / "proj", store=store)
    store.rmdir()
    store.write_bytes(b"")
    spoken = Voice()
    with pytest.raises(InputError, match=r"\[narration\] store_dir") as refused:
        voice(places(workspace), spoken, Witnessed())
    assert "nothing was paid" in str(refused.value)
    assert spoken.spoken == 0


def test_a_store_lock_that_cannot_be_taken_refuses_naming_store_dir_before_speaking(workspace: Workspace) -> None:
    (store_of(workspace) / f"{DIGEST}.lock").mkdir()
    spoken = Voice()
    with pytest.raises(InputError, match=r"\[narration\] store_dir"):
        voice(places(workspace), spoken, Witnessed())
    assert spoken.spoken == 0


def test_a_store_write_that_fails_after_speaking_warns_once_and_the_takes_directory_holds_the_pair(
    workspace: Workspace,
) -> None:
    squat(store_of(workspace))
    witness = Witnessed()
    assert voice(places(workspace), Voice(), witness) is TakeOutcome.VOICED
    assert witness.said().count("could not be copied into the take store") == 1
    assert [level for _message, level in witness.notes] == [Level.WARNING]
    assert (workspace.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"VOICED"


def test_a_machine_with_no_take_store_voices_into_the_takes_directory_with_no_lock(no_store: Workspace) -> None:
    witness = Witnessed()
    assert voice(places(no_store), Voice(), witness) is TakeOutcome.VOICED
    assert (no_store.takes / take_file(DIGEST, TAKE_SUFFIX)).read_bytes() == b"VOICED"
    assert witness.checks == 0
    assert sorted(path.name for path in no_store.takes.iterdir()) == sorted(
        [take_file(DIGEST, TAKE_SUFFIX), words_file(DIGEST)]
    )


def test_the_take_stores_files_are_never_recorded_as_written(tmp_path: Path) -> None:
    store = tmp_path / "store"
    a, b = a_workspace(tmp_path / "a", store=store), a_workspace(tmp_path / "b", store=store)
    witness = Witnessed()
    voice(places(a), Voice(), witness)
    places(b).keep(DIGEST, witness, section=1)
    (store / words_file(DIGEST)).write_text("{", encoding="utf-8")
    places(a).keep(DIGEST, witness, section=1)
    assert witness.said().count("moved aside") == 1, "the store was healed"
    assert witness.written
    assert not any(path.is_relative_to(store) for path in witness.written)


def test_a_placeholder_is_never_voiced(workspace: Workspace) -> None:
    spoken = Voice()
    with pytest.raises(ValueError, match="is a placeholder"):
        places(workspace).voice(PLACEHOLDER, spoken.speak, spoken.charge, Witnessed(), section=1, replacing=False)
    assert spoken.spoken == 0
