"""ElevenLabs sound effects and music, which is the one sound provider DeckTalk ships.

It is the same service, key and `api_base` as the ElevenLabs voice, and a second adapter in the
sound table rather than a method of the voice, so the score never needs a voice to buy a sound.
The key is a `Secret` that only `_headers` reveals, and the base is checked once, when the provider
is built, by the same rule the voice is checked by.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from ...results import SoundKind
from ...secret import Secret
from ..elevenlabs import NAME, checked
from ..http import post_bytes
from . import SOUND_DECLARED, SoundContext

PUBLISHED_BASE = "https://api.elevenlabs.io/v1"
"""Truth: the base the service publishes its API under, which names a sound's endpoint in its ledger digest.

A request goes to whatever base `[elevenlabs] api_base` names, and the digest names the published
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


@dataclass
class ElevenLabsSound:
    """Sound effects and music from ElevenLabs, built from a `SoundContext` and never from a voice."""

    context: SoundContext
    api_key: Secret
    name: str = NAME
    checked_base: str = field(init=False)

    def __post_init__(self) -> None:
        # Every URL is built from the base that passed the check, and never from the setting again.
        self.checked_base = checked(self.context.api_base, allow_any=self.context.allow_any_api_base)

    @classmethod
    def for_context(cls, context: SoundContext) -> ElevenLabsSound:
        """The provider one project asks for: the service's key, and the timeout and retries it sends with."""
        (api_key,) = context.secrets.require(cast("str", SOUND_DECLARED[NAME].key_variable))
        return cls(context, api_key)

    def _headers(self) -> dict[str, str]:
        """The one place the key is revealed, which is the request that is allowed to carry it."""
        return {"xi-api-key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
        """One sound effect or ambience bed, bought from `SOUND_PATH`."""
        return self._buy(SOUND_PATH, body, output_format)

    def music(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
        """One piece of music, or one part of a longer one, bought from `MUSIC_PATH`."""
        return self._buy(MUSIC_PATH, body, output_format)

    def _buy(self, path: str, body: Mapping[str, Any], output_format: str) -> bytes:
        return post_bytes(
            f"{self.checked_base}{path}?output_format={output_format}",
            dict(body),
            self._headers(),
            secrets=(self.api_key,),
            timeout=self.context.timeout_seconds,
            retries=self.context.retries,
        )
