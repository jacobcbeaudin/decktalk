"""Frozen frames kept by what drew them, so one state of a page is drawn once whoever asks for it.

    build/stills/<key>.png    one frozen frame, named by everything that decides how it looks
    build/stills/<key>.json   the project files the page had loaded when it was drawn, with their digests

`check` freezes the states either side of every cue, `storyboard` freezes the same states to lay them
out, and `assemble` freezes the opening slide as the poster. Each of them draws a frame from a URL,
a frame size, a colour scheme, a motion setting and the files the page loads, so a frame drawn once
from those is the frame every later call would draw again. The key is taken over everything the
caller knows before the page is opened, and the manifest beside the frame names every file the page
went on to load, because a picture or a module the page reached for is part of the picture as well.

A frame is kept only when its key matches and every file its manifest names still has the digest it
had, which is the same rule the recorder trusts for a whole section.

Every edit to a page gives its frames new keys, so the store would grow for as long as a project is
worked on. A frame that is found is touched, and a frame nobody has asked for in `IDLE_SECONDS` is
removed whenever another is kept, which bounds the store without any caller knowing what another
caller still needs. A frame removed too early costs one redraw and is never wrong.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from decktalk.artifacts.recordings import HASH_DIGITS
from decktalk.artifacts.stored import Stored, engine_digest, file_digest
from decktalk.errors import NotBuiltError
from decktalk.logs import cache_decision

log = logging.getLogger(__name__)

IMAGE_SUFFIX = ".png"
"""What a kept frame is called after its key, which is the lossless format every frozen frame is drawn in."""

MANIFEST_SUFFIX = ".json"
"""What the manifest beside a kept frame is called after its key."""

IDLE_SECONDS = 14 * 24 * 60 * 60
"""Calibration: a fortnight, which outlives any pause in work on one deck and still bounds the store."""


def still_key(parts: Sequence[str]) -> str:
    """The name of one frozen frame, from everything that decides how it looks and the engine that drew it."""
    return engine_digest(*parts)[:HASH_DIGITS]


class StillManifest(Stored):
    """The project files a page had loaded when one frame of it was drawn, each with its digest then."""

    files: dict[str, str] = Field(description="Each project-relative file against the digest it had when drawn.")


@dataclass(frozen=True)
class Stills:
    """The frames one project keeps, and the rule that says whether one of them is still current."""

    directory: Path
    root: Path

    def image(self, key: str) -> Path:
        return self.directory / f"{key}{IMAGE_SUFFIX}"

    def manifest(self, key: str) -> Path:
        return self.directory / f"{key}{MANIFEST_SUFFIX}"

    def find(self, key: str) -> Path | None:
        """The kept frame with this key, or None when there is none or a file it was drawn from has moved.

        Each answer is recorded with the reason for it, and a manifest that would not parse is told
        apart from one that is not there, although both mean the frame is drawn again.
        """
        found, why = self._found(key)
        cache_decision(log, "still", hit=found is not None, why=why, key=key)
        return found

    def _found(self, key: str) -> tuple[Path | None, str]:
        image = self.image(key)
        if not image.is_file():
            return None, "no-image"
        try:
            kept = StillManifest.read(self.manifest(key))
        except NotBuiltError:
            # silent: the reason is returned and recorded as the cache decision.
            return None, "manifest-unreadable"
        if kept is None:
            return None, "no-manifest"
        if any(file_digest(self.root / name) != digest for name, digest in kept.files.items()):
            return None, "source-changed"
        os.utime(image)
        return image, "unchanged"

    def keep(self, key: str, drawn: Path, loaded: Sequence[str]) -> Path:
        """Keep one frame just drawn under its key, with the files the page had loaded to draw it.

        The manifest is removed first and written last, so a run stopped between the two leaves a
        frame no later run will trust.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest(key).unlink(missing_ok=True)
        shutil.copyfile(drawn, self.image(key))
        files = {name: file_digest(self.root / name) for name in dict.fromkeys(loaded)}
        StillManifest(files=files).write(self.manifest(key))
        self._prune()
        return self.image(key)

    def _prune(self) -> None:
        """Remove every frame nobody has found or kept for `IDLE_SECONDS`, its manifest first."""
        idle = time.time() - IDLE_SECONDS
        for image in self.directory.glob(f"*{IMAGE_SUFFIX}"):
            if image.stat().st_mtime < idle:
                self.manifest(image.stem).unlink(missing_ok=True)
                image.unlink(missing_ok=True)


__all__ = ["IMAGE_SUFFIX", "MANIFEST_SUFFIX", "StillManifest", "Stills", "still_key"]
