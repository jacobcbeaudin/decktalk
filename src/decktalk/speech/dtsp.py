"""`dtsp`, the DeckTalk speech protocol: a voice served by a separate local server, with a time for every word.

`decktalk-voice` is a separate local server that is not part of this repository. It loads the local
speech models in a process of its own and answers one request per section, by default on a loopback address, so
no model, no GPU runtime and no model weights ever load inside DeckTalk, and a model that crashes
ends the server rather than the build. This module is the one client of it the engine has.

One section is one POST to `/v1/speech/timed`:

    {"pieces": [{"text": "A bowl.", "pause": 0.7}, {"text": "A ball.", "pause": null}],
     "voice": "af_heart", "model": "kokoro-82m", "speed": 1.0, "format": "mp3"}

Each piece is a paragraph of the script with the pause after it in seconds: null for none, 0 for a
beat, and more for that many seconds of silence, which the server joins the voiced pieces with. The
reply carries the audio and a start and an end time for every word, in seconds after the take starts:

    {"audio_base64": "...", "format": "mp3",
     "words": [{"word": "A", "start": 0.0, "end": 0.12}, {"word": "bowl", "start": 0.12, "end": 0.5}]}

Any other field of the reply, such as the model's revision or its licence, is the server's own and
is not read. The server needs no key, so the request names no secret, and it bills nothing.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..errors import ProviderError
from ..results import Word
from ..settings import DtspConfig
from . import DECLARED, FREE, PUNCT, Output, SpeechContext, SpeechRequest
from .http import post_json

NAME = "dtsp"
"""The name this adapter is declared under, which every take it voices is named by beside the voice id."""

SPEECH_PATH = "/v1/speech/timed"
"""Where the local server is asked for one section read aloud with its word times."""

OUTPUT = Output(format="mp3", suffix=".mp3")
"""Truth: what a take is asked for and written as, which is the mp3 every take ffmpeg reads is in."""

WORD_DIGITS = 3
"""Truth: a word time is read to the millisecond, as every voice's words are."""


def identity(_table: DtspConfig, speed: float) -> dict[str, Any]:
    """The settings a `dtsp` take is named by, which is its pace: the server, the model and the voice name the rest."""
    return {"speed": speed}


@dataclass
class Dtsp:
    """A voice on the local server, built from a `SpeechContext` and from no key.

    The base URL is `[dtsp] base_url`, which only the machine sets, so a project file can send its
    script to no machine the machine did not name.
    """

    context: SpeechContext
    """The base URL, the timeout and the retries, as the machine stamped them on."""
    name: str = NAME

    @classmethod
    def for_context(cls, context: SpeechContext) -> Dtsp:
        """The provider one project asks for, which reads no secret because the server takes none."""
        return cls(context)

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        """One section read aloud by the local server, as the audio and a start and an end time per word."""
        payload: dict[str, Any] = {
            "pieces": [{"text": piece.text, "pause": piece.pause} for piece in request.pieces],
            "voice": request.voice_id,
            "model": request.model,
            "speed": request.voice_settings["speed"],
            "format": request.output_format,
        }
        reply = post_json(
            f"{self.context.base_url.rstrip('/')}{SPEECH_PATH}",
            payload,
            {"Content-Type": "application/json", "Accept": "application/json"},
            secrets=(),
            timeout=self.context.speech_timeout_seconds,
            retries=self.context.retries,
            free=DECLARED[NAME].billing is FREE,
        )
        return _audio(reply, request.output_format), _words(reply)


def _audio(reply: Mapping[str, Any], asked: str) -> bytes:
    """The audio the reply carries, refused when it carries none or carries another format than was asked for.

    A take is named by its format, so audio in another one would be written under a name that lies.
    """
    encoded, answered = reply.get("audio_base64"), reply.get("format", asked)
    if not isinstance(encoded, str) or not encoded:
        raise ProviderError("the local speech server answered with no audio in it.")
    if answered != asked:
        raise ProviderError(f"the local speech server answered in {answered!r} when {asked!r} was asked for.")
    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ProviderError("the local speech server answered with audio that is not base64.") from exc


def _words(reply: Mapping[str, Any]) -> list[Word]:
    """The word times the reply carries, which is what the whole cut is measured against.

    A reply with no word times is refused rather than kept, because a take with no words cannot be
    cut, and aligning a take that came without them is not this adapter's to do.
    """
    rows = reply.get("words")
    if not isinstance(rows, list) or not rows:
        raise ProviderError("the local speech server answered with no word times.")
    try:
        timed = [Word.model_validate(row) for row in rows]
    except ValidationError as exc:
        raise ProviderError(f"the local speech server answered with a word DeckTalk cannot read: {exc}") from exc
    return [
        Word(word=clean, start=round(word.start, WORD_DIGITS), end=round(word.end, WORD_DIGITS))
        for word in timed
        if (clean := word.word.strip(PUNCT))
    ]
