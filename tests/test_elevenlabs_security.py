"""Where the ElevenLabs key may go: only to an https ElevenLabs host, never across a redirect, never into output."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request
from dataclasses import asdict, fields

import pytest

from decktalk.config import ElevenLabsConfig, Settings
from decktalk.errors import ConfigError
from decktalk.project import Project
from decktalk.providers import _http
from decktalk.providers.elevenlabs import ALLOW_ANY_API_BASE, ElevenLabs, check_api_base

SENTINEL = "sk_sentinel_key_that_must_never_print"

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
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    with pytest.raises(ConfigError, match="evil.test"):
        ElevenLabs(api_key=SENTINEL, cfg=ElevenLabsConfig(api_base="https://evil.test/v1"), voice_id="v")
    monkeypatch.setenv(ALLOW_ANY_API_BASE, "1")
    ElevenLabs(api_key=SENTINEL, cfg=ElevenLabsConfig(api_base="https://evil.test/v1"), voice_id="v")


def test_a_project_file_cannot_redirect_the_key(tmp_path, monkeypatch):
    """A decktalk.toml written by someone else is a normal input, so its api_base is checked like any other."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[elevenlabs]\napi_base = 'https://evil.test/v1'\n[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(f"ELEVENLABS_API_KEY={SENTINEL}\nELEVENLABS_VOICE_ID=v\n", encoding="utf-8")
    project = Project.load(tmp_path, environ={})
    with pytest.raises(ConfigError, match="evil.test") as info:
        ElevenLabs.for_project(project)
    assert SENTINEL not in str(info.value)


# ---- redirects ---------------------------------------------------------------------------------


def test_the_redirect_handler_drops_auth_headers_only_when_the_host_changes():
    handler = _http.DropAuthAcrossHosts()
    headers = {"xi-api-key": SENTINEL, "Authorization": "Bearer x", "Accept": "audio/mpeg"}
    origin = "https://api.elevenlabs.io/v1/text-to-speech/v/with-timestamps"
    moved = handler.redirect_request(
        urllib.request.Request(origin, headers=headers), None, 302, "Found", {}, "https://evil.test/collect"
    )
    assert moved is not None and moved.full_url == "https://evil.test/collect"
    assert not moved.has_header("Xi-api-key") and not moved.has_header("Authorization")
    assert moved.get_header("Accept") == "audio/mpeg"
    same = handler.redirect_request(
        urllib.request.Request(origin, headers=headers), None, 302, "Found", {}, "https://api.elevenlabs.io/v2/x"
    )
    assert (
        same is not None
        and same.get_header("Xi-api-key") == SENTINEL
        and same.get_header("Authorization") == "Bearer x"
    )


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


# ---- the key in output -------------------------------------------------------------------------


def test_the_key_never_appears_in_repr_or_in_any_config_export():
    provider = ElevenLabs(api_key=SENTINEL, cfg=ElevenLabsConfig(), voice_id="v")
    assert SENTINEL not in repr(provider) and SENTINEL not in str(provider) and SENTINEL not in f"{provider!r}"
    assert "api_key" not in repr(provider)
    assert [f.name for f in fields(ElevenLabs) if f.name == "api_key"] == ["api_key"]
    assert [f.name for f in fields(ElevenLabs) if f.repr][0] == "cfg"
    exported = json.dumps(asdict(Settings()))
    assert "api_key" not in exported and "key" not in {k for k in asdict(ElevenLabsConfig())}
    assert SENTINEL not in exported
