"""Where a credential may go: never across a redirect, never into an error, and never without a deadline."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request

import pytest

from decktalk.errors import ProviderError
from decktalk.speech import http as _http

SENTINEL = "sk_sentinel_key_that_must_never_print"
STOP_POLL_SECONDS = 0.05
"""How often the loopback service checks whether it was asked to stop, where the default half second
made every test that used it wait that long to finish."""

# The reply the loopback service sends, as one format so a test can place the key at an exact offset.
_ECHO_PREFIX = '{"detail": "%sinvalid api key '


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
        if self.path.startswith("/busy/"):
            # A service that is busy for the first N requests and answers after that, which is
            # what a voice account at its concurrency limit does.
            busy, _, status = self.path.removeprefix("/busy/").partition("/")
            server.busy_seen = getattr(server, "busy_seen", 0) + 1
            if server.busy_seen <= int(busy):
                self.send_response(int(status or 429))
                if getattr(server, "retry_after", None) is not None:
                    self.send_header("Retry-After", server.retry_after)
                self.send_header("Content-Length", "0")
                self.end_headers()
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
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": STOP_POLL_SECONDS}, daemon=True)
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
    assert _http.post_json(f"http://127.0.0.1:{port}/to/localhost/", {"text": "hi"}, headers, timeout=5, retries=0) == {
        "ok": True
    }
    (first_path, first), (landed_path, landed) = server.seen
    assert first_path == "/to/localhost/" and first["xi-api-key"] == SENTINEL
    assert landed_path == "/landed" and "xi-api-key" not in landed and landed["accept"] == "audio/mpeg"
    server.seen.clear()
    assert _http.post_json(f"http://127.0.0.1:{port}/to/127.0.0.1/", {}, headers, timeout=5, retries=0) == {"ok": True}
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
        _http.post_json(f"http://127.0.0.1:{port}/echo/", {"text": "hi"}, headers, timeout=5, retries=0)
    message = str(info.value)
    assert SENTINEL not in message
    assert _http.CREDENTIAL in message and "401" in message

    # The whole body is scrubbed before it is cut, so a key that straddles the cut leaves no prefix.
    # The padding puts the key's first twelve characters just inside the cut and the rest beyond it.
    server.pad = _http.BODY_CHARS - len(_ECHO_PREFIX % "") - 12
    with pytest.raises(ProviderError) as info:
        _http.post_json(f"http://127.0.0.1:{port}/echo/", {"text": "hi"}, headers, timeout=5, retries=0)
    straddled = str(info.value)
    assert SENTINEL[:12] not in straddled, straddled[-160:]
    assert _http.CREDENTIAL in straddled


# ---- what a caller is told ------------------------------------------------------------------------


def test_a_url_in_a_message_carries_no_query_string(server):
    """A query string says nothing a reader can act on, and the path is what names the call."""
    port = server.server_port
    with pytest.raises(ProviderError) as caught:
        _http.post_json(f"http://127.0.0.1:{port}/echo/?output_format=mp3_44100_128", {}, {}, timeout=5, retries=0)
    assert "output_format" not in str(caught.value)
    assert _http.shown("https://x.test/a/b?c=d#e") == "https://x.test/a/b"


def test_a_host_that_cannot_be_reached_is_worth_trying_again():
    """Nothing about the request was wrong, so a caller that waits and retries is right to."""
    with pytest.raises(ProviderError) as caught:
        _http.post_json("http://127.0.0.1:1/never", {}, {}, timeout=1, retries=0)
    assert caught.value.retryable is True
    assert "could not reach http://127.0.0.1:1/never" in str(caught.value)


def test_a_reply_that_is_not_an_object_is_refused_rather_than_read(monkeypatch):
    """The caller reads fields off what comes back, so a list or a number is refused where it arrives."""
    monkeypatch.setattr(_http, "post_bytes", lambda *_a, **_k: b"[1, 2, 3]")
    with pytest.raises(ProviderError, match="rather than an object"):
        _http.post_json("https://x.test/a", {}, {}, timeout=5, retries=0)
    monkeypatch.setattr(_http, "post_bytes", lambda *_a, **_k: b"<html>not json</html>")
    with pytest.raises(ProviderError, match="not JSON"):
        _http.post_json("https://x.test/a", {}, {}, timeout=5, retries=0)


def test_every_call_carries_the_timeout_it_was_given(server, monkeypatch):
    """A provider that stopped answering would otherwise hold a build open for as long as the socket did."""
    port = server.server_port
    seen: list[int] = []
    real = _http.urlopen

    def timed(request, *, timeout):  # noqa: ANN001, ANN202  (the opener's own signature)
        seen.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr(_http, "urlopen", timed)
    _http.post_bytes(f"http://127.0.0.1:{port}/plain", {}, {}, timeout=7, retries=0)
    assert seen == [7]


# ---- a busy service ---------------------------------------------------------------------------------


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every wait a retry asked for, with none of them slept, so the suite does not wait on a back-off."""
    asked: list[float] = []
    monkeypatch.setattr(_http, "pause", asked.append)
    return asked


def test_a_busy_service_is_asked_again_until_it_answers(server, waits):
    port = server.server_port
    assert _http.post_json(f"http://127.0.0.1:{port}/busy/2/429", {}, {}, timeout=5, retries=3) == {"ok": True}
    assert server.busy_seen == 3
    assert waits == [_http.FIRST_WAIT_SECONDS, 2 * _http.FIRST_WAIT_SECONDS]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_failure_on_the_services_side_is_asked_again(server, waits, status):
    port = server.server_port
    assert _http.post_json(f"http://127.0.0.1:{port}/busy/1/{status}", {}, {}, timeout=5, retries=1) == {"ok": True}
    assert len(waits) == 1


def test_the_last_busy_answer_is_raised_once_the_retries_are_spent(server, waits):
    port = server.server_port
    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        _http.post_json(f"http://127.0.0.1:{port}/busy/9/429", {}, {}, timeout=5, retries=2)
    assert caught.value.retryable is True
    assert server.busy_seen == 3 and len(waits) == 2


def test_a_request_the_service_refused_as_wrong_is_never_sent_again(server, waits):
    """A refusal about the request itself would be refused again, and each sending may be billed."""
    port = server.server_port
    with pytest.raises(ProviderError, match="HTTP 400"):
        _http.post_json(f"http://127.0.0.1:{port}/busy/9/400", {}, {}, timeout=5, retries=3)
    assert server.busy_seen == 1 and waits == []


def test_the_wait_a_service_names_is_the_wait_taken_and_held_under_the_longest(server, waits):
    port = server.server_port
    server.retry_after = "7"
    _http.post_json(f"http://127.0.0.1:{port}/busy/1/429", {}, {}, timeout=5, retries=1)
    assert waits == [7.0]
    assert _http.wait_before(0, "86400") == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(10, None) == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(1, "a date rather than seconds") == 2 * _http.FIRST_WAIT_SECONDS
