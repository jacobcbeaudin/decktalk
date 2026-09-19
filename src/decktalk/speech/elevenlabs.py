"""ElevenLabs, the default speech provider: text read aloud with a time for every word.

The `/text-to-speech` endpoint returns the audio and a start and an end time per character, which
`words_from_alignment` groups into words, and that is the whole reason DeckTalk can cut on a word.
The same key buys the sound effects and the music that `decktalk soundscape` generates.

The key travels in a header to whatever host `[elevenlabs] api_base` names, so the base is checked
once when the provider is built, and the key is a `Secret` that only the header builder reveals.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..artifacts import Word
from ..errors import ConfigError
from ..secret import Secret
from ..settings import ElevenLabsConfig
from . import SpeechRequest, VoiceContext
from .http import post_bytes, post_json

PUNCT = "\"'“”‘’.,;:!?()[]—–-…"
ELEVENLABS_DOMAIN = "elevenlabs.io"
# Set this to let `[elevenlabs] api_base` name any host, for a local mock of the API. The
# environment is the user's own machine and a project file is not, so the file alone can never
# redirect the key. Any value but an empty string, `0`, `no` or `false` turns the check off.
ALLOW_ANY_API_BASE = "DECKTALK_ALLOW_ANY_API_BASE"


def check_api_base(api_base: str, environ: Mapping[str, str] | None = None) -> str:
    """`api_base` when it is an https URL on an ElevenLabs host or the override is set, and otherwise an error.

    The key travels in a header to whatever host `api_base` names, so the value is checked here,
    before the first request, wherever it came from.
    """
    env = os.environ if environ is None else environ
    if env.get(ALLOW_ANY_API_BASE, "").lower() not in ("false", "0", "no", ""):
        return api_base
    parts = urlsplit(api_base)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == ELEVENLABS_DOMAIN or host.endswith(f".{ELEVENLABS_DOMAIN}")):
        return api_base
    # The value is not quoted, because it may be set from the environment and reaches an error
    # that a --json payload carries, and only the switch that lifts this check may be printed.
    raise ConfigError(
        f"[elevenlabs] api_base must be an https URL on {ELEVENLABS_DOMAIN}. "
        f"Set {ALLOW_ANY_API_BASE}=1 to send the key to another host on purpose."
    )


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    """Group the character alignment into words. A <break .../> tag is skipped rather than spoken."""
    words: list[Word] = []
    current: list[tuple[str, float, float]] = []
    in_tag = False

    def flush() -> None:
        if not current:
            return
        clean = "".join(c for c, _, _ in current).strip(PUNCT)
        if clean:
            words.append(Word(word=clean, start=round(current[0][1], 3), end=round(current[-1][2], 3)))
        current.clear()

    for ch, start, end in zip(chars, starts, ends, strict=False):
        if in_tag:
            if ch == ">":
                in_tag = False
            continue
        if ch == "<":
            flush()
            in_tag = True
            continue
        if ch.isspace():
            flush()
            continue
        current.append((ch, start, end))
    flush()
    return words


@dataclass
class ElevenLabs:
    """Speech with word timestamps, sound effects and music. The default SpeechProvider.

    Both values this provider reads from `.env` are `Secret`s, so no log line, error, `repr` or
    JSON payload that reaches the provider can print the key or the voice id, and `http.redact`
    takes the voice id out of every URL and every message besides. The base URL is checked once,
    when the provider is built.
    """

    api_key: Secret
    cfg: ElevenLabsConfig
    voice: Secret = field(default_factory=lambda: Secret("", "ELEVENLABS_VOICE_ID"))
    context_chars: int = 1500
    timeout: int = 180
    name: str = "elevenlabs"
    api_base: str = field(init=False)

    def __post_init__(self) -> None:
        # Every URL is built from the base that passed the check, and never from the table again.
        object.__setattr__(self, "api_base", check_api_base(self.cfg.api_base).rstrip("/"))

    @classmethod
    def for_context(cls, context: VoiceContext) -> ElevenLabs:
        """The provider one project asks for: its key, its voice and its narration settings."""
        api_key, voice = context.secrets.require("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
        narration = context.settings.narration
        return cls(
            api_key,
            context.settings.elevenlabs,
            voice=voice,
            context_chars=narration.context_chars,
            timeout=narration.timeout_seconds,
        )

    def cache_key(self, request: SpeechRequest) -> str:
        """Everything but the text that changes the audio. The voice id is part of the take hash."""
        return f"{self.name}\n{self.voice.reveal()}\n{request.model}\n{request.output_format}"

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        return self.synthesize(
            request.text,
            voice_id=self.voice.reveal(),
            model=request.model,
            voice_settings=request.voice_settings,
            output_format=request.output_format,
            previous_text=request.previous_text,
            next_text=request.next_text,
            context_chars=self.context_chars,
            timeout=self.timeout,
        )

    def _headers(self) -> dict[str, str]:
        """The one place the key is revealed, which is the request that is allowed to carry it."""
        return {"xi-api-key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        model: str,
        voice_settings: dict[str, Any],
        output_format: str,
        previous_text: str | None = None,
        next_text: str | None = None,
        context_chars: int = 1500,
        timeout: int = 180,
    ) -> tuple[bytes, list[Word]]:
        """One section read aloud, returned as the mp3 bytes and a start and an end time per word."""
        url = f"{self.api_base}/text-to-speech/{voice_id}/with-timestamps?output_format={output_format}"
        payload: dict[str, Any] = {"text": text, "model_id": model, "voice_settings": voice_settings}
        if previous_text:
            payload["previous_text"] = previous_text[-context_chars:]
        if next_text:
            payload["next_text"] = next_text[:context_chars]
        reply = post_json(url, payload, self._headers(), timeout=timeout)
        audio = base64.b64decode(reply["audio_base64"])
        alignment = reply.get("alignment") or reply.get("normalized_alignment") or {}
        words = words_from_alignment(
            alignment.get("characters", []),
            alignment.get("character_start_times_seconds", []),
            alignment.get("character_end_times_seconds", []),
        )
        return audio, words

    def sound_effect(self, body: dict[str, Any], *, output_format: str) -> bytes:
        url = f"{self.api_base}/sound-generation?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.cfg.timeout_seconds)

    def music(self, body: dict[str, Any], *, output_format: str) -> bytes:
        url = f"{self.api_base}/music?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.cfg.timeout_seconds)
