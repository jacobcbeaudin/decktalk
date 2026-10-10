"""The `dtsp` voice: a free, keyless voice on a local server, reached at the address the machine names.

The server is a local fake on a socket of its own, which answers `/v1/speech/timed` the way the
DeckTalk speech protocol says, so the request the adapter sends and the reply it reads are the real
ones. The end-to-end test drives the real command line against it, with no key anywhere.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import socket
import sys
import time
import urllib.request
import wave
from http.client import HTTPResponse
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from werkzeug import Request, Response

from decktalk.artifacts.takes import TakeInputs
from decktalk.cli import main
from decktalk.errors import ProviderError
from decktalk.findings import Code
from decktalk.media import audio
from decktalk.results import BillingBasis, TakeOutcome
from decktalk.settings import BY_ID, MACHINE_FILE_VARIABLE, DtspConfig, Settings, VoiceConfig
from decktalk.speech import (
    DECLARED,
    PROVIDERS,
    AlignerContext,
    AlignerIdentity,
    AlignmentRequest,
    Piece,
    SpeechContext,
    SpeechRequest,
    VoiceInForce,
)
from decktalk.speech import http as speech_http
from decktalk.speech.dtsp import MODELS_PATH, OUTPUT, SPEECH_PATH, Dtsp, DtspAligner
from support.service import Service
from support.speech import NoSecrets
from support.voice_server import VoiceServer

AUDIO = b"ID3 a local take"
VOICE = "af_heart"
MODEL = DtspConfig().model


def context(base: str, **over: Any) -> SpeechContext:
    """What a run hands the adapter: the base its table names, a timeout and no secret it may read."""
    fields: dict[str, Any] = {
        "secrets": NoSecrets(),
        "base_url": base,
        "context_characters": 0,
        "speech_timeout_seconds": 5,
        **over,
    }
    return SpeechContext(**fields)


def spoken(pieces: list[dict[str, Any]]) -> dict[str, Any]:
    """The reply the server gives for these pieces: mp3 bytes and one word every tenth of a second."""
    words = [word for piece in pieces for word in piece["text"].split()]
    timed = [
        {"word": word, "start": round(i * 0.1, 3), "end": round(i * 0.1 + 0.09, 3)} for i, word in enumerate(words)
    ]
    return {"audio_base64": base64.b64encode(AUDIO).decode(), "format": "mp3", "words": timed}


class LocalServer:
    """The local speech server, answering every section it is asked for and keeping each request."""

    def __init__(self, service: Service) -> None:
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.reply: dict[str, Any] | None = None
        service.expect_request(SPEECH_PATH, method="POST").respond_with_handler(self.speak)

    def speak(self, request: Request) -> Response:
        body = request.get_json()
        self.requests.append((body, {name.lower(): value for name, value in request.headers.items()}))
        reply = self.reply if self.reply is not None else spoken(body["pieces"])
        return Response(json.dumps(reply), 200, content_type="application/json")


@pytest.fixture
def server(service: Service) -> LocalServer:
    return LocalServer(service)


def request(**over: Any) -> SpeechRequest:
    fields: dict[str, Any] = {
        "pieces": (Piece("A bowl.", 0.7), Piece("A ball.", 0.0), Piece("It rests.")),
        "voice_id": VOICE,
        "model": MODEL,
        "voice_settings": {"speed": 1.1},
        "output_format": OUTPUT.format,
        **over,
    }
    return SpeechRequest(**fields)


# ---- what it declares ----------------------------------------------------------------------------


def test_it_is_in_the_closed_set_free_keyless_and_renders_every_pause():
    assert sorted(PROVIDERS) == sorted(DECLARED) == ["dtsp", "elevenlabs"]
    voice = VoiceInForce.of(Settings(voice=VoiceConfig(provider="dtsp")))
    assert voice.billing is BillingBasis.FREE
    assert voice.key_variable is None
    assert voice.renders_pauses
    assert voice.output == OUTPUT


def test_its_takes_never_share_a_name_with_an_elevenlabs_take_of_the_same_words():
    """The adapter's name opens every digest, so one sentence voiced by each is two takes."""
    same = {"voice": VOICE, "model": MODEL, "output_format": "mp3_44100_128", "settings": {"speed": 1.0}, "text": "Hi"}
    assert TakeInputs.of(provider="dtsp", **same).digest != TakeInputs.of(provider="elevenlabs", **same).digest
    assert DECLARED["dtsp"].identity(DtspConfig(), 1.1) == {"speed": 1.1}


# ---- the request and the reply -----------------------------------------------------------------


def test_one_section_is_sent_as_pieces_with_their_pauses_and_read_back_as_audio_and_words(
    service: Service, server: LocalServer
):
    speech = Dtsp(context(service.url_for(""))).speak(request())
    audio_bytes, words = speech.audio, speech.words
    ((body, headers),) = server.requests
    assert body == {
        "pieces": [
            {"text": "A bowl.", "pause": 0.7},
            {"text": "A ball.", "pause": 0.0},
            {"text": "It rests.", "pause": None},
        ],
        "voice": VOICE,
        "model": MODEL,
        "speed": 1.1,
        "format": "mp3",
    }
    assert "authorization" not in headers and not any("key" in name for name in headers)
    assert audio_bytes == AUDIO
    assert words is not None
    assert [word.word for word in words] == ["A", "bowl", "A", "ball", "It", "rests"]
    assert (words[1].start, words[1].end) == (0.1, 0.19)


@pytest.mark.parametrize(
    ("reply", "said"),
    [
        ({"format": "mp3", "words": [{"word": "a", "start": 0, "end": 1}]}, "no audio"),
        ({"audio_base64": "aGk=", "format": "wav", "words": [{"word": "a", "start": 0, "end": 1}]}, "'wav'"),
        ({"audio_base64": "not base64!", "format": "mp3", "words": [{"word": "a", "start": 0, "end": 1}]}, "base64"),
        ({"audio_base64": "aGk=", "format": "mp3", "words": [{"word": "a", "start": -1, "end": 1}]}, "cannot read"),
    ],
)
def test_a_reply_that_is_not_a_take_is_a_provider_error(
    service: Service, server: LocalServer, reply: dict[str, Any], said: str
):
    server.reply = reply
    with pytest.raises(ProviderError, match=said):
        Dtsp(context(service.url_for(""))).speak(request())


def test_a_failure_of_a_voice_that_bills_nothing_never_says_the_request_was_charged(service: Service) -> None:
    """A free voice charges nothing, so its refusal neither warns of a bill nor sends the author to one."""
    service.expect_request(SPEECH_PATH, method="POST").respond_with_data("not json", content_type="text/plain")
    with pytest.raises(ProviderError) as caught:
        Dtsp(context(service.url_for(""))).speak(request())
    said = f"{caught.value} {caught.value.hint}"
    assert "charged" not in said, said
    assert "usage" not in said, said
    assert caught.value.possibly_charged is False


def test_a_reply_from_the_free_voice_that_broke_is_asked_for_again(
    service: Service, server: LocalServer, monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """The adapter says the voice bills nothing, so a server that hung up once is asked again and answers."""
    real = speech_http.urlopen
    hung_up: list[str] = []

    def hangs_up_once(req: urllib.request.Request, **options: float) -> HTTPResponse:
        if not hung_up:
            hung_up.append(req.full_url)
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return real(req, **options)

    monkeypatch.setattr(speech_http, "urlopen", hangs_up_once)
    assert Dtsp(context(service.url_for(""), retries=1)).speak(request()).audio == AUDIO
    assert len(hung_up) == 1 and len(server.requests) == 1 and len(waits) == 1


def test_a_request_to_this_machine_never_goes_through_a_proxy(
    service: Service, server: LocalServer, monkeypatch: pytest.MonkeyPatch
):
    """A proxy is another machine, so the script reaches the local server directly whatever the process names.

    urllib reads the proxy variables when an opener is built, so the opener every other request
    goes through is replaced by one that names a proxy for http, as a machine with HTTP_PROXY set has.
    """
    proxied = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": "http://proxy.invalid:3128"}), speech_http.DropAuthAcrossOrigins
    )
    monkeypatch.setattr(speech_http, "_opener", proxied)
    Dtsp(context(service.url_for(""))).speak(request())
    assert len(server.requests) == 1


# ---- end to end: the command line voices a lesson for free --------------------------------------

LESSON_TOML = """
[project]
name = "local"

[voice]
provider = "dtsp"
id = "{voice}"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

LESSON_SCRIPT = (
    "# Notes\n\n## 1. Open\n\nA bowl and a ball.\n\n[pause 0.5]\n\nIt rolls.\n\n## 2. Close\n\nThe ball rests.\n"
)


def lesson(root: Path, url: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A two-section lesson read by the local voice, whose server this machine names at `url`."""
    (root / "deck").mkdir(parents=True)
    (root / "deck" / "index.html").write_text("<p>deck</p>", encoding="utf-8")
    (root / "decktalk.toml").write_text(LESSON_TOML.format(voice=VOICE), encoding="utf-8")
    (root / "script.md").write_text(LESSON_SCRIPT, encoding="utf-8")
    monkeypatch.setenv(BY_ID["dtsp.base_url"].environment, url)
    return root


def command(*argv: str) -> tuple[int, str, str]:
    """One command line through the real entry point, with no terminal, as a script or a CI job runs it."""
    with CliRunner().isolation() as (out, err, _):
        code = main(list(argv))
        sys.stdout.flush()
        sys.stderr.flush()
        return code, out.getvalue().decode(), err.getvalue().decode()


@pytest.fixture
def machine_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process with no voice key and no machine file, so nothing but the project decides the voice."""
    for name in [declared.key_variable for declared in DECLARED.values() if declared.key_variable]:
        monkeypatch.delenv(name, raising=False)
    for name in ("DECKTALK_VOICE_ID", "DECKTALK_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(MACHINE_FILE_VARIABLE, str(tmp_path / "no-machine.toml"))
    monkeypatch.setenv("TTY_COMPATIBLE", "0")
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)


@pytest.mark.usefixtures("fake_ffmpeg", "machine_without_a_key")
def test_narrate_voices_a_lesson_through_the_local_server_with_no_key_and_no_spend_asked(
    tmp_path: Path, service: Service, server: LocalServer, monkeypatch: pytest.MonkeyPatch
):
    """The free rule end to end: no `--spend`, no terminal, no key, and every section is voiced."""
    monkeypatch.chdir(lesson(tmp_path / "local", service.url_for(""), monkeypatch))
    code, out, err = command("narrate", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert [row["outcome"] for row in result["sections"]] == [TakeOutcome.VOICED.value] * 2
    assert result["spend"] is True
    assert result["cost"]["billing"] == BillingBasis.FREE.value and result["cost"]["dollars"] == 0
    assert len(server.requests) == 2
    # The two sections are voiced at once, so the opening one is found by its words.
    opening = next(body for body, _ in server.requests if body["pieces"][0]["text"] == "A bowl and a ball.")
    assert [piece["pause"] for piece in opening["pieces"]] == [0.5, None]
    assert {body["voice"] for body, _ in server.requests} == {VOICE}


@pytest.mark.usefixtures("fake_ffmpeg", "machine_without_a_key")
def test_a_run_with_no_spend_still_voices_every_missing_take_through_the_local_server(
    tmp_path: Path, service: Service, server: LocalServer, monkeypatch: pytest.MonkeyPatch
):
    """`--no-spend` gates money, which a free voice never asks for, so the Action and `--watch` voice with it."""
    monkeypatch.chdir(lesson(tmp_path / "local", service.url_for(""), monkeypatch))
    code, out, err = command("narrate", "--no-spend", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert result["spend"] is False
    assert [row["outcome"] for row in result["sections"]] == [TakeOutcome.VOICED.value] * 2
    assert [found["code"] for found in result["findings"]] == []
    assert len(server.requests) == 2


def closed_port() -> str:
    """A loopback address nothing listens on, found by binding a port and letting it go."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.mark.usefixtures("fake_ffmpeg", "machine_without_a_key", "waits")
@pytest.mark.parametrize("flag", ["--no-spend", "--spend"])
def test_a_local_server_that_is_not_running_plays_placeholders_and_says_to_start_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
):
    """A free voice that is down never fails the run: each section plays a placeholder whose hint starts the server."""
    monkeypatch.chdir(lesson(tmp_path / "down", closed_port(), monkeypatch))
    code, out, err = command("narrate", flag, "--json")
    assert code == 0, err
    result = json.loads(out)
    assert [row["outcome"] for row in result["sections"]] == [TakeOutcome.PLACEHOLDER.value] * 2
    assert [found["code"] for found in result["findings"]] == [Code.TAKE_MISSING.value] * 2
    for found in result["findings"]:
        assert "Start decktalk-voice" in found["message"]
        assert "[dtsp] base_url" in found["message"]
        assert "--spend" not in found["message"]


# ---- the aligner -------------------------------------------------------------------------------


def a_wav(seconds: float, rate: int = 16000) -> bytes:
    """A silent mono 16-bit WAV `seconds` long, as `audio.wav` decodes a take to."""
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(bytes(2 * round(seconds * rate)))
    return out.getvalue()


def aligner_context(base: str, **over: Any) -> AlignerContext:
    """What a run hands the aligner: the shared base URL, the two waits and the retries."""
    fields: dict[str, Any] = {"base_url": base, "handshake_seconds": 5, "timeout_seconds": 5, "retries": 0, **over}
    return AlignerContext(**fields)


SAID = ("A", "bowl", "a", "ball")


def test_the_handshake_asks_the_model_list_once_and_reads_who_aligns(voice_server: VoiceServer) -> None:
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url))
    assert aligner.identity == AlignerIdentity(**voice_server.identity)
    assert voice_server.models_calls == 1
    assert voice_server.align_calls == 0


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (b"<html>a page of another service</html>", "answered as decktalk-voice: "),
        (json.dumps({"service": "other", "contract": 1}).encode(), "answered as 'other', not decktalk-voice"),
    ],
)
def test_a_port_held_by_another_service_is_an_aligner_that_is_down(
    voice_server: VoiceServer, body: bytes, said: str
) -> None:
    voice_server.models_body = body
    with pytest.raises(ProviderError, match=said) as caught:
        DtspAligner.for_context(aligner_context(voice_server.base_url))
    assert caught.value.reached is False
    assert caught.value.hint == "Start decktalk-voice at the address [dtsp] base_url names"


def test_a_server_on_another_contract_is_an_aligner_that_is_down(voice_server: VoiceServer) -> None:
    voice_server.models_body = json.dumps({"service": "decktalk-voice", "contract": 2}).encode()
    with pytest.raises(ProviderError, match="speaks contract 2, and this DeckTalk speaks 1") as caught:
        DtspAligner.for_context(aligner_context(voice_server.base_url))
    assert caught.value.reached is False


@pytest.mark.parametrize("field", ["id", "revision", "model_digest"])
@pytest.mark.parametrize("value", ["", "two\nlines", None])
def test_a_model_list_naming_no_usable_aligner_is_an_aligner_that_is_down(
    voice_server: VoiceServer, field: str, value: str | None
) -> None:
    voice_server.identity = {**voice_server.identity, field: value}
    with pytest.raises(ProviderError, match="named no aligner DeckTalk can key its words by") as caught:
        DtspAligner.for_context(aligner_context(voice_server.base_url))
    assert caught.value.reached is False


def test_a_server_still_starting_is_down_for_this_run_and_says_to_run_again(
    voice_server: VoiceServer, waits: list[float]
) -> None:
    voice_server.models_status = 503
    with pytest.raises(ProviderError, match=r"is still starting \(HTTP 503\)") as caught:
        DtspAligner.for_context(aligner_context(voice_server.base_url, retries=3))
    assert caught.value.reached is False
    assert caught.value.hint == "Run decktalk narrate again once decktalk-voice answers."
    assert caught.value.retryable is True
    assert voice_server.models_calls == 1 and waits == []


def test_a_silent_listener_costs_the_handshake_wait_and_not_the_request_timeout(service: Service) -> None:
    service.expect_request(MODELS_PATH, method="GET").respond_with_handler(service.stalls)
    started = time.monotonic()
    with pytest.raises(ProviderError) as caught:
        DtspAligner.for_context(aligner_context(service.url_for(""), handshake_seconds=1, timeout_seconds=60))
    assert time.monotonic() - started < STALL_BOUND_SECONDS
    assert caught.value.reached is False


STALL_BOUND_SECONDS = 5
"""Well past the one second handshake and far short of the sixty second request timeout."""


def test_a_take_is_sent_as_a_wav_part_and_a_words_part_and_read_back_as_one_word_per_word_sent(
    voice_server: VoiceServer,
) -> None:
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url))
    wav = a_wav(2.0)
    words = aligner.align(AlignmentRequest(wav=wav, words=SAID))
    assert voice_server.last_wav == wav
    assert voice_server.last_request == {"words": list(SAID)}
    assert [(word.word, word.start, word.end) for word in words] == [
        ("A", 0.0, 0.5),
        ("bowl", 0.5, 1.0),
        ("a", 1.0, 1.5),
        ("ball", 1.5, 2.0),
    ]


@pytest.mark.parametrize(
    ("reply", "said"),
    [
        ({"words": [{"word": "A", "start": 0.0, "end": 0.5}]}, "answered 1 words for the 4 it was sent"),
        (
            {"words": [{"word": w, "start": 0.0, "end": 0.5} for w in ("A", "bowl", "a", "BALL")]},
            "answered 'BALL' where it was sent 'ball'",
        ),
    ],
)
def test_a_reply_that_is_not_one_word_per_word_sent_spelled_as_sent_is_a_provider_error(
    voice_server: VoiceServer, reply: dict[str, Any], said: str
) -> None:
    voice_server.align_reply = reply
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url))
    with pytest.raises(ProviderError, match=said) as caught:
        aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    assert caught.value.reached is True and caught.value.retryable is False


def test_a_reply_with_fields_a_newer_server_adds_is_read(voice_server: VoiceServer) -> None:
    voice_server.align_reply = {
        "words": [{"word": w, "start": 0.1234, "end": 0.5, "confidence": None, "phones": []} for w in SAID],
        "elapsed_seconds": 0.2,
    }
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url))
    words = aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    assert [word.word for word in words] == list(SAID)
    assert words[0].start == 0.123


def test_a_busy_aligner_is_asked_again_after_the_wait_it_names(voice_server: VoiceServer, waits: list[float]) -> None:
    voice_server.align_busy = 1
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url, retries=2))
    assert len(aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))) == len(SAID)
    assert voice_server.align_calls == 2 and waits == [0.0]


def test_an_aligner_still_busy_after_its_retries_raises_a_retryable_failure(
    voice_server: VoiceServer, waits: list[float]
) -> None:
    voice_server.align_status = 503
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url, retries=2))
    with pytest.raises(ProviderError, match="MODEL_LOADING") as caught:
        aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    assert caught.value.retryable is True
    assert voice_server.align_calls == 3 and len(waits) == 2


def test_a_broken_reply_from_the_aligner_is_asked_for_again_and_never_called_charged(
    voice_server: VoiceServer, monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url, retries=1))

    def hangs_up(_req: urllib.request.Request, **_options: float) -> HTTPResponse:
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(speech_http, "urlopen", hangs_up)
    with pytest.raises(ProviderError) as caught:
        aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    said = f"{caught.value} {caught.value.hint}"
    assert "charged" not in said
    assert caught.value.possibly_charged is False and caught.value.retryable is True
    assert len(waits) == 1


def test_a_reply_from_the_aligner_that_is_not_json_fails_and_is_not_down(
    voice_server: VoiceServer, waits: list[float]
) -> None:
    voice_server.align_reply = b"<html>not json</html>"
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url, retries=2))
    with pytest.raises(ProviderError, match="not JSON") as caught:
        aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    assert caught.value.reached is True and caught.value.retryable is False
    assert voice_server.align_calls == 1 and waits == []


def test_a_word_outside_the_aligners_vocabulary_is_a_provider_error_naming_its_code(
    voice_server: VoiceServer,
) -> None:
    voice_server.align_status, voice_server.align_code = 422, "WORD_OUTSIDE_VOCABULARY"
    aligner = DtspAligner.for_context(aligner_context(voice_server.base_url, retries=2))
    with pytest.raises(ProviderError, match="WORD_OUTSIDE_VOCABULARY") as caught:
        aligner.align(AlignmentRequest(wav=a_wav(1.0), words=SAID))
    assert caught.value.reached is True and caught.value.retryable is False
    assert voice_server.align_calls == 1


# ---- a voice that sends no word times ----------------------------------------------------------


@pytest.mark.parametrize("words", [None, []])
def test_a_take_with_no_word_times_is_kept_for_the_aligner_to_time(
    service: Service, server: LocalServer, words: list[Any] | None
) -> None:
    server.reply = {"audio_base64": base64.b64encode(AUDIO).decode(), "format": "mp3"}
    if words is not None:
        server.reply["words"] = words
    speech = Dtsp(context(service.url_for(""))).speak(request())
    assert speech.audio == AUDIO
    assert speech.words is None


def test_a_reply_whose_words_are_not_a_list_is_a_provider_error(service: Service, server: LocalServer) -> None:
    server.reply = {"audio_base64": base64.b64encode(AUDIO).decode(), "format": "mp3", "words": "A bowl"}
    with pytest.raises(ProviderError, match="no word times"):
        Dtsp(context(service.url_for(""))).speak(request())


def test_the_model_the_server_served_is_read_off_its_reply(voice_server: VoiceServer) -> None:
    voice_server.model_digest = "sha256:5eed"
    assert Dtsp(context(voice_server.base_url)).speak(request()).model_digest == "sha256:5eed"
    voice_server.model_digest = None
    assert Dtsp(context(voice_server.base_url)).speak(request()).model_digest is None
