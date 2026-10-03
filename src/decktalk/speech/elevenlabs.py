"""ElevenLabs, the cloud voice: text read aloud with a time for every word.

The `/text-to-speech` endpoint returns the audio and a start and an end time per character, which
`words_from_alignment` groups into words, and that is the whole reason DeckTalk can cut on a word.
The same service sells sound effects and music, which are bought by its own adapter in the sound
table, `speech/sound/elevenlabs.py`, with this key and this `base_url`.

The key travels in a header to whatever host `[elevenlabs] base_url` names, which only the machine
sets, and the key is a `Secret` that only the header builder reveals. The
voice id is not a secret: it names which voice reads the script, the way a model name names which
model does, and it arrives in the request rather than being read from the environment here.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from ..errors import InputError, ProviderError
from ..results import Word
from ..secret import Secret
from ..settings import ElevenLabsConfig
from . import DECLARED, PUNCT, Output, SpeechContext, SpeechRequest, canonical_text
from .http import post_json

NAME = "elevenlabs"
"""The name this adapter is registered and declared under, which `DECLARED` holds its key variable by."""


FORMAT_SEPARATOR = "_"
"""What separates the codec, the sample rate and the bitrate in an ElevenLabs output format token."""


BREAK_MODELS = frozenset(
    {"eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_flash_v2", "eleven_turbo_v2_5", "eleven_turbo_v2"}
)
"""The models that read a `<break time="1s" />` tag as silence, which is the v2 family.

Eleven v3 and v4 read no `<break>` tag, so a timed pause sent to them would be dropped or read out.
A model is named here only once it is known to read the tag, so a model nobody has checked is
refused a timed pause rather than trusted with one.
"""


def voice_settings(table: ElevenLabsConfig, speed: float) -> dict[str, Any]:
    """What `[elevenlabs]` and `[voice] speed` ask the service for, under the service's own names.

    The names differ in one place, because the service calls the speaker boost `use_speaker_boost`
    while the key an author writes is `speaker_boost`. These five values are sent with every request
    and are also the settings a take's digest is taken over, so a voiced take keeps its name.
    """
    return {
        "stability": table.stability,
        "similarity_boost": table.similarity_boost,
        "style": table.style,
        "use_speaker_boost": table.speaker_boost,
        "speed": speed,
    }


def output(table: ElevenLabsConfig) -> Output:
    """The format `[elevenlabs] output_format` asks for, and the suffix its codec names a take with.

    ElevenLabs spells a format `codec_rate_bitrate`, such as `mp3_44100_128`, so its codec is the
    file's own suffix and a take of `mp3_44100_128` is written `.mp3`.
    """
    codec = table.output_format.split(FORMAT_SEPARATOR, 1)[0]
    return Output(format=table.output_format, suffix=f".{codec}")


def renders_pauses(model: str) -> bool:
    """Whether this ElevenLabs model renders a timed pause, which it does by reading a `<break>` tag."""
    return model in BREAK_MODELS


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
    """Speech with word timestamps from the cloud voice.

    The key is a `Secret`, so no log line, error, `repr` or JSON payload that reaches this provider
    can print it, and `_headers` is the one place it is revealed.
    """

    context: SpeechContext
    """The tuning that shapes every request, and the retries that `SpeechProviders.provider` set on it."""
    api_key: Secret
    name: str = NAME

    @classmethod
    def for_context(cls, context: SpeechContext) -> ElevenLabs:
        """The provider one project asks for: its key, and the tuning that shapes its requests."""
        (api_key,) = context.secrets.require(cast("str", DECLARED[NAME].key_variable))
        return cls(context, api_key)

    def _headers(self) -> dict[str, str]:
        """The one place the key is revealed, which is the request that is allowed to carry it."""
        return {"xi-api-key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        """One section read aloud, as the mp3 bytes and a start and an end time per word.

        The neighbouring sections travel with the request so the voice carries its prosody across a
        cut, trimmed to the characters the tuning allows, and the alignment that comes back is what
        every later stage measures against.
        """
        if not renders_pauses(request.model) and any(piece.timed for piece in request.pieces):
            # A pause this model would drop is refused before anything is sent, so nothing is bought.
            raise InputError(
                f"the ElevenLabs model {request.model!r} reads no <break> tag, so a timed pause would be dropped.",
                hint=f"Use one of {', '.join(sorted(BREAK_MODELS))}, or take the timed pauses out of the script.",
            )
        url = f"{self.context.base_url.rstrip('/')}/text-to-speech/{request.voice_id}/with-timestamps"
        payload: dict[str, Any] = {
            # The pieces in the canonical text, which writes a timed pause as the <break> tag these models read.
            "text": canonical_text(request.pieces),
            "model_id": request.model,
            "voice_settings": request.voice_settings,
        }
        if request.previous_text:
            payload["previous_text"] = request.previous_text[-self.context.context_chars :]
        if request.next_text:
            payload["next_text"] = request.next_text[: self.context.context_chars]
        reply = post_json(
            f"{url}?output_format={request.output_format}" if request.output_format else url,
            payload,
            self._headers(),
            secrets=(self.api_key,),
            timeout=self.context.speech_timeout_seconds,
            retries=self.context.retries,
        )
        return self._audio(reply), self._words(reply)

    def _audio(self, reply: Mapping[str, Any]) -> bytes:
        """The mp3 the reply carries, refused as a provider failure when it carries none.

        A reply with no audio in it is the service answering something other than speech, and
        letting it through would write an empty take that every later stage measures as silence.
        """
        encoded = reply.get("audio_base64")
        if not isinstance(encoded, str) or not encoded:
            # The service answered, and it may have charged for the answer, so asking again could buy
            # the same take twice. The refusal is therefore not worth trying again on its own.
            raise ProviderError("the voice answered with no audio in it.")
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
