"""A fake decktalk-voice, the local server `dtsp` speaks to, answering from the pinned fixtures.

It serves the three endpoints of `tests/data/aligner/openapi.json` on a `Service` of its own: the
model list, the alignment of one take and the speech of one section. Its bodies start from the
fixtures DeckTalk pins, so a test that drives the real client against it drives the client against
the contract. The switches below turn it into a server that answers otherwise, and the counters say
what it was asked.
"""

from __future__ import annotations

import base64
import io
import json
import wave
from typing import Any

from werkzeug import Request, Response

from support.paths import DATA
from support.service import Service

MODELS_PATH = "/v1/models"
ALIGN_PATH = "/v1/align"
SPEECH_PATH = "/v1/speech/timed"

AUDIO = b"take"
"""The audio every speech reply carries, which a test that keeps a take writes as its mp3."""

DIGITS = 3
"""Every time the fake answers is rounded to the millisecond, as the spec says."""

SERVICE_UNAVAILABLE = 503
"""The status a server that is still loading or busy answers with."""

RETRY_AFTER = "0"
"""The wait a busy reply names, which is none, so a test that retries does not sleep."""


def fixture(name: str) -> dict[str, Any]:
    """The body of the pinned fixture called `name`."""
    rows = json.loads((DATA / "aligner" / "fixtures.json").read_text(encoding="utf-8"))
    return next(row["body"] for row in rows if row["name"] == name)


def spread(words: list[str], seconds: float) -> list[dict[str, Any]]:
    """`words` laid end to end over `seconds`, each the same length, which is what the fake's aligner answers."""
    step = seconds / len(words) if words else 0.0
    return [
        {"word": word, "start": round(i * step, DIGITS), "end": round((i + 1) * step, DIGITS)}
        for i, word in enumerate(words)
    ]


def busy(status: int, code: str) -> Response:
    """An `ErrorReply` with `status` and `code`, naming a wait when the status is one a client asks again after."""
    body = {"error": {"code": code, "message": f"the fake answers {status}", "word": None}}
    return Response(json.dumps(body), status, {"Retry-After": RETRY_AFTER}, content_type="application/json")


class VoiceServer:
    """The fake server: what it answers, switched by the fields below, and what it was asked."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self.identity: dict[str, Any] = dict(fixture("models")["aligner"])
        """The aligner the model list names, which every alignment is keyed by."""
        self.speech_words = True
        """Whether a speech reply carries word times. False answers the audio alone, as a voice with no times."""
        self.model_digest: str | None = None
        """The model digest a speech reply names, or None to name none."""
        self.align_reply: dict[str, Any] | bytes | None = None
        """A body `/v1/align` answers in place of the even spread, sent as JSON, or as it is when it is bytes."""
        self.align_busy = 0
        """How many of the next `/v1/align` requests answer 503 `MODEL_LOADING` before the aligner answers."""
        self.align_status = 200
        """The status `/v1/align` answers with. Anything but 200 answers an `ErrorReply`."""
        self.align_code = "MODEL_LOADING"
        """The error code a failed `/v1/align` names."""
        self.models_status = 200
        """The status `/v1/models` answers with. Anything but 200 answers an `ErrorReply`."""
        self.models_body: bytes | None = None
        """Raw bytes `/v1/models` answers in place of the fixture, such as a page of another service."""
        self.models_calls = 0
        self.align_calls = 0
        self.speech_calls = 0
        self.last_request: dict[str, Any] | None = None
        """The JSON part of the last `/v1/align` request, as it was read."""
        self.last_wav: bytes | None = None
        """The audio part of the last `/v1/align` request."""
        service.expect_request(MODELS_PATH, method="GET").respond_with_handler(self.models)
        service.expect_request(ALIGN_PATH, method="POST").respond_with_handler(self.align)
        service.expect_request(SPEECH_PATH, method="POST").respond_with_handler(self.speech)

    @property
    def base_url(self) -> str:
        """The address a client is pointed at, with no trailing slash."""
        return self.service.url_for("").rstrip("/")

    def models(self, _request: Request) -> Response:
        self.models_calls += 1
        if self.models_status != 200:
            return busy(self.models_status, "MODEL_LOADING")
        if self.models_body is not None:
            return Response(self.models_body, 200, content_type="text/html")
        body = {**fixture("models"), "aligner": self.identity}
        return Response(json.dumps(body), 200, content_type="application/json")

    def align(self, request: Request) -> Response:
        self.align_calls += 1
        self.last_wav = request.files["audio"].read()
        self.last_request = json.loads(request.form["request"])
        if self.align_busy:
            self.align_busy -= 1
            return busy(SERVICE_UNAVAILABLE, "MODEL_LOADING")
        if self.align_status != 200:
            return busy(self.align_status, self.align_code)
        if isinstance(self.align_reply, bytes):
            return Response(self.align_reply, 200, content_type="application/json")
        if self.align_reply is not None:
            return Response(json.dumps(self.align_reply), 200, content_type="application/json")
        with wave.open(io.BytesIO(self.last_wav), "rb") as sound:
            seconds = sound.getnframes() / sound.getframerate()
        body = {"words": spread(list(self.last_request["words"]), seconds), "aligner": self.identity}
        return Response(json.dumps(body), 200, content_type="application/json")

    def speech(self, request: Request) -> Response:
        self.speech_calls += 1
        asked = request.get_json()
        body: dict[str, Any] = {"audio_base64": base64.b64encode(AUDIO).decode(), "format": asked["format"]}
        if self.speech_words:
            said = [word for piece in asked["pieces"] for word in piece["text"].split()]
            body["words"] = spread(said, len(said) / 10)
        if self.model_digest is not None:
            body["model_digest"] = self.model_digest
        return Response(json.dumps(body), 200, content_type="application/json")
