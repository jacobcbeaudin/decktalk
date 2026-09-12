"""ElevenLabs: text to speech with word timestamps, sound effects, and music."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from ..artifacts import Word
from ..config import ElevenLabsConfig
from ._http import post_bytes, post_json

PUNCT = "\"'“”‘’.,;:!?()[]—–-…"


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
    api_key: str
    cfg: ElevenLabsConfig

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def speak(
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
