"""The pinned spec of decktalk-voice, the local server `dtsp` speaks to, and the fixtures that hold it.

`tests/data/aligner/openapi.json` is DeckTalk's copy of the server's contract: the model list it
answers first, the alignment it answers for one take, and the speech it answers for one section.
`tests/data/aligner/fixtures.json` files bodies under the schema they are held to and the verdict
the spec must give each one. Every request the client builds and every reply the fake server sends
is judged against the same spec, so the client, the fake and the server cannot drift apart unseen.
"""

from __future__ import annotations

import io
import json
import secrets
import urllib.request
import wave
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import pytest

from decktalk.settings import BY_ID
from decktalk.speech import AlignerContext, AlignmentRequest
from decktalk.speech.dtsp import DtspAligner
from support.paths import DATA
from support.service import Service
from support.voice_server import ALIGN_PATH, MODELS_PATH, SPEECH_PATH, VoiceServer

ALIGNER = DATA / "aligner"


def spec() -> dict[str, Any]:
    return json.loads((ALIGNER / "openapi.json").read_text(encoding="utf-8"))


def fixtures() -> list[dict[str, Any]]:
    return json.loads((ALIGNER / "fixtures.json").read_text(encoding="utf-8"))


def judge(schema: str, body: object) -> list[str]:
    """Every way `body` breaks the spec's component `schema`, as messages, which is empty when it holds."""
    document = spec()
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": document["components"]}
    )
    return [error.message for error in validator.iter_errors(body)]


def schemas(node: object) -> Iterator[dict[str, Any]]:
    """Every schema object nested anywhere in `node`."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from schemas(value)


@pytest.mark.parametrize("fixture", fixtures(), ids=lambda fixture: fixture["name"])
def test_every_fixture_gets_the_verdict_it_is_filed_under(fixture: dict[str, Any]) -> None:
    errors = judge(fixture["schema"], fixture["body"])
    assert (errors == []) is fixture["valid"], errors or f"{fixture['name']} holds, and it is filed as invalid"


def test_every_schema_a_fixture_names_is_one_the_spec_declares() -> None:
    declared = set(spec()["components"]["schemas"])
    named = {fixture["schema"] for fixture in fixtures()}
    assert named <= declared, named - declared
    assert {"ModelsReply", "AlignRequest", "AlignReply", "SpeechReply", "ErrorReply"} <= named


def test_the_spec_lets_a_newer_server_add_fields() -> None:
    closed = [node for node in schemas(spec()["components"]) if node.get("additionalProperties") is False]
    assert closed == []
    extra = [fixture for fixture in fixtures() if fixture["name"].endswith("extra-field")]
    assert extra
    assert all(fixture["valid"] for fixture in extra)


def test_the_spec_and_the_setting_name_the_same_port() -> None:
    served = urlsplit(spec()["servers"][0]["url"])
    setting = urlsplit(BY_ID["dtsp.base_url"].default)
    assert (served.hostname, served.port) == (setting.hostname, setting.port)


@pytest.fixture
def voice_server() -> Iterator[VoiceServer]:
    """The fake decktalk-voice on a service of its own, since this directory sees no other fixture."""
    held = Service()
    held.start()
    try:
        yield VoiceServer(held)
    finally:
        held.released.set()
        held.clear()
        held.stop()


def a_wav(seconds: float, rate: int = 16000) -> bytes:
    """A silent mono 16-bit WAV `seconds` long."""
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(bytes(2 * round(seconds * rate)))
    return out.getvalue()


def multipart(wav: bytes, request: dict[str, Any]) -> tuple[bytes, str]:
    """The body the spec describes for `/v1/align`, built by hand, and its content type."""
    boundary = secrets.token_hex()
    parts = [
        (b'name="audio"; filename="take.wav"', b"audio/wav", wav),
        (b'name="request"', b"application/json", json.dumps(request).encode()),
    ]
    body = b"".join(
        b"--%s\r\nContent-Disposition: form-data; %s\r\nContent-Type: %s\r\n\r\n%s\r\n"
        % (boundary.encode(), disposition, kind, data)
        for disposition, kind, data in parts
    )
    return body + b"--%s--\r\n" % boundary.encode(), f"multipart/form-data; boundary={boundary}"


def reply(url: str, data: bytes | None = None, kind: str = "application/json") -> dict[str, Any]:
    sent = urllib.request.Request(url, data=data, headers={"Content-Type": kind})
    with urllib.request.urlopen(sent, timeout=5) as answer:
        return json.loads(answer.read())


def test_the_fake_server_answers_every_endpoint_with_a_body_the_spec_accepts(voice_server: VoiceServer) -> None:
    base = voice_server.base_url
    assert judge("ModelsReply", reply(base + MODELS_PATH)) == []
    body, kind = multipart(a_wav(1.0), {"words": ["A", "bowl"]})
    assert judge("AlignReply", reply(base + ALIGN_PATH, body, kind)) == []
    speech = {"pieces": [{"text": "A bowl.", "pause": None}], "voice": "v", "model": "m", "speed": 1.0, "format": "mp3"}
    assert judge("SpeechRequest", speech) == []
    for words in (True, False):
        voice_server.speech_words = words
        voice_server.model_digest = "sha256:5eed" if words else None
        assert judge("SpeechReply", reply(base + SPEECH_PATH, json.dumps(speech).encode())) == []


def test_the_fake_server_reads_the_multipart_request_the_spec_describes(voice_server: VoiceServer) -> None:
    body, kind = multipart(a_wav(2.0), {"words": ["A", "bowl", "a", "ball"]})
    answered = reply(voice_server.base_url + ALIGN_PATH, body, kind)
    assert voice_server.last_request is not None
    assert judge("AlignRequest", voice_server.last_request) == []
    assert voice_server.last_wav == a_wav(2.0)
    assert [(word["word"], word["start"], word["end"]) for word in answered["words"]] == [
        ("A", 0.0, 0.5),
        ("bowl", 0.5, 1.0),
        ("a", 1.0, 1.5),
        ("ball", 1.5, 2.0),
    ]
    assert voice_server.align_calls == 1


def aligner(voice_server: VoiceServer) -> DtspAligner:
    context = AlignerContext(base_url=voice_server.base_url, handshake_seconds=5, timeout_seconds=5)
    return DtspAligner.for_context(context)


@pytest.mark.parametrize(
    "fixture",
    [row for row in fixtures() if row["schema"] == "AlignReply" and row["valid"]],
    ids=lambda fixture: fixture["name"],
)
def test_the_client_reads_every_valid_align_reply_fixture(voice_server: VoiceServer, fixture: dict[str, Any]) -> None:
    voice_server.align_reply = fixture["body"]
    sent = tuple(word["word"] for word in fixture["body"]["words"])
    words = aligner(voice_server).align(AlignmentRequest(wav=a_wav(1.0), words=sent))
    assert [(word.word, word.start, word.end) for word in words] == [
        (row["word"], row["start"], row["end"]) for row in fixture["body"]["words"]
    ]


def test_the_request_the_client_builds_is_one_the_spec_accepts(voice_server: VoiceServer) -> None:
    wav = a_wav(1.0)
    aligner(voice_server).align(AlignmentRequest(wav=wav, words=("A", "bowl")))
    assert voice_server.last_request is not None
    assert judge("AlignRequest", voice_server.last_request) == []
    assert voice_server.last_wav == wav
