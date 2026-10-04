"""A build artifact as a file: one frozen model per file, read once and written atomically.

Every file under `build/` that a later stage or the page runtime reads is one of these. The model
is the file's shape, so the field names are the JSON keys and a reader holds members rather than
comparing strings, and the same class both reads and writes, so no two places can disagree about
what a file holds. The shape is DeckTalk's own and not a promise: `build/` is a cache, and the
contract a caller builds on is the `--json` results, the events file, `build/final/` and the exit
codes.

A file is written under a temporary name in the same directory and renamed over the target, which
is atomic on every platform DeckTalk ships on, so a reader never opens a half-written artifact and
a run interrupted mid-write leaves the previous file whole.

A stage that needs the take index reads it through `Inputs.takes(required=True)`, which refuses an
index that is absent or does not read as never built and names the stage that writes it, through
`Artifact.next_step`.

A stage that reads its own earlier output reads it through `previous`, which counts a file it cannot
read as absent. `build/` is DeckTalk's cache and that stage is about to write the file again, so an
engine that changed the file's shape builds it again rather than asking a person to delete it. A
paid record says so with `paid`, and one that does not read is refused with a sentence by `read`
and `previous` alike and left where it is, because counting it as absent would buy what it records
again, and that is a decision about money a person takes.

Every fingerprint of a file's content is `file_digest`, and every fingerprint of content held in
memory is `content_digest`, which are one hash: BLAKE3. The files a build fingerprints are the
recordings, the section videos and the film, which grow with the film, and BLAKE3 spreads one large
file across every core where SHA-256 reads it on one. The keys taken over those fingerprints stay
the SHA-256 of `engine_digest`, because a key is a few lines of text, and voiced takes keep the
SHA-256 their names are published as, because a changed take name would buy the take again.
"""

from __future__ import annotations

import hashlib
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import ClassVar, Self

from blake3 import blake3
from pydantic import PrivateAttr, ValidationError

from decktalk.errors import InputError, NotBuiltError
from decktalk.files import replace_all
from decktalk.findings import Model

log = logging.getLogger(__name__)

DIGEST_BYTES = 8
"""How much of a BLAKE3 names some content, sixteen hex characters, which never collide within one project."""

DIGEST_DIGITS = 2 * DIGEST_BYTES
"""Derived: the hex characters of a key cut from a sha256, which is as long as a content digest and as safe."""

THREADED_BYTES = 1 << 20
"""Measured: below a mebibyte, handing a file to several threads costs more than hashing it on one does."""

GONE = "gone"
"""What a file that is not on disk is digested as, so a key over a missing file still names it."""

UNINSTALLED = "0+unknown"
"""The engine version a checkout that was never installed reports, which is still one fixed name."""


def engine_version() -> str:
    """The version of the engine writing the artifacts, which a cache key carries.

    An artifact kept across an upgrade was made by the older engine's recorder, probe and encoder, so
    every key that decides whether to keep one names the engine that would keep it.
    """
    try:
        return version("decktalk")
    except PackageNotFoundError:
        # silent: an engine run from a checkout has no installed version to read.
        return UNINSTALLED


ENGINE_VERSION = engine_version()
"""The version of the engine this process runs, read once, because it cannot change under a run."""


def engine_digest(*lines: str) -> str:
    """The sha256 of these lines under the engine's own, which is how every kept artifact is keyed."""
    return hashlib.sha256("\n".join([f"engine:{ENGINE_VERSION}", *lines]).encode("utf-8")).hexdigest()


def content_digest(data: bytes) -> str:
    """The BLAKE3 of these bytes, which is how content held in memory joins a key."""
    return blake3(data).hexdigest(length=DIGEST_BYTES)


def file_digest(path: Path) -> str:
    """The BLAKE3 of a file's bytes, or `gone` when there is no file there.

    The file is mapped rather than read, and a large one is hashed on every core, which is what keeps
    a film's worth of recordings from costing a build more time the longer the film runs.
    """
    if not path.is_file():
        return GONE
    threads = blake3.AUTO if path.stat().st_size >= THREADED_BYTES else 1
    return blake3(max_threads=threads).update_mmap(path).hexdigest(length=DIGEST_BYTES)


class Unreadable(Exception):
    """A file that is there and does not read as its model, which says the file, its role and the fault.

    It is no refusal by itself, because what a bad file costs depends on who wrote it, and the reader
    that knows turns it into one.
    """


class UnreadablePaidRecord(InputError):
    """A paid record that is there and does not read, refused as `INPUT` and never built again or deleted.

    It carries the file, so a reader that only asks about a section it is not making, such as whether
    a recording still stands, can say which file it could not read and go on rather than refuse.
    """

    def __init__(self, message: str, *, path: Path, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.path = path
        """The paid record that does not read."""


class UnreadableCache(NotBuiltError):
    """A cache file that is there and does not read, refused as `NOT_BUILT`, which its writer builds again.

    It carries the file, as `UnreadablePaidRecord` does, so a reader that only asks about a section it
    is not making can say which file it could not read and go on rather than refuse.
    """

    def __init__(self, message: str, *, path: Path, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.path = path
        """The cache file that does not read."""


class Stored(Model):
    """One file under `build/`, which knows how to read itself and how to write itself."""

    label: ClassVar[str] = "a DeckTalk file"
    """What this file is, in the words a refusal names it by, such as "the take index".

    Every model names its own, so a person reads the file's role and never a class name.
    """
    paid: ClassVar[bool] = False
    """True on a record only remaking can replace, which costs money on a paid provider.

    Such a record is refused rather than built again when it does not read.
    """
    regained: ClassVar[str] = ""
    """What alone gives a paid record back, as its refusal says it, such as voicing its take again."""

    _view: bool = PrivateAttr(default=False)
    """True on a copy changed for a reader, such as words on their section's clock, which no writer puts on disk."""

    @classmethod
    def read(cls, path: Path) -> Self | None:
        """The artifact at `path`, or None when nothing has written one there yet.

        A file that is there and cannot be read as this shape is refused. A cache file is refused as
        `NOT_BUILT` with `UnreadableCache`, which its writer builds again. A paid record is refused as `INPUT` with
        `UnreadablePaidRecord`, whose sentence says what alone gives it back, and nothing here deletes it.
        """
        try:
            return cls.parse(path)
        except Unreadable as exc:
            if cls.paid:
                raise UnreadablePaidRecord(
                    f"{exc} {cls.regained[:1].upper()}{cls.regained[1:]}, so DeckTalk neither builds it "
                    "again nor deletes it.",
                    hint=f"Run the DeckTalk release that wrote {path.name}, or move it aside knowing that "
                    "making again what it records costs money on a paid provider.",
                    path=path,
                ) from exc
            raise UnreadableCache(str(exc), hint=f"Delete {path.name} and build it again.", path=path) from exc

    @classmethod
    def parse(cls, path: Path) -> Self | None:
        """The model at `path`, or None when there is no file there, with no say about what a bad one costs.

        A file that does not read raises `Unreadable`, which names the file, this model's label and the
        fault. `read` decides the cost for a file under `build/`, and a reader of a file the author names
        refuses it as the author's own input instead.
        """
        if not path.is_file():
            return None
        try:
            return cls.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError, OSError) as exc:
            raise Unreadable(f"{path.name} is there and cannot be read as {cls.label}: {_why_unread(exc)}.") from exc

    @classmethod
    def previous(cls, path: Path) -> Self | None:
        """What the stage that writes `path` wrote there last, or None when there is none it can read.

        Only the writer reads its own file this way, so a cache file it cannot read is built again, and
        the record this leaves is how a person learns why a kept section was made again. A paid record
        it cannot read is refused as `read` refuses it, because building it again would buy it again.
        """
        try:
            return cls.read(path)
        except NotBuiltError as exc:
            log.info("%s It will be built again.", exc, extra={"data": {"file": path.name}})
            return None

    def __eq__(self, other: object) -> bool:
        """Two artifacts are equal when their fields are, whether or not either is a copy made for a reader."""
        if not isinstance(other, Stored):
            return NotImplemented
        return type(self) is type(other) and self.__dict__ == other.__dict__

    def write(self, path: Path) -> Path:
        """Write this artifact over `path` in one step, and give back the path it was written to."""
        replace_all({path: self.text})
        return path

    @property
    def text(self) -> str:
        """This artifact as the file holds it, for a writer that replaces it together with another file."""
        if self._view:
            raise ValueError(f"this copy of {self.label} was changed for a reader, so it is never written to a file.")
        return json.dumps(self.model_dump(mode="json"), indent=2, allow_nan=False) + "\n"


_WRONG = {
    "json_invalid": "is not JSON",
    "json_type": "is not JSON",
    "missing": "is missing",
    "extra_forbidden": "is not a field it has",
    "model_type": "is not a JSON object",
    "model_attributes_type": "is not a JSON object",
    "dict_type": "is not a JSON object",
    "int_type": "is not a whole number",
    "int_parsing": "is not a whole number",
    "int_from_float": "is not a whole number",
    "float_type": "is not a number",
    "float_parsing": "is not a number",
    "string_type": "is not text",
    "bool_type": "is not true or false",
    "bool_parsing": "is not true or false",
    "list_type": "is not a list",
    "tuple_type": "is not a list",
    "enum": "is not one of the values it may take",
    "literal_error": "is not one of the values it may take",
}
"""What a parser's error type says is wrong, in the words a refusal relays to a person."""


def _why_unread(error: Exception) -> str:
    """What is wrong with a file that did not read, in plain words: the field and its fault, or not JSON.

    Only the first fault is named, because one fixed field is how a person learns the next one.
    """
    if isinstance(error, ValidationError) and error.errors():
        first = error.errors()[0]
        place = _place(first["loc"]) if first["type"] not in ("json_invalid", "json_type") else ""
        wrong = _WRONG.get(first["type"])
        if wrong is None:
            wrong = f"is wrong: {first['msg'][:1].lower()}{first['msg'][1:]}"
        return f"{place or 'it'} {wrong}"
    if isinstance(error, OSError):
        return f"it could not be opened ({error.strerror or type(error).__name__})"
    return f"it is wrong: {error}"


def _place(loc: tuple[int | str, ...]) -> str:
    """A field's path as a person writes it, such as `words[1].start`, or nothing for the file itself."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f"{'.' if out else ''}{part}"
    return out


__all__ = [
    "DIGEST_DIGITS",
    "ENGINE_VERSION",
    "GONE",
    "Stored",
    "Unreadable",
    "content_digest",
    "engine_digest",
    "engine_version",
    "file_digest",
]
