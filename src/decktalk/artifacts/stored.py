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

A stage that needs an artifact as its input refuses one that will not parse as one that was never
built. The hint names the stage that writes it, read from `PIPELINE` through `Artifact.next_step`,
so no stage spells a "run this first" sentence of its own. A refusal names the file it looked for
rather than the artifact's default path, because a project may move its build directory.

A stage that reads its own earlier output reads it through `previous`, which counts a file it cannot
read as absent. `build/` is DeckTalk's cache and that stage is about to write the file again, so an
engine that changed the file's shape builds it again rather than asking a person to delete it. A
paid record says so with `paid`, and one that does not read is refused with a sentence by `read`
and `previous` alike and left where it is, because counting it as absent would buy what it records
again, and that is a decision about money a person takes.

Every fingerprint of a file's content is `file_digest`, and every fingerprint of content held in
memory is `content_digest`, which are one hash: BLAKE3. The files a build fingerprints are the
recordings, the section cuts and the film, which grow with the film, and BLAKE3 spreads one large
file across every core where SHA-256 reads it on one. The keys taken over those fingerprints stay
the SHA-256 of `engine_digest`, because a key is a few lines of text, and the paid voice takes keep
the SHA-256 their names are published as, because a changed take name would buy the take again.
"""

from __future__ import annotations

import hashlib
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import ClassVar, Self

from blake3 import blake3
from pydantic import ValidationError

from decktalk.errors import InputError, NotBuiltError
from decktalk.files import replace_all
from decktalk.findings import Model
from decktalk.pipeline import Artifact

log = logging.getLogger(__name__)

INDENT = 2
"""How the artifacts are indented, which keeps a diff of one readable in a terminal."""

DIGEST_BYTES = 8
"""How much of a BLAKE3 names some content, sixteen hex characters, which never collide within one project."""

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


class Stored(Model):
    """One file under `build/`, which knows how to read itself and how to write itself."""

    paid: ClassVar[bool] = False
    """True on a record of something bought, which is refused rather than built again when it does not read."""

    @classmethod
    def read(cls, path: Path) -> Self | None:
        """The artifact at `path`, or None when nothing has written one there yet.

        A file that is there and cannot be read as this shape is refused. A cache file is refused as
        `NOT_BUILT`, which its writer builds again. A paid record is refused as `INPUT`, with a sentence
        that says what reading it again would cost, and nothing here deletes it.
        """
        if not path.is_file():
            return None
        try:
            return cls.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError, OSError) as exc:
            unread = f"{path.name} is there and cannot be read as {cls.__name__.lower()} ({_first_line(exc)})."
            if cls.paid:
                raise InputError(
                    f"{unread} It records what this project paid for, so DeckTalk neither builds it again "
                    "nor deletes it.",
                    hint=f"Run the DeckTalk release that wrote {path.name}, or move it aside knowing that the "
                    "next run that may spend buys again everything it records.",
                ) from exc
            raise NotBuiltError(unread, hint=f"Delete {path.name} and build it again.") from exc

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

    @classmethod
    def require(cls, path: Path, artifact: Artifact) -> Self:
        """The artifact at `path`, or a `NOT_BUILT` refusal naming the stage that writes it."""
        found = cls.read(path)
        if found is None:
            raise NotBuiltError(f"{path.name} has not been built.", hint=artifact.next_step)
        return found

    def write(self, path: Path) -> Path:
        """Write this artifact over `path` in one step, and give back the path it was written to."""
        text = json.dumps(self.model_dump(mode="json"), indent=INDENT, allow_nan=False)
        replace_all({path: text + "\n"})
        return path


def _first_line(error: Exception) -> str:
    """The first line of a parser's complaint, because a reader relays this to a person."""
    return next((line.strip() for line in str(error).splitlines() if line.strip()), type(error).__name__)


__all__ = ["ENGINE_VERSION", "GONE", "Stored", "content_digest", "engine_digest", "engine_version", "file_digest"]
