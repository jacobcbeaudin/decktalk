"""`dtsp`, the DeckTalk speech protocol: a voice served by a separate local server, with a time for every word.

`decktalk-voice` is a separate local server that is not part of this repository. It loads the local
speech models in a process of its own and answers one request per turn, by default on a loopback address, so
no model, no GPU runtime and no model weights ever load inside DeckTalk, and a model that crashes
ends the server rather than the build. This module is the one client of it the engine has.

One turn is one POST to `/v1/speech/timed`:

    {"pieces": [{"text": "A bowl.", "pause": 0.7}, {"text": "A ball.", "pause": null}],
     "voice": "af_heart", "model": "kokoro-82m", "speed": 1.0, "format": "mp3"}

Each piece is a paragraph of the script with the pause after it in seconds: null for none, 0 for a
beat, and more for that many seconds of silence, which the server joins the voiced pieces with. The
reply carries the audio, a start and an end time for every word in seconds after the take starts,
and the digest of the model that spoke:

    {"audio_base64": "...", "format": "mp3", "model_digest": "sha256:...",
     "words": [{"word": "A", "start": 0.0, "end": 0.12}, {"word": "bowl", "start": 0.12, "end": 0.5}]}

A reply with no `words`, or an empty list, is a take the voice sent no word times with, which the
aligner times. Any other field of the reply, such as the model's licence, is the server's own and
is not read. The server needs no key, so the request names no secret, and it bills nothing.

The same server is the aligner, `DtspAligner`, which times the words of a take whose voice sent none.
Building it asks `GET /v1/models` once, with `[dtsp] handshake_seconds` to answer and no retry, which
names the service, the contract it speaks and the aligner it serves:

    {"service": "decktalk-voice", "contract": 1,
     "aligner": {"id": "...", "revision": "...", "model_digest": "sha256:..."}}

One take is one multipart POST to `/v1/align`: an `audio` part holding the take as a mono 16-bit WAV,
and a `request` part holding `{"words": [...]}`, the words the take should hold. The reply is one
timed word per word sent, spelled as sent, in seconds from the first sample. Fields either side does
not know are ignored, and `tests/data/aligner/openapi.json` is the pinned spec of all three paths.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from pydantic import ValidationError

from ..errors import ProviderError
from ..results import Word
from ..settings import DtspConfig
from . import (
    DECLARED,
    FREE,
    PUNCT,
    AlignerContext,
    AlignerIdentity,
    AlignmentRequest,
    Output,
    Speech,
    SpeechContext,
    SpeechRequest,
)
from .http import get_json, post_json

NAME = "dtsp"
"""The name this adapter is declared under, which every take it voices is named by beside the voice id."""

SPEECH_PATH = "/v1/speech/timed"
"""Where the local server is asked for one section read aloud with its word times."""

MODELS_PATH = "/v1/models"
"""Where the local server says which service it is, which contract it speaks and which aligner it serves."""

ALIGN_PATH = "/v1/align"
"""Where the local server is sent one take's audio and words and answers a time for every word."""

CONTRACT = 1
"""Truth: the version of the local server's contract this client speaks, which the server's model list must name."""

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

    def speak(self, request: SpeechRequest) -> Speech:
        """One section read aloud by the local server: the audio, a start and an end time per word when it sends
        them, and the model it served."""
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
        served = reply.get("model_digest")
        return Speech(
            _audio(reply, request.output_format),
            _words(reply),
            served if isinstance(served, str) and served else None,
        )


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


def _words(reply: Mapping[str, Any]) -> tuple[Word, ...] | None:
    """The word times the reply carries, which is what the whole cut is measured against.

    A reply with no word times, absent or empty, is None, so the take is kept and the aligner times
    it. A `words` that is not a list is refused, because it is neither times nor their absence.
    """
    rows = reply.get("words")
    if rows is None or rows == []:
        return None
    if not isinstance(rows, list):
        raise ProviderError("the local speech server answered with no word times.")
    try:
        timed = [Word.model_validate(row) for row in rows]
    except ValidationError as exc:
        raise ProviderError(f"the local speech server answered with a word DeckTalk cannot read: {exc}") from exc
    return (
        tuple(
            Word(word=clean, start=round(word.start, WORD_DIGITS), end=round(word.end, WORD_DIGITS))
            for word in timed
            if (clean := word.word.strip(PUNCT))
        )
        or None
    )


SERVER = DECLARED[NAME].server
"""The service the local server must name itself as, which tells it apart from anything else on its port."""

START = DECLARED[NAME].start_hint
"""The advice beside a handshake nothing answered as the local server, which is the voice's own start hint."""

STARTING = f"Run decktalk narrate again once {SERVER} answers."
"""The advice beside a handshake the server answered while it was still starting."""

IDENTITY_FIELDS = ("id", "revision", "model_digest")
"""Truth: the fields of the model list's `aligner` that key aligned words, each a line of its own."""


@dataclass(frozen=True)
class DtspAligner:
    """The aligner on the local server, built by asking it who it is and holding the answer."""

    context: AlignerContext
    identity: AlignerIdentity

    @classmethod
    def for_context(cls, context: AlignerContext) -> DtspAligner:
        """The aligner the server at `context.base_url` serves, asked once with no retry.

        Every way the handshake fails is a `ProviderError` with `reached` false, because a port that
        answers as nothing DeckTalk can align with is, for this run, an aligner that is down.
        """
        base = context.base_url.rstrip("/")
        url = f"{base}{MODELS_PATH}"
        try:
            reply = get_json(url, timeout=context.handshake_seconds)
        except ProviderError as exc:
            if exc.status == HTTPStatus.SERVICE_UNAVAILABLE:
                raise _down(f"{SERVER} at {base} is still starting (HTTP {exc.status})", STARTING, again=True) from exc
            raise _down(f"nothing at {url} answered as {SERVER}: {str(exc).rstrip('.')}") from exc
        return cls(context, _identity(url, reply))

    def align(self, request: AlignmentRequest) -> list[Word]:
        """The start and end of every word of one take, as the server timed them in its WAV."""
        boundary = secrets.token_hex()
        reply = post_json(
            f"{self.context.base_url.rstrip('/')}{ALIGN_PATH}",
            _multipart(boundary, request),
            {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
            secrets=(),
            timeout=self.context.timeout_seconds,
            retries=self.context.retries,
            free=True,
        )
        return _aligned(reply, request.words)


def _down(message: str, hint: str = START, *, again: bool = False) -> ProviderError:
    """A handshake that found no aligner, which a run reads as the aligner being down.

    `again` is true for a server that is still starting, which running again is enough for.
    """
    return ProviderError(message, hint=hint, reached=False, retryable=again)


def _identity(url: str, reply: Mapping[str, Any]) -> AlignerIdentity:
    """The aligner the model list names, refused unless the server is the one this client speaks to."""
    service, contract = reply.get("service"), reply.get("contract")
    if service != SERVER:
        raise _down(f"{url} answered as {service!r}, not {SERVER}")
    if contract != CONTRACT:
        raise _down(f"{url} speaks contract {contract!r}, and this DeckTalk speaks {CONTRACT}")
    named = reply.get("aligner")
    fields = [named.get(field) if isinstance(named, Mapping) else None for field in IDENTITY_FIELDS]
    usable = [value for value in fields if isinstance(value, str) and value and "\n" not in value]
    if len(usable) != len(IDENTITY_FIELDS):
        raise _down(f"{url} named no aligner DeckTalk can key its words by")
    aligner, revision, model_digest = usable
    return AlignerIdentity(id=aligner, revision=revision, model_digest=model_digest)


def _multipart(boundary: str, request: AlignmentRequest) -> bytes:
    """The two parts `/v1/align` takes: the take as a WAV, and the words it should hold as JSON."""
    words = json.dumps({"words": list(request.words)}).encode()
    parts = (
        (b'name="audio"; filename="take.wav"', b"audio/wav", request.wav),
        (b'name="request"', b"application/json", words),
    )
    mark = boundary.encode()
    body = b"".join(
        b"--%s\r\nContent-Disposition: form-data; %s\r\nContent-Type: %s\r\n\r\n%s\r\n" % (mark, head, kind, data)
        for head, kind, data in parts
    )
    return body + b"--%s--\r\n" % mark


def _aligned(reply: Mapping[str, Any], sent: tuple[str, ...]) -> list[Word]:
    """The words of the reply, refused unless there is one per word sent, spelled as it was sent.

    Only a word's spelling and its two times are read, so a field a newer server adds is ignored.
    """
    rows = reply.get("words")
    try:
        if not isinstance(rows, list):
            raise ValueError(f"its words are {rows!r}, not a list")
        timed = [
            Word.model_validate({name: row[name] for name in ("word", "start", "end") if name in row})
            if isinstance(row, Mapping)
            else Word.model_validate(row)
            for row in rows
        ]
    except (ValidationError, ValueError) as exc:
        raise ProviderError(f"the aligner answered a word DeckTalk cannot read: {exc}") from exc
    if len(timed) != len(sent):
        raise ProviderError(f"the aligner answered {len(timed)} words for the {len(sent)} it was sent.")
    for word, asked in zip(timed, sent, strict=True):
        if word.word != asked:
            raise ProviderError(f"the aligner answered {word.word!r} where it was sent {asked!r}.")
    return [Word(word=w.word, start=round(w.start, WORD_DIGITS), end=round(w.end, WORD_DIGITS)) for w in timed]
