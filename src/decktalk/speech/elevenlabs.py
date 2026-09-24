"""ElevenLabs, the only speech provider: text read aloud with a time for every word.

The `/text-to-speech` endpoint returns the audio and a start and an end time per character, which
`words_from_alignment` groups into words, and that is the whole reason DeckTalk can cut on a word.
The same key buys the sound effects and the music that the soundscape generates.

The key travels in a header to whatever host `[elevenlabs] api_base` names, so the base is checked
once when the provider is built, and the key is a `Secret` that only the header builder reveals. The
voice id is not a secret: it names which voice reads the script, the way a model name names which
model does, and it arrives in the request rather than being read from the environment here.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..errors import InputError, ProviderError
from ..results import Word
from ..secret import Secret
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
    raise InputError(
        f"[elevenlabs] api_base must be an https URL on {ELEVENLABS_DOMAIN}.",
        hint=f"Set {ALLOW_ANY_API_BASE}=1 to send the key to another host on purpose.",
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
    """Speech with word timestamps, sound effects and music, which is the one provider DeckTalk ships.

    The key is a `Secret`, so no log line, error, `repr` or JSON payload that reaches this provider
    can print it, and `_headers` is the one place it is revealed. The base URL is checked once, when
    the provider is built.
    """

    api_key: Secret
    api_base: str
    context_chars: int  # [narration] context_chars
    speech_timeout_seconds: int  # [narration] timeout_seconds
    sound_timeout_seconds: int  # [elevenlabs] timeout_seconds
    name: str = "elevenlabs"
    checked_base: str = field(init=False)

    def __post_init__(self) -> None:
        # Every URL is built from the base that passed the check, and never from the setting again.
        object.__setattr__(self, "checked_base", check_api_base(self.api_base).rstrip("/"))

    @classmethod
    def for_context(cls, context: VoiceContext) -> ElevenLabs:
        """The provider one project asks for: its key, and the tuning that shapes its requests."""
        (api_key,) = context.secrets.require("ELEVENLABS_API_KEY")
        return cls(
            api_key=api_key,
            api_base=context.api_base,
            context_chars=context.context_chars,
            speech_timeout_seconds=context.speech_timeout_seconds,
            sound_timeout_seconds=context.sound_timeout_seconds,
        )

    def cache_key(self, request: SpeechRequest) -> str:
        """Everything but the text that changes the audio. The voice id is part of the take hash."""
        return f"{self.name}\n{request.voice_id}\n{request.model}\n{request.output_format}"

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        return self.synthesize(request)

    def _headers(self) -> dict[str, str]:
        """The one place the key is revealed, which is the request that is allowed to carry it."""
        return {"xi-api-key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def synthesize(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        """One section read aloud, as the mp3 bytes and a start and an end time per word.

        The neighbouring sections travel with the request so the voice carries its prosody across a
        cut, trimmed to the characters the tuning allows, and the alignment that comes back is what
        every later stage measures against.
        """
        url = f"{self.checked_base}/text-to-speech/{request.voice_id}/with-timestamps"
        payload: dict[str, Any] = {
            "text": request.text,
            "model_id": request.model,
            "voice_settings": request.voice_settings,
        }
        if request.previous_text:
            payload["previous_text"] = request.previous_text[-self.context_chars :]
        if request.next_text:
            payload["next_text"] = request.next_text[: self.context_chars]
        reply = post_json(
            f"{url}?output_format={request.output_format}",
            payload,
            self._headers(),
            timeout=self.speech_timeout_seconds,
        )
        return self._audio(reply), self._words(reply)

    def _audio(self, reply: Mapping[str, Any]) -> bytes:
        """The mp3 the reply carries, refused as a provider failure when it carries none.

        A reply with no audio in it is the service answering something other than speech, and
        letting it through would write an empty take that every later stage measures as silence.
        """
        encoded = reply.get("audio_base64")
        if not isinstance(encoded, str) or not encoded:
            raise ProviderError("the voice answered with no audio in it.", retryable=True)
        return base64.b64decode(encoded)

    def _words(self, reply: Mapping[str, Any]) -> list[Word]:
        """The word times the reply carries, which is what the whole cut is measured against.

        The normalised alignment is the fallback, because a service that could not align the text as
        written still aligned what it spoke.
        """
        alignment = reply.get("alignment") or reply.get("normalized_alignment") or {}
        return words_from_alignment(
            alignment.get("characters", []),
            alignment.get("character_start_times_seconds", []),
            alignment.get("character_end_times_seconds", []),
        )

    def sound_effect(self, body: dict[str, Any], *, output_format: str) -> bytes:
        url = f"{self.checked_base}/sound-generation?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.sound_timeout_seconds)

    def music(self, body: dict[str, Any], *, output_format: str) -> bytes:
        url = f"{self.checked_base}/music?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.sound_timeout_seconds)
