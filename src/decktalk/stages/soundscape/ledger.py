"""What this project has already bought from the sound service, as one typed file it reads and writes.

    build/soundscape/ledger.json   one row per generated item, keyed by the request that made it

Every request is paid for, so the one question this file answers is whether the audio on disk was
made from the request the project asks for now. The answer is a digest of the request body and the
endpoint it was sent to, and a row whose digest still matches is kept rather than bought again.

The ledger is one file for the whole soundscape rather than a cache file beside every output,
because the outputs may sit wherever `decktalk.toml` sends them and a run has to read the whole
record before it prices anything. It is a `Stored` model, so it is written under a temporary name
and renamed over the target in one step: a half-written ledger would read as a project that has
bought nothing, and the next run would buy every item in it again.

A file that is there and will not parse is refused rather than read as an empty record, for the
same reason. Deleting it is a decision about money, so it is the author's to take.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from decktalk.artifacts import Stored
from decktalk.findings import MODEL, ProjectPath
from decktalk.results import SoundKind

DIGEST_DIGITS = 16
"""Truth: sixteen hex characters of a sha256, which two requests of one project never collide within."""

LEDGER_FILE = "ledger.json"
"""What the record of bought audio is called, under the directory the soundscape is generated into."""

UNFINISHED_DIGEST = ""
"""What a row carries while the parts of one piece are still being bought, which matches no request.

A digest is the head of a sha256 and is never empty, so a row marked this way is always read as one
whose audio has still to be finished, and the parts beside it are still kept and never bought twice.
"""


def request_digest(endpoint: str, body: Mapping[str, Any]) -> str:
    """The digest of one request, which is the endpoint it goes to and the body it carries.

    The endpoint is part of it because the same body sent to the music service and to the sound
    service is two different pieces of audio, and the ledger is keyed by what was bought.
    """
    payload = f"{endpoint}\n{json.dumps(body, sort_keys=True)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DIGEST_DIGITS]


class SoundEntry(BaseModel):
    """One item this project has bought: what was asked for, what came back, and where it went."""

    model_config = MODEL

    name: str = Field(description="What the author calls this item in decktalk.toml.")
    kind: SoundKind = Field(description="Whether this item is music, ambience or an effect.")
    digest: str = Field(description="The digest of the request this file was made from.")
    file: ProjectPath = Field(description="The audio this request produced, project-relative.")
    seconds: float | None = Field(None, ge=0, description="How long that audio runs, or null when unmeasured.")
    parts: tuple[str, ...] = Field(
        (), description="The digest of each music part, in the order they are joined, and empty for a single request."
    )
    request: str = Field(description="The request body as compact JSON with its keys sorted, so a reader sees it.")


class Ledger(Stored):
    """Every item this project has bought, which is what tells a kept item from one to buy again."""

    items: tuple[SoundEntry, ...] = Field((), description="One row per generated item, in the order it was written.")

    def of(self, name: str) -> SoundEntry | None:
        """One item's row, or None when this project has never bought it."""
        return next((entry for entry in self.items if entry.name == name), None)

    def updated(self, entry: SoundEntry) -> Ledger:
        """This ledger with one row written over its own name, which is how a frozen record grows."""
        kept = tuple(row for row in self.items if row.name != entry.name)
        return Ledger(items=(*kept, entry))


__all__ = ["DIGEST_DIGITS", "LEDGER_FILE", "UNFINISHED_DIGEST", "Ledger", "SoundEntry", "request_digest"]
