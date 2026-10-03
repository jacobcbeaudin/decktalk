"""The `dtsp` voice: a free, keyless voice on a local server, reached on a loopback address and nowhere else.

The server is a local fake on a socket of its own, which answers `/v1/speech/timed` the way the
DeckTalk speech protocol says, so the request the adapter sends and the reply it reads are the real
ones. The end-to-end test drives the real command line against it, with no key anywhere.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from werkzeug import Request, Response

import decktalk
from decktalk.artifacts.takes import TakeInputs
from decktalk.cli import main
from decktalk.errors import InputError, ProviderError
from decktalk.findings import Code
from decktalk.machine import Machine
from decktalk.media import audio
from decktalk.results import Billing, TakeStatus, Word
from decktalk.settings import ALLOW_ANY_API_BASE, CONFIG_VARIABLE, DtspConfig, Settings
from decktalk.speech import (
    DECLARED,
    PROVIDERS,
    Piece,
    SpeechRequest,
    VoiceContext,
    Voices,
    billing_of,
    key_variable,
    output_of,
    renders_pauses,
)
from decktalk.speech import http as speech_http
from decktalk.speech.dtsp import OUTPUT, SPEECH_PATH, Dtsp
from support.service import Service
from support.speech import NoSecrets

AUDIO = b"ID3 a local take"
VOICE = "af_heart"
MODEL = DtspConfig().model


def context(base: str, **over: Any) -> VoiceContext:
    """What a run hands the adapter: the base its table names, a timeout and no secret it may read."""
    fields: dict[str, Any] = {
        "secrets": NoSecrets(),
        "api_base": base,
        "context_chars": 0,
        "speech_timeout_seconds": 5,
        **over,
    }
    return VoiceContext(**fields)


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
    assert billing_of("dtsp").by is Billing.FREE
    assert key_variable("dtsp") is None
    assert renders_pauses("dtsp", "any-model-the-server-has")
    assert output_of(Settings(), "dtsp") == OUTPUT


def test_its_takes_never_share_a_name_with_an_elevenlabs_take_of_the_same_words():
    """The adapter's name opens every digest, so one sentence voiced by each is two takes."""
    same = {"voice": VOICE, "model": MODEL, "output_format": "mp3_44100_128", "settings": {"speed": 1.0}, "text": "Hi"}
    assert TakeInputs.of(provider="dtsp", **same).digest != TakeInputs.of(provider="elevenlabs", **same).digest
    assert DECLARED["dtsp"].identity(DtspConfig(), 1.1) == {"speed": 1.1}


# ---- where it may send a script --------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    ["http://127.0.0.1:8765", "http://localhost:9000/", "http://[::1]:8765", "http://LOCALHOST", "http://127.0.0.1"],
)
def test_a_loopback_url_over_http_is_allowed(base: str):
    assert Dtsp(context(base)).checked_base == base.rstrip("/")


@pytest.mark.parametrize(
    "base",
    [
        "http://example.com:8765",  # another machine
        "https://127.0.0.1:8765",  # loopback, but not the scheme it declares
        "http://10.0.0.5:8765",  # a private address is still another machine
        "http://127.0.0.1.evil.test",  # the loopback name as a prefix of another host
        "http://localhost@evil.test",  # userinfo that reads like the right host
        "http://evil.test/127.0.0.1",  # the address in the path
        "http://evil.test\\@127.0.0.1:8765",  # a backslash urllib reads as part of another host
        "http://user@127.0.0.1:8765",  # userinfo, which the right host does not need
        "http://[::1",  # a host this parser cannot read
        "127.0.0.1:8765",  # no scheme
        "",
    ],
)
def test_any_other_url_is_refused_with_a_sentence_that_names_the_key_and_the_switch(base: str):
    with pytest.raises(InputError) as refused:
        Dtsp(context(base))
    said = f"{refused.value} {refused.value.hint}"
    assert str(refused.value) == "[dtsp] url must be an http URL on 127.0.0.1, [::1] or localhost."
    assert ALLOW_ANY_API_BASE in said
    assert "evil.test" not in said and "example.com" not in said


def test_the_machines_switch_and_only_the_machines_switch_allows_another_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ALLOW_ANY_API_BASE, "1")
    with pytest.raises(InputError):
        Dtsp(context("http://example.com:8765"))
    built = Voices(factories=PROVIDERS, allow_any_api_base=True).provider("dtsp", context("http://example.com:8765"))
    assert isinstance(built, Dtsp) and built.checked_base == "http://example.com:8765"


# ---- the request and the reply -----------------------------------------------------------------


def test_one_section_is_sent_as_pieces_with_their_pauses_and_read_back_as_audio_and_words(
    service: Service, server: LocalServer
):
    audio_bytes, words = Dtsp(context(service.url_for(""))).speak(request())
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
    assert [word.word for word in words] == ["A", "bowl", "A", "ball", "It", "rests"]
    assert (words[1].start, words[1].end) == (0.1, 0.19)


@pytest.mark.parametrize(
    ("reply", "said"),
    [
        ({"format": "mp3", "words": [{"word": "a", "start": 0, "end": 1}]}, "no audio"),
        ({"audio_base64": "aGk=", "format": "wav", "words": [{"word": "a", "start": 0, "end": 1}]}, "'wav'"),
        ({"audio_base64": "aGk=", "format": "mp3"}, "no word times"),
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

[dtsp]
url = "{url}"

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


def lesson(root: Path, url: str) -> Path:
    (root / "deck").mkdir(parents=True)
    (root / "deck" / "index.html").write_text("<p>deck</p>", encoding="utf-8")
    (root / "decktalk.toml").write_text(LESSON_TOML.format(voice=VOICE, url=url), encoding="utf-8")
    (root / "script.md").write_text(LESSON_SCRIPT, encoding="utf-8")
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
    for name in ("DECKTALK_VOICE_ID", "DECKTALK_PROJECT", ALLOW_ANY_API_BASE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(CONFIG_VARIABLE, str(tmp_path / "no-machine.toml"))
    monkeypatch.setenv("TTY_COMPATIBLE", "0")
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)


@pytest.mark.usefixtures("fake_ffmpeg", "machine_without_a_key")
def test_narrate_voices_a_lesson_through_the_local_server_with_no_key_and_no_spend_asked(
    tmp_path: Path, service: Service, server: LocalServer, monkeypatch: pytest.MonkeyPatch
):
    """The free rule end to end: no `--spend`, no terminal, no key, and every section is voiced."""
    monkeypatch.chdir(lesson(tmp_path / "local", service.url_for("")))
    code, out, err = command("narrate", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert [row["status"] for row in result["sections"]] == [TakeStatus.VOICED.value] * 2
    assert result["spending"] is True
    assert result["spend"]["billing"] == Billing.FREE.value and result["spend"]["dollars"] == 0
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
    monkeypatch.chdir(lesson(tmp_path / "local", service.url_for("")))
    code, out, err = command("narrate", "--no-spend", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert result["spending"] is False
    assert [row["status"] for row in result["sections"]] == [TakeStatus.VOICED.value] * 2
    assert [found["code"] for found in result["findings"]] == []
    assert len(server.requests) == 2


def closed_port() -> str:
    """A loopback address nothing listens on, found by binding a port and letting it go."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.mark.usefixtures("fake_ffmpeg", "machine_without_a_key", "waits")
@pytest.mark.parametrize("spending", ["--no-spend", "--spend"])
def test_a_local_server_that_is_not_running_plays_placeholders_and_says_to_start_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spending: str
):
    """A free voice that is down never fails the run: each section plays a placeholder whose hint starts the server."""
    monkeypatch.chdir(lesson(tmp_path / "down", closed_port()))
    code, out, err = command("narrate", spending, "--json")
    assert code == 0, err
    result = json.loads(out)
    assert [row["status"] for row in result["sections"]] == [TakeStatus.PLACEHOLDER.value] * 2
    assert [found["code"] for found in result["findings"]] == [Code.TAKE_MISSING.value] * 2
    for found in result["findings"]:
        assert "Start decktalk-voice" in found["message"]
        assert "[dtsp] url" in found["message"]
        assert "--spend" not in found["message"]


@pytest.mark.usefixtures("machine_without_a_key")
def test_check_refuses_a_url_that_is_not_this_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`check` judges where the script would go before it plans or prices anything."""
    monkeypatch.chdir(lesson(tmp_path / "remote", "http://speech.example.com:8765"))
    code, out, _ = command("check", "--no-pages", "--json")
    refused = json.loads(out)
    assert code != 0 and refused["ok"] is False
    assert refused["error"]["message"] == "[dtsp] url must be an http URL on 127.0.0.1, [::1] or localhost."
    assert "example.com" not in out


@pytest.mark.usefixtures("machine_without_a_key")
def test_check_leaves_a_voice_the_host_registered_under_a_shipped_name_alone(tmp_path: Path):
    """A host's own voice builds its own requests, so the shipped adapter's hosts do not judge it."""
    root = lesson(tmp_path / "hosted", "http://speech.example.com:8765")
    here = Machine.of(
        environ={}, config_path=tmp_path / "m.toml", cwd=root, cache_dir=tmp_path / "c", providers={"dtsp": Silent}
    )
    assert decktalk.open(root, machine=here).check(pages=False, frames=False).ok
    shipped = Machine.of(environ={}, config_path=tmp_path / "m.toml", cwd=root, cache_dir=tmp_path / "c")
    with pytest.raises(InputError, match=r"\[dtsp\] url"):
        decktalk.open(root, machine=shipped).check(pages=False, frames=False)


class Silent:
    """A host's voice, which this test never asks to speak."""

    name = "dtsp"

    def __init__(self, _context: VoiceContext) -> None:
        pass

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        raise AssertionError(f"check spoke {request.pieces}")
