"""Where the ElevenLabs key may go: only to an https ElevenLabs host, never across a redirect, never into output."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request
from dataclasses import asdict, fields

import pytest

from decktalk.errors import ConfigError, ProviderError
from decktalk.jsonio import as_json
from decktalk.model import Project
from decktalk.model.env import Env
from decktalk.secret import Secret
from decktalk.settings import ElevenLabsConfig, Settings
from decktalk.speech import VoiceContext
from decktalk.speech import http as _http
from decktalk.speech.elevenlabs import ALLOW_ANY_API_BASE, ElevenLabs, check_api_base
from decktalk.stages.narrate import narrate

SENTINEL = "sk_sentinel_key_that_must_never_print"
VOICE_SENTINEL = "voice_sentinel_that_must_never_print"
# The reply the loopback service sends, as one format so a test can place the key at an exact offset.
_ECHO_PREFIX = '{"detail": "%sinvalid api key '

# ---- api_base ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "https://api.elevenlabs.io/v1",
        "https://api.us.elevenlabs.io/v1",
        "https://ELEVENLABS.IO/v1",
        "https://elevenlabs.io",
    ],
)
def test_an_https_elevenlabs_host_passes(base):
    assert check_api_base(base, environ={}) == base


@pytest.mark.parametrize(
    "base",
    [
        "http://api.elevenlabs.io/v1",  # not https
        "https://evil.test/v1",  # another host
        "https://elevenlabs.io.evil.test/v1",  # the domain as a prefix of another
        "https://notelevenlabs.io/v1",  # the domain as a suffix without its dot
        "https://api.elevenlabs.io@evil.test/v1",  # userinfo that reads like the right host
        "https://evil.test/api.elevenlabs.io/v1",  # the domain in the path
        "api.elevenlabs.io/v1",  # no scheme
        "",
    ],
)
def test_anything_else_is_a_config_error_that_names_the_override(base):
    with pytest.raises(ConfigError, match=ALLOW_ANY_API_BASE):
        check_api_base(base, environ={})


def test_the_environment_override_allows_any_base():
    assert check_api_base("http://127.0.0.1:8000/v1", environ={ALLOW_ANY_API_BASE: "1"}) == "http://127.0.0.1:8000/v1"
    for off in ("", "0", "false", "no"):
        with pytest.raises(ConfigError):
            check_api_base("http://127.0.0.1:8000/v1", environ={ALLOW_ANY_API_BASE: off})


def test_the_provider_refuses_a_foreign_base_before_any_request(monkeypatch):
    """The refusal names the rule and the switch, and never the value, which may hold a path token."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    with pytest.raises(ConfigError, match="must be an https URL on elevenlabs.io") as caught:
        ElevenLabs(
            api_key=Secret(SENTINEL),
            cfg=ElevenLabsConfig(api_base="https://evil.test/v1/SUPERSECRETTOKEN"),
            voice=Secret("v"),
        )
    assert "evil.test" not in str(caught.value) and "SUPERSECRETTOKEN" not in str(caught.value)
    monkeypatch.setenv(ALLOW_ANY_API_BASE, "1")
    ElevenLabs(api_key=Secret(SENTINEL), cfg=ElevenLabsConfig(api_base="https://evil.test/v1"), voice=Secret("v"))


def test_a_project_file_cannot_redirect_the_key(tmp_path, monkeypatch):
    """A decktalk.toml written by someone else is a normal input, so its api_base is checked like any other."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[elevenlabs]\napi_base = 'https://evil.test/v1'\n[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(f"ELEVENLABS_API_KEY={SENTINEL}\nELEVENLABS_VOICE_ID=v\n", encoding="utf-8")
    project = Project.load(tmp_path, environ={})
    context = VoiceContext(settings=project.settings, secrets=project.env)
    with pytest.raises(ConfigError, match="must be an https URL on elevenlabs.io") as info:
        ElevenLabs.for_context(context)
    assert SENTINEL not in str(info.value)


# ---- redirects ---------------------------------------------------------------------------------


def _redirected(to: str, headers: dict[str, str]) -> urllib.request.Request:
    """The request urllib would make next, after a 302 from the API to `to`."""
    start = "https://api.elevenlabs.io/v1/text-to-speech/v/with-timestamps"
    moved = _http.DropAuthAcrossOrigins().redirect_request(
        urllib.request.Request(start, headers=headers), None, 302, "Found", {}, to
    )
    assert moved is not None and moved.full_url == to
    return moved


@pytest.mark.parametrize(
    "to",
    [
        "https://evil.test/collect",  # another host
        "http://api.elevenlabs.io/v1/x",  # the same host without TLS
        "https://api.elevenlabs.io:8443/v1/x",  # the same host on another port
        "https://eu.elevenlabs.io/v1/x",  # another host of the same domain
    ],
)
def test_no_credential_header_follows_a_redirect_to_another_origin(to):
    """A credential belongs to a scheme, a host and a port together, and to nothing else."""
    headers = {
        "xi-api-key": SENTINEL,
        "Authorization": "Bearer x",
        "Proxy-Authorization": "Basic y",
        "Cookie": "session=z",
        "Accept": "audio/mpeg",
    }
    moved = _redirected(to, headers)
    for name in _http.AUTH_HEADERS:
        assert not moved.has_header(name.capitalize()), name
    assert moved.get_header("Accept") == "audio/mpeg"


def test_the_credential_headers_survive_a_redirect_inside_one_origin():
    """A redirect to another path of the API is the ordinary case and keeps the request whole."""
    headers = {"xi-api-key": SENTINEL, "Authorization": "Bearer x", "Accept": "audio/mpeg"}
    same = _redirected("https://api.elevenlabs.io/v2/x", headers)
    assert same.get_header("Xi-api-key") == SENTINEL and same.get_header("Authorization") == "Bearer x"
    explicit = _redirected("https://api.elevenlabs.io:443/v2/x", headers)
    assert explicit.get_header("Xi-api-key") == SENTINEL  # the default port is the same origin


class _Server(http.server.ThreadingHTTPServer):
    """Records the headers of every request. A path under /to/<host>/ answers 302 to that host's /landed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, dict[str, str]]] = []

    daemon_threads = True


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # keeps the test output quiet
        pass

    def _serve(self) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        server.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/echo/"):
            # A service that names the key it refused, which is the case the scrubber exists for.
            pad = "." * getattr(server, "pad", 0)
            body = (_ECHO_PREFIX % pad + (self.headers.get("xi-api-key") or "") + '"}').encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/to/"):
            host = self.path.removeprefix("/to/").split("/")[0]
            self.send_response(302)
            self.send_header("Location", f"http://{host}:{server.server_port}/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _serve
    do_POST = _serve


@pytest.fixture
def server():
    srv = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_real_redirect_to_another_host_never_carries_the_key(server):
    """127.0.0.1 and localhost are the same machine and two different hosts, which is all a redirect needs."""
    port = server.server_port
    headers = {"xi-api-key": SENTINEL, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    assert _http.post_json(f"http://127.0.0.1:{port}/to/localhost/", {"text": "hi"}, headers, timeout=5) == {"ok": True}
    (first_path, first), (landed_path, landed) = server.seen
    assert first_path == "/to/localhost/" and first["xi-api-key"] == SENTINEL
    assert landed_path == "/landed" and "xi-api-key" not in landed and landed["accept"] == "audio/mpeg"
    server.seen.clear()
    assert _http.get_json(f"http://127.0.0.1:{port}/to/127.0.0.1/", headers, timeout=5) == {"ok": True}
    (_, first), (_, landed) = server.seen
    assert first["xi-api-key"] == SENTINEL and landed["xi-api-key"] == SENTINEL  # the same host keeps it


def test_the_token_after_an_authorization_scheme_is_scrubbed_on_its_own():
    """A reply quotes the token far more often than it quotes the whole `Bearer x` header value."""
    token = "tok_0123456789abcdef"
    text = _http.scrub(f"refused {token}", {"Authorization": f"Bearer {token}"})
    assert text == f"refused {_http.CREDENTIAL}" and token not in text
    # The whole header value is taken out too, for a reply that quotes the header as it was sent.
    whole = _http.scrub(f"sent Bearer {token}", {"Authorization": f"Bearer {token}"})
    assert token not in whole
    # A header that is not a credential is left alone, so an ordinary value is not blanked out.
    assert _http.scrub("accept audio/mpeg", {"Accept": "audio/mpeg"}) == "accept audio/mpeg"


def test_a_reply_that_echoes_the_key_never_reaches_the_error(server):
    """The body is written by whatever host `api_base` names, so the quote is scrubbed before it is used."""
    port = server.server_port
    headers = {"xi-api-key": SENTINEL, "Authorization": f"Bearer {SENTINEL}", "Content-Type": "application/json"}
    with pytest.raises(ProviderError) as info:
        _http.post_json(f"http://127.0.0.1:{port}/echo/", {"text": "hi"}, headers, timeout=5)
    message = str(info.value)
    assert SENTINEL not in message
    assert _http.CREDENTIAL in message and "401" in message

    # The whole body is scrubbed before it is cut, so a key that straddles the cut leaves no prefix.
    # The padding puts the key's first twelve characters just inside the cut and the rest beyond it.
    server.pad = _http.BODY_CHARS - len(_ECHO_PREFIX % "") - 12
    with pytest.raises(ProviderError) as info:
        _http.post_json(f"http://127.0.0.1:{port}/echo/", {"text": "hi"}, headers, timeout=5)
    straddled = str(info.value)
    assert SENTINEL[:12] not in straddled, straddled[-160:]
    assert _http.CREDENTIAL in straddled


# ---- the key in output -------------------------------------------------------------------------


def test_the_key_survives_no_walk_over_the_provider_or_the_environment(tmp_path, monkeypatch):
    """`repr=False` hides a value from `repr` alone, so the key is held in a `Secret` instead."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    provider = ElevenLabs(
        api_key=Secret(SENTINEL, "ELEVENLABS_API_KEY"),
        cfg=ElevenLabsConfig(),
        voice=Secret(VOICE_SENTINEL, "ELEVENLABS_VOICE_ID"),
    )
    # Both values this provider reads from `.env` are covered, because the rule covers every one.
    for shown in (repr(provider), str(provider), f"{provider!r}", repr(provider.api_key), str(provider.voice)):
        assert SENTINEL not in shown and VOICE_SENTINEL not in shown
    # The two walks that a result, an artifact or an envelope would use.
    with pytest.raises(TypeError):  # asdict deep-copies, and a secret refuses to be copied out
        asdict(provider)
    walked = json.dumps(as_json(provider), default=str)
    assert SENTINEL not in walked and VOICE_SENTINEL not in walked
    with pytest.raises(TypeError):  # a writer refuses a secret rather than writing it
        json.dumps(as_json(provider))
    assert provider._headers()["xi-api-key"] == SENTINEL  # the one place the key is revealed

    env = Env(tmp_path / ".env", {"ELEVENLABS_API_KEY": SENTINEL, "PATH": "/bin"})
    for shown in (repr(env), str(env), json.dumps(asdict(env), default=str), json.dumps(as_json(env), default=str)):
        assert SENTINEL not in shown and "PATH" not in shown
    assert [f.name for f in fields(Env)] == ["file"]  # the environment is not a field, so no walk reaches it
    assert env.require("ELEVENLABS_API_KEY")[0].reveal() == SENTINEL

    exported = json.dumps(asdict(Settings()))
    assert "api_key" not in exported and "key" not in set(asdict(ElevenLabsConfig()))
    assert SENTINEL not in exported


def test_no_file_a_run_writes_can_hold_the_key(tmp_path, monkeypatch):
    """No value read from `.env` reaches a payload, a log line or an error, whatever a run writes."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[project]\nname = 't'\n[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        f"ELEVENLABS_API_KEY={SENTINEL}\nELEVENLABS_VOICE_ID={VOICE_SENTINEL}\n", encoding="utf-8"
    )
    project = Project.load(tmp_path, environ={})
    assert SENTINEL not in json.dumps(as_json(project.document), default=str)
    assert SENTINEL not in json.dumps(as_json(project.settings), default=str)
    assert SENTINEL not in repr(project)
    narrate(project, silent=True)
    written = [p for p in (tmp_path / "build").rglob("*") if p.is_file()]
    assert written
    for path in written:
        assert SENTINEL.encode() not in path.read_bytes(), path
        assert VOICE_SENTINEL.encode() not in path.read_bytes(), path
