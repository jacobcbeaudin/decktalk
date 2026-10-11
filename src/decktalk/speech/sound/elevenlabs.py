"""ElevenLabs sound effects and music, which is the one sound provider DeckTalk ships.

It is the same service, key and `base_url` as the ElevenLabs voice, and a second adapter in the
sound table rather than a method of the voice, so the score never needs a voice to buy a sound.
The key is a `Secret` that only `_headers` reveals, and the base URL is the machine's alone to set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ...results import SoundKind
from ...secret import Secret
from ..elevenlabs import NAME
from ..http import send
from . import SOUND_DECLARED, SoundContext, SoundRequest

PUBLISHED_BASE = "https://api.elevenlabs.io/v1"
"""Truth: the base the service publishes its API under, which names a sound's endpoint in its ledger digest.

A request goes to whatever base `[elevenlabs] base_url` names, and the digest names the published
one whatever that is, so moving to another host of the same service buys no sound again.
"""

SOUND_PATH = "/sound-generation"
"""Where a sound effect or an ambience bed is asked for on the service."""

MUSIC_PATH = "/music"
"""Where a piece of music is asked for on the service."""


def sound_path(kind: SoundKind) -> str:
    """Where this kind of sound is asked for, which is the music endpoint for music and the sound one otherwise."""
    return MUSIC_PATH if kind is SoundKind.MUSIC else SOUND_PATH


def sound_endpoint(kind: SoundKind) -> str:
    """The endpoint this kind of sound is bought from as the service publishes it, which a ledger digest names."""
    return PUBLISHED_BASE + sound_path(kind)


MILLISECONDS = 1000
"""Truth: milliseconds in a second, which is the unit the music endpoint reads a length in."""


def sound_body(request: SoundRequest) -> dict[str, Any]:
    """The JSON the service reads for one request, which is what a ledger digest is taken over.

    Music is asked for by its length in whole milliseconds, and a part of a longer piece is told
    which part it is, which is what keeps the key and the tempo. An effect or a bed is asked for by
    its seconds, and a bed that loops says so.
    """
    if request.kind is SoundKind.MUSIC:
        prompt = request.prompt
        if request.parts > 1:
            prompt += f" (part {request.part} of {request.parts}, same key and tempo, seamless mood)"
        return {
            "prompt": prompt,
            "force_instrumental": request.instrumental,
            "model_id": request.model,
            "music_length_ms": int(request.seconds * MILLISECONDS),
        }
    body: dict[str, Any] = {
        "text": request.prompt,
        "duration_seconds": request.seconds,
        "prompt_influence": request.influence,
        "model_id": request.model,
    }
    if request.loop:
        body["loop"] = True
    return body


@dataclass
class ElevenLabsSound:
    """Sound effects and music from ElevenLabs, built from a `SoundContext` and never from a voice."""

    context: SoundContext
    api_key: Secret
    name: str = NAME

    @classmethod
    def for_context(cls, context: SoundContext) -> ElevenLabsSound:
        """The provider one project asks for: the service's key, and the timeout and retries it sends with."""
        (api_key,) = context.secrets.require(cast("str", SOUND_DECLARED[NAME].key_variable))
        return cls(context, api_key)

    def _headers(self) -> dict[str, str]:
        """The one place the key is revealed, which is the request that is allowed to carry it."""
        return {"xi-api-key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def effect(self, request: SoundRequest, *, output_format: str) -> bytes:
        """One sound effect or ambience bed, bought from `SOUND_PATH`."""
        return self._buy(SOUND_PATH, request, output_format)

    def music(self, request: SoundRequest, *, output_format: str) -> bytes:
        """One piece of music, or one part of a longer one, bought from `MUSIC_PATH`."""
        return self._buy(MUSIC_PATH, request, output_format)

    def _buy(self, path: str, request: SoundRequest, output_format: str) -> bytes:
        return send(
            f"{self.context.base_url.rstrip('/')}{path}?output_format={output_format}",
            sound_body(request),
            self._headers(),
            parse=bytes,
            secrets=(self.api_key,),
            timeout=self.context.timeout_seconds,
            retries=self.context.retries,
        )
