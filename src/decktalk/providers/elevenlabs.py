"""ElevenLabs: text to speech with word timestamps, sound effects, and music."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..artifacts import Word
from ..errors import ConfigError
from ..project import Project
from ..settings import ElevenLabsConfig
from ._http import post_bytes, post_json
from .speech import SpeechRequest, register_speech_provider

PUNCT = "\"'“”‘’.,;:!?()[]—–-…"
ELEVENLABS_DOMAIN = "elevenlabs.io"
# Set to 1 to let `[elevenlabs] api_base` name any host, for a local mock of the API. The
# environment is the user's own machine and a project file is not, so the file alone can never
# redirect the key. The value is read the way _env.py reads a bool.
ALLOW_ANY_API_BASE = "DECKTALK_ALLOW_ANY_API_BASE"


def check_api_base(api_base: str, environ: Mapping[str, str] | None = None) -> str:
    """`api_base` when it is an https URL on an ElevenLabs host, or the override is set; else a ConfigError.

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
    raise ConfigError(
        f"[elevenlabs] api_base must be an https URL on {ELEVENLABS_DOMAIN}, not {api_base!r}. "
        f"Set {ALLOW_ANY_API_BASE}=1 to send the key to another host on purpose."
    )


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    """Group the character alignment into words; <break .../> tags are skipped."""
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

    The key is kept out of repr, so no log line, error or JSON payload that shows the provider
    shows it. The base URL is checked once, when the provider is built.
    """

    api_key: str = field(repr=False)
    cfg: ElevenLabsConfig
    voice_id: str = ""
    context_chars: int = 1500
    timeout: int = 180
    name: str = "elevenlabs"

    def __post_init__(self) -> None:
        check_api_base(self.cfg.api_base)

    @classmethod
    def for_project(cls, project: Project) -> ElevenLabs:
        api_key, voice_id = project.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
        n = project.settings.narration
        return cls(
            api_key,
            project.settings.elevenlabs,
            voice_id=voice_id,
            context_chars=n.context_chars,
            timeout=n.timeout_seconds,
        )

    def cache_key(self, request: SpeechRequest) -> str:
        return f"{self.name}\n{self.voice_id}\n{request.model}\n{request.output_format}"

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        return self.synthesize(
            request.text,
            voice_id=self.voice_id,
            model=request.model,
            voice_settings=request.voice_settings,
            output_format=request.output_format,
            previous_text=request.previous_text,
            next_text=request.next_text,
            context_chars=self.context_chars,
            timeout=self.timeout,
        )

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}

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
        """Synthesize text; returns (mp3 bytes, word timings)."""
        url = f"{self.cfg.api_base}/text-to-speech/{voice_id}/with-timestamps?output_format={output_format}"
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
        url = f"{self.cfg.api_base}/sound-generation?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.cfg.timeout_seconds)

    def music(self, body: dict[str, Any], *, output_format: str) -> bytes:
        url = f"{self.cfg.api_base}/music?output_format={output_format}"
        return post_bytes(url, body, self._headers(), timeout=self.cfg.timeout_seconds)

    def sound_endpoint(self, output_format: str) -> str:
        return f"{self.cfg.api_base}/sound-generation?output_format={output_format}"

    def music_endpoint(self, output_format: str) -> str:
        return f"{self.cfg.api_base}/music?output_format={output_format}"


register_speech_provider("elevenlabs", ElevenLabs.for_project)
