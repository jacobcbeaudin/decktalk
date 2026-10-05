"""Every place a voiced take is kept, and every rule about its copies there.

A voiced take is a paid record: only voicing it again gives it back. It is kept in two places, looked
in first to last. The takes directory is the project's own and is committed, so a clone plays every
take the film plays. The take store is the machine's, outside every project, and holds a second copy
of every take voiced on this machine, so an uncommitted take survives a `git clean` and another
project with the same words finds it. A placeholder is not a voiced take: it is a cache under
`build/narrate`, found there when both of its files are, and never voiced through the take places.

A place holds a copy when both of its files are there: the audio, under the suffix its words file
recorded or the voice's own, and the words file that records the audio's size and BLAKE3. A copy is
good when its words read and its audio holds the bytes they recorded. Finding a take asks only the
size, which is cheap. Keeping or voicing it asks the whole BLAKE3, which reads every byte, so each
verdict is remembered by the stat of both files, inode, size and both change times included, and a
file rewritten since is hashed again. One instance therefore serves a project for its whole life,
across runs and threads, and nothing else about the places is remembered.

A voiced take is written to the take store first and the takes directory second, once per take: a
store that holds a good copy keeps it, so a take voiced again on request changes this project's
takes directory and never the take another project plays. Voicing holds the store's lock on that
take, the operating system's own through `filelock`, so two runs on one machine voice one take once:
the second waits up to `[narration] store_wait_seconds` for the lock and keeps the copy the first
left, and it is refused when the wait runs out. Before the voice is asked, each place the take will
be written is made and proved writable, so a place that cannot hold it is refused before anything is
paid.

Nothing damaged is ever deleted. A damaged copy is moved aside as `<name>.unreadable`, or as
`<name>.<n>.unreadable` with the first number free for both files, in the same step that a good copy
replaces it, and it is put back when that write fails. When no place holds a good copy and one holds
a damaged copy, the take is refused with the sentence a paid record is refused with, because the next
step would voice it again.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from filelock import FileLock, Timeout

from decktalk.artifacts import (
    AudioPrint,
    ProviderWords,
    Unreadable,
    file_digest,
    is_placeholder,
    take_file,
    words_file,
)
from decktalk.errors import InputError, ProjectLocked
from decktalk.events import Level
from decktalk.files import DRAFT_TOKEN_BYTES, replace_all
from decktalk.inputs.paths import at, relative
from decktalk.inputs.workspace import Workspace
from decktalk.results import TakeOutcome, Word

STORE_LOCK_SUFFIX = ".lock"
"""What the take store's lock on one take is called after its digest, which a voicing holds while it voices."""

STORE_POLL_SECONDS = 0.1
"""Calibration: how often a run waiting on another's voicing asks again, which no person waits on."""

UNREADABLE_SUFFIX = ".unreadable"
"""What a damaged copy of a voiced take is renamed to end in, after `.<n>` when the bare name is taken."""

PREFLIGHT_SUFFIX = ".preflight"
"""What the empty file a voicing writes and removes in each place before it speaks ends in."""

HALF_PAIR = "Only one of its two files is there."
"""Why a place holding the audio or the words of a take, and not both, holds a damaged copy."""


class Witness(Protocol):
    """What the take places tell the run they work for, which `Run` already is."""

    def note(self, message: str, *, level: Level = ...) -> None:
        """Say one sentence on the run's stream."""
        ...

    def wrote(self, path: Path) -> Path:
        """Record one project file the take places wrote."""
        ...

    def check(self) -> None:
        """Raise when the run was asked to stop."""
        ...


@dataclass(frozen=True)
class TakeFiles:
    """One take's audio and words: the first good copy's, or where a new pair would be written."""

    digest: str
    audio: Path
    """The take's audio, which may be in the take store, and is under `build/narrate` for a placeholder."""
    words: Path
    """The words file beside that audio."""
    held: bool
    """Some place holds a good copy, by the size its words recorded."""
    in_takes_directory: bool
    """The takes directory holds both files of the take, whatever they hold."""
    _fault: tuple[Path, str] | None = field(default=None, repr=False, compare=False)
    _workspace: Workspace | None = field(default=None, repr=False, compare=False)

    def refusal(self, section: int) -> InputError | None:
        """The paid record's refusal when a place holds both files of a damaged copy and none a good one, else None."""
        if self.held or self._fault is None or self._workspace is None:
            return None
        return _damaged(self._workspace, section, self.digest, *self._fault)


@dataclass(frozen=True)
class _Copy:
    """One place's copy of one voiced take: its two files, which of them are there, and what is wrong with it."""

    place: Path
    audio: Path
    words: Path
    there: bool
    """Either file is there."""
    pair: bool
    """Both files are there."""
    fault: str | None
    """What is wrong with the copy, `HALF_PAIR` when one file is there, and None when it is good or absent."""

    @property
    def good(self) -> bool:
        return self.pair and self.fault is None

    def why(self) -> str:
        return self.fault or HALF_PAIR


class TakePlaces:
    """The places a voiced take is kept, first to last: the takes directory, then the take store.

    Each copy is hashed whole at most once while its files are unchanged, so one instance serves a
    project for its whole life, across runs and threads.
    """

    def __init__(self, workspace: Workspace, *, wait_seconds: float) -> None:
        self._workspace = workspace
        self._wait_seconds = wait_seconds
        self._judged: dict[tuple[object, ...], str | None] = {}
        self._judging = threading.Lock()

    def find(self, digest: str) -> TakeFiles:
        """Where this take's files are, by the cheap check alone, which reads which places hold it every time."""
        workspace = self._workspace
        if is_placeholder(digest):
            audio, words = (
                workspace.narrate_dir / workspace.take_file(digest),
                workspace.narrate_dir / words_file(digest),
            )
            return TakeFiles(digest, audio, words, held=audio.is_file() and words.is_file(), in_takes_directory=False)
        copies = [self._judge(place, digest, whole=False) for place in self._places]
        takes = copies[0]
        good = next((copy for copy in copies if copy.good), None)
        fault = next(((copy.place, copy.why()) for copy in copies if copy.pair and not copy.good), None)
        shown = good or takes
        return TakeFiles(
            digest,
            shown.audio,
            shown.words,
            held=good is not None,
            in_takes_directory=takes.pair,
            _fault=fault,
            _workspace=workspace,
        )

    def keep(self, digest: str, witness: Witness, *, section: int) -> None:
        """Make sure the takes directory holds a whole good copy of this take, healing the store from it.

        The store's good copy is copied into the takes directory when that holds none, after any copy
        there is moved aside. A whole good copy in the takes directory replaces a damaged one in the
        store, when the store's lock is free at once, because a run holding it leaves a good copy
        itself. A store that holds nothing is left empty. When every copy is damaged, the take is
        refused and nothing is moved.
        """
        self._keep(digest, witness, section=section, locked=False)

    def voice(
        self,
        digest: str,
        speak: Callable[[], tuple[bytes, Sequence[Word]]],
        charge: Callable[[], None],
        witness: Witness,
        *,
        section: int,
        replacing: bool,
    ) -> TakeOutcome:
        """Voice this take once on this machine, and write it to every place, or keep the copy another run left.

        Under the store's lock, a whole good copy in either place is kept unless `replacing`, which is
        whether this section's plan was told to replace its voiced take. Otherwise each place is proved
        writable, `speak` is asked, `charge` is called, and the pair is written to the store, unless it
        holds a good copy, then to the takes directory. A charge that raises still leaves the pair, and
        is raised after it.
        """
        if is_placeholder(digest):
            raise ValueError(
                f"take {digest} is a placeholder, which is made under the build and never voiced through the "
                "take places."
            )
        workspace = self._workspace
        with self._holding(digest, witness):
            store = self._judge(self._store, digest, whole=True) if self._store is not None else None
            takes = self._judge(workspace.takes, digest, whole=True)
            if not replacing and (takes.good or (store is not None and store.good)):
                self._keep(digest, witness, section=section, locked=True)
                return TakeOutcome.KEPT
            writes = store if store is not None and not store.good else None
            try:
                self._preflight(workspace.takes, digest)
            except OSError as failed:
                raise self._takes_unwritable(digest, failed) from failed
            if writes is not None:
                try:
                    self._preflight(writes.place, digest)
                except OSError as failed:
                    raise self._store_unwritable(digest, failed) from failed
            audio, words = speak()
            try:
                charge()
            finally:
                self._write(
                    digest, audio, words, witness, section=section, replacing=replacing, takes=takes, store=writes
                )
        return TakeOutcome.VOICED

    # ---- the places ---------------------------------------------------------------------------------

    @property
    def _store(self) -> Path | None:
        store = self._workspace.store
        return None if store is None or store == self._workspace.takes else store

    @property
    def _places(self) -> tuple[Path, ...]:
        return (self._workspace.takes, *([self._store] if self._store is not None else []))

    def _judge(self, place: Path, digest: str, *, whole: bool) -> _Copy:
        """This place's copy of a voiced take, judged by its size alone or by its whole BLAKE3."""
        words = place / words_file(digest)
        audio = place / take_file(digest, _recorded_suffix(words) or self._workspace.suffix)
        audio_there, words_there = audio.is_file(), words.is_file()
        fault: str | None = None
        if audio_there and words_there:
            fault = self._whole(audio, words) if whole else _pair_fault(audio, words)
        elif audio_there or words_there:
            fault = HALF_PAIR
        return _Copy(place, audio, words, audio_there or words_there, audio_there and words_there, fault)

    def _whole(self, audio: Path, words: Path) -> str | None:
        """What the whole BLAKE3 finds wrong with this pair, remembered while neither file changes."""
        try:
            before = _stamp(audio, words)
            with self._judging:
                if before in self._judged:
                    return self._judged[before]
            fault = _pair_fault(audio, words, whole=True)
            after = _stamp(audio, words)
        except OSError as failed:
            return f"{audio.name} could not be read ({failed.strerror or failed})."
        if after == before:
            with self._judging:
                self._judged[after] = fault
        return fault

    # ---- keep ---------------------------------------------------------------------------------------

    def _keep(self, digest: str, witness: Witness, *, section: int, locked: bool) -> None:
        """`keep`, which `voice` calls holding the store's lock already, so it never takes it again."""
        if is_placeholder(digest):
            return
        workspace = self._workspace
        takes = self._judge(workspace.takes, digest, whole=True)
        store = self._judge(self._store, digest, whole=True) if self._store is not None else None
        if takes.good:
            if store is not None and store.there and not store.good:
                self._heal(digest, takes, witness, locked=locked)
            return
        if store is not None and store.good:
            self._copy_in(digest, store, takes, witness)
            return
        damaged = next((copy for copy in (takes, store) if copy is not None and copy.there), None)
        if damaged is not None:
            raise _damaged(workspace, section, digest, damaged.place, damaged.why())

    def _copy_in(self, digest: str, store: _Copy, takes: _Copy, witness: Witness) -> None:
        """Copy the store's good pair into the takes directory, moving whatever of the pair is there aside."""
        home = self._workspace.takes
        pair: dict[Path, str | bytes] = {home / store.audio.name: store.audio.read_bytes()}
        pair[home / store.words.name] = store.words.read_bytes()
        moved = _replace_pair(pair, _aside_names(takes, store.audio.name))
        for path in pair:
            witness.wrote(path)
        if moved:
            witness.note(
                f"The copy of take {digest} in {self._shown_takes} is damaged: {takes.why()} It was moved aside as "
                f"{_names(moved)}, and the good copy in {store.place} was copied in its place.",
                level=Level.WARNING,
            )

    def _heal(self, digest: str, takes: _Copy, witness: Witness, *, locked: bool) -> None:
        """Replace the store's damaged copy with the takes directory's whole one, never waiting on its lock."""
        store = self._store
        assert store is not None
        if locked:
            self._healed(digest, store, takes, witness)
            return
        try:
            store.mkdir(parents=True, exist_ok=True)
            lock = FileLock(store / f"{digest}{STORE_LOCK_SUFFIX}")
            lock.acquire(timeout=0)
        except Timeout:  # silent: another run is voicing or healing this take, and it leaves a good copy
            return
        except OSError as failed:
            witness.note(self._not_healed(digest, failed), level=Level.WARNING)
            return
        try:
            self._healed(digest, store, takes, witness)
        finally:
            lock.release()

    def _healed(self, digest: str, store: Path, takes: _Copy, witness: Witness) -> None:
        """Judge the store's copy again under its lock, and when it is still damaged, replace it."""
        held = self._judge(store, digest, whole=True)
        if not held.there or held.good:
            return
        pair: dict[Path, str | bytes] = {store / takes.audio.name: takes.audio.read_bytes()}
        pair[store / takes.words.name] = takes.words.read_bytes()
        try:
            moved = _replace_pair(pair, _aside_names(held, takes.audio.name))
        except OSError as failed:
            witness.note(self._not_healed(digest, failed), level=Level.WARNING)
            return
        witness.note(
            f"The copy of take {digest} in the take store at {store} is damaged: {held.why()} It was moved aside as "
            f"{_names(moved)}, and the good copy in {self._shown_takes} was copied in its place.",
            level=Level.WARNING,
        )

    # ---- voice --------------------------------------------------------------------------------------

    @contextmanager
    def _holding(self, digest: str, witness: Witness) -> Iterator[None]:
        """Hold the store's lock on this take, waiting in short turns, and check the run once it is won.

        A run that finds the lock held asks its witness between turns, so a cancel stops the wait, and
        is refused once `wait_seconds` passes. A machine with no store holds no lock and checks nothing.
        """
        store = self._store
        if store is None:
            yield
            return
        try:
            store.mkdir(parents=True, exist_ok=True)
        except OSError as failed:
            raise self._store_unwritable(digest, failed) from failed
        lock = FileLock(store / f"{digest}{STORE_LOCK_SUFFIX}")
        deadline = time.monotonic() + self._wait_seconds
        while True:
            try:
                lock.acquire(timeout=STORE_POLL_SECONDS)
                break
            except Timeout:
                witness.check()
                if time.monotonic() > deadline:
                    raise ProjectLocked(
                        f"another run has been voicing take {digest} into the take store at {store} for longer "
                        f"than the {self._wait_seconds:g} seconds [narration] store_wait_seconds allows, so this "
                        "run voiced nothing.",
                        hint="Let the other run finish, or stop it, then run this again. If no run is voicing it, "
                        f"delete {store / (digest + STORE_LOCK_SUFFIX)}.",
                    ) from None
            except OSError as failed:
                raise self._store_unwritable(digest, failed) from failed
        try:
            witness.check()
            yield
        finally:
            lock.release()

    @staticmethod
    def _preflight(place: Path, digest: str) -> None:
        """Make `place` and create and remove one empty file there, which proves it can take the pair."""
        place.mkdir(parents=True, exist_ok=True)
        draft = place / f".{digest}.{secrets.token_hex(DRAFT_TOKEN_BYTES)}{PREFLIGHT_SUFFIX}"
        try:
            with draft.open("xb"):
                pass
        finally:
            draft.unlink(missing_ok=True)

    def _write(
        self,
        digest: str,
        audio: bytes,
        words: Sequence[Word],
        witness: Witness,
        *,
        section: int,
        replacing: bool,
        takes: _Copy,
        store: _Copy | None,
    ) -> None:
        """Write the voiced pair to the store when `store` is given, then to the takes directory, then say so."""
        workspace = self._workspace
        audio_name, words_name = workspace.take_file(digest), words_file(digest)
        text = ProviderWords(words=tuple(words), audio=AudioPrint.of(audio, suffix=workspace.suffix)).text

        def pair(place: Path) -> dict[Path, str | bytes]:
            # The words come last, so a run stopped while it writes never leaves a words file vouching for audio
            # that is not there.
            return {place / audio_name: audio, place / words_name: text}

        in_store: list[tuple[Path, Path]] = []
        stored, unstored = False, None
        if store is not None:
            try:
                in_store = _replace_pair(pair(store.place), _aside_names(store, audio_name))
                stored = True
            except OSError as failed:
                unstored = failed
        aside = () if takes.good else _aside_names(takes, audio_name)
        replaced = (takes.audio,) if takes.good and takes.audio.name != audio_name else ()
        try:
            in_takes = _replace_pair(pair(workspace.takes), aside, replaced=replaced)
        except OSError as failed:
            raise self._landed(digest, failed, section=section, replacing=replacing, stored=stored, takes=takes) from (
                failed
            )
        for path in pair(workspace.takes):
            witness.wrote(path)
        for copy, moved, where in (
            (store, in_store, f"the take store at {workspace.store}"),
            (takes, in_takes, self._shown_takes),
        ):
            if copy is not None and moved:
                witness.note(
                    f"The copy of take {digest} in {where} is damaged: {copy.why()} It was moved aside as "
                    f"{_names(moved)} before the take voiced again was written.",
                    level=Level.WARNING,
                )
        if unstored is not None:
            witness.note(
                f"Take {digest} could not be copied into the take store at {workspace.store} ({_reason(unstored)}). "
                f"It is safe in {self._shown_takes}.",
                level=Level.WARNING,
            )

    # ---- what is said -------------------------------------------------------------------------------

    @property
    def _shown_takes(self) -> str:
        return relative(self._workspace.takes, self._workspace.root).as_posix()

    def _landed(
        self, digest: str, failed: OSError, *, section: int, replacing: bool, stored: bool, takes: _Copy
    ) -> InputError:
        """The refusal for a voiced take the takes directory could not hold, worded from where its bytes landed."""
        workspace = self._workspace
        shown, reason = self._shown_takes, _reason(failed)
        location = at(workspace.takes, workspace.root, section=section)
        if stored and takes.good:
            return InputError(
                f"Take {digest} was voiced again and could not be written into {shown} ({reason}). The new take is "
                f"in the take store at {workspace.store}, and {shown} still holds the take it replaces, which the "
                "next run would play.",
                hint=f"Move {takes.audio.name} and {takes.words.name} out of {shown}, then run decktalk narrate "
                "again, and it copies the new take in for nothing.",
                location=location,
            )
        if stored:
            return InputError(
                f"Take {digest} was voiced and could not be written into {shown} ({reason}). A good copy of it is in "
                f"the take store at {workspace.store}, so the next run copies it in for nothing.",
                hint=f"Make {shown} a folder DeckTalk can write, then run decktalk narrate again.",
                location=location,
            )
        flag = " --replace-voiced" if replacing else ""
        return InputError(
            f"Take {digest} was voiced and could not be written into {shown} ({reason}), and no other place kept "
            "it, so any charge for it stands and only voicing it again gives it back.",
            hint=f"Make {shown} a folder DeckTalk can write, then run decktalk narrate --section {section}{flag} "
            "--spend to voice it again.",
            location=location,
        )

    def _takes_unwritable(self, digest: str, failed: OSError) -> InputError:
        shown = self._shown_takes
        return InputError(
            f"{shown} cannot be written ({_reason(failed)}), so take {digest} was not voiced and nothing was paid.",
            hint=f"Make {shown} a folder DeckTalk can write, then run decktalk narrate again.",
            location=at(self._workspace.takes, self._workspace.root),
        )

    def _store_unwritable(self, digest: str, failed: OSError) -> InputError:
        return InputError(
            f"[narration] store_dir is {self._workspace.store}, which this machine cannot write ({_reason(failed)}), "
            f"so take {digest} was not voiced and nothing was paid.",
            hint="Make that folder writable, or name another with decktalk config set narration.store_dir DIR "
            "--scope machine.",
        )

    def _not_healed(self, digest: str, failed: OSError) -> str:
        return (
            f"Take {digest} in the take store at {self._workspace.store} is damaged and could not be replaced "
            f"({_reason(failed)}), so another project that needs it is refused until a good copy replaces it."
        )


def _damaged(workspace: Workspace, section: int, digest: str, place: Path, fault: str) -> InputError:
    """The sentence a voiced take is refused with when no place holds a good copy and `place` holds a damaged one."""
    takes = relative(workspace.takes, workspace.root).as_posix()
    return InputError(
        f"{fault} No place holds a good copy of the take section {section} plays, and only voicing it again "
        "gives it back, so DeckTalk neither buys it again nor deletes it.",
        hint=f"Put a good copy of {workspace.take_file(digest)} and {words_file(digest)} in {takes}, or run "
        f"decktalk narrate --section {section} --replace-voiced --spend knowing that it buys the take again.",
        location=at(place / words_file(digest), workspace.root, section=section),
    )


def _pair_fault(audio: Path, words: Path, *, whole: bool = False) -> str | None:
    """What is wrong with a take and its words file, both on disk, in one sentence, or None when they agree.

    The words must read, and the audio must hold the number of bytes they recorded, which is cheap
    enough to ask on every lookup. `whole` also takes the audio's BLAKE3, which is what tells a
    swapped take of the same length, and which a run asks once per take and before every copy. A words
    file with no fingerprint vouches only that its audio is not empty.
    """
    try:
        said = ProviderWords.parse(words)
    except Unreadable as unread:
        return str(unread)
    size = audio.stat().st_size
    printed = said.audio if said is not None else None
    if printed is None:
        return None if size else f"{audio.name} is empty."
    if size != printed.bytes:
        return f"{audio.name} holds {size} bytes where {words.name} recorded {printed.bytes}."
    if whole and file_digest(audio) != printed.blake3:
        return f"{audio.name} does not hold the bytes {words.name} recorded."
    return None


def _recorded_suffix(words: Path) -> str | None:
    """The suffix a take's words file says its audio was written under, or None when it says none or cannot be read.

    A words file that cannot be read is judged by `_pair_fault`, which says what is wrong with it, so
    here it only means the take is looked for under the voice's own suffix.
    """
    try:
        said = ProviderWords.parse(words)
    except Unreadable:  # silent: _pair_fault reads the same file and says what is wrong with it
        return None
    return said.audio.suffix if said is not None and said.audio is not None else None


def _stamp(audio: Path, words: Path) -> tuple[object, ...]:
    """What tells a pair rewritten since it was judged: each file's path, inode, size and both change times."""
    seen = audio.stat()
    said = words.stat()
    return (
        audio,
        seen.st_ino,
        seen.st_size,
        seen.st_mtime_ns,
        seen.st_ctime_ns,
        words,
        said.st_ino,
        said.st_size,
        said.st_mtime_ns,
        said.st_ctime_ns,
    )


def _reason(failed: OSError) -> str:
    return failed.strerror or str(failed)


def _names(moved: list[tuple[Path, Path]]) -> str:
    return ", ".join(aside.name for _source, aside in moved)


def _aside_names(copy: _Copy, audio_name: str) -> tuple[str, ...]:
    """Every name of one pair a write into `copy.place` would replace, audio first and words last."""
    return tuple(dict.fromkeys((copy.audio.name, audio_name, copy.words.name)))


def _replace_pair(
    pair: dict[Path, str | bytes], aside: tuple[str, ...], *, replaced: tuple[Path, ...] = ()
) -> list[tuple[Path, Path]]:
    """Move `aside` out of the way, write `pair` with its words last, and give back what was moved where.

    A write that fails puts every moved file back under its own name and is raised, so a copy is set
    aside only when a good copy replaced it. `replaced` is a good copy's audio under another suffix,
    which the new pair replaces on request and which goes once the write is done.
    """
    place = next(iter(pair)).parent
    moved = _set_aside(place, aside) if aside else []
    try:
        replace_all(pair)
    except OSError:
        _put_back(moved)
        raise
    for gone in replaced:
        gone.unlink(missing_ok=True)
    return moved


def _set_aside(place: Path, names: tuple[str, ...]) -> list[tuple[Path, Path]]:
    """Move the files of one pair in `place` aside under one number, never overwriting a file.

    The pair takes `<name>.unreadable` when that is free for every name, else `<name>.<n>.unreadable`
    for the first `n` free for every name. Each name is reserved before its file moves onto it, so a
    file this call did not create is never replaced. A move that fails puts back what this call already
    moved and is raised. It gives back each file moved with where it went.
    """
    present = [place / name for name in names if (place / name).is_file()]
    number = 0
    while True:
        tag = f".{number}{UNREADABLE_SUFFIX}" if number else UNREADABLE_SUFFIX
        if not any(os.path.lexists(place / f"{name}{tag}") for name in names):
            moved = _moved_under(present, tag)
            if moved is not None:
                return moved
        number += 1


def _moved_under(present: list[Path], tag: str) -> list[tuple[Path, Path]] | None:
    """Move each file onto its name with `tag` added, or None when another file took one of those names first."""
    moved: list[tuple[Path, Path]] = []
    for source in present:
        aside = source.with_name(source.name + tag)
        try:
            os.close(os.open(aside, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:  # silent: that number is taken, so the next one is tried
            _put_back(moved)
            return None
        try:
            os.replace(source, aside)
        except OSError:
            aside.unlink(missing_ok=True)
            _put_back(moved)
            raise
        moved.append((source, aside))
    return moved


def _put_back(moved: list[tuple[Path, Path]]) -> None:
    """Move every file set aside back under its own name."""
    for source, aside in moved:
        os.replace(aside, source)


__all__ = ["TakeFiles", "TakePlaces", "Witness"]
