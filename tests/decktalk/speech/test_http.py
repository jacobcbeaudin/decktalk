"""Where a credential may go: never across a redirect, never into an error, and never without a deadline."""

from __future__ import annotations

import http.client
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import NoReturn

import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from decktalk.errors import ProviderError
from decktalk.secret import Secret, redact
from decktalk.speech import http as _http
from support.logs import data_of

SENTINEL = "sk_sentinel_key_that_must_never_print"

UNKNOWN_NAME = "FAKE_VENDOR_ACCESS"
"""A variable name the secret registry does not take for a credential, so only `http.py` guards its value."""


def unregistered(value: str) -> Secret:
    """A key the process-wide registry never saw, so a test of it proves what `http.py` does on its own."""
    held = Secret(value, UNKNOWN_NAME)
    assert redact(value) == value, "the registry holds this value, so it would hide what http.py misses"
    return held


KEY = unregistered(SENTINEL)


@dataclass
class Vendor:
    """A provider that sends its key as `X-API-Key`, the way Cartesia does, a name no header list held."""

    base: str
    api_key: Secret

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key.reveal(), "Content-Type": "application/json", "Accept": "audio/mpeg"}

    def speak(self, path: str) -> dict[str, object]:
        url = f"{self.base.rstrip('/')}{path}"
        return _http.post_json(url, {"text": "hi"}, self._headers(), secrets=(self.api_key,), timeout=5, retries=0)


# The reply the loopback service sends, as one format so a test can place the key at an exact offset.
_ECHO_PREFIX = '{"detail": "%sinvalid api key '


def _redirected(to: str, headers: dict[str, str], secrets: tuple[Secret, ...] = (KEY,)) -> urllib.request.Request:
    """The request urllib would make next, after a 302 from the API to `to`."""
    start = "https://api.elevenlabs.io/v1/text-to-speech/v/with-timestamps"
    sent = _http.Sending(start, credentials=_http.credentials(headers, secrets), headers=headers)
    moved = _http.DropAuthAcrossOrigins().redirect_request(sent, None, 302, "Found", {}, to)
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
    """A credential belongs to a scheme, a host and a port together, and to nothing else.

    The key travels under a name no list knows, and the standard auth headers carry values that no
    secret names, so each of the two rules is what drops its own headers.
    """
    headers = {
        "X-Vendor-Key": SENTINEL,
        "X-Signed": f"v1:{SENTINEL}",
        "Authorization": "Bearer x",
        "Proxy-Authorization": "Basic y",
        "Cookie": "session=z",
        "Accept": "audio/mpeg",
    }
    moved = _redirected(to, headers)
    for name in ("X-Vendor-Key", "X-Signed", "Authorization", "Proxy-Authorization", "Cookie"):
        assert not moved.has_header(name.capitalize()), name
    assert moved.get_header("Accept") == "audio/mpeg"
    assert isinstance(moved, _http.Sending) and SENTINEL in moved.credentials  # a further redirect is judged alike


def test_the_credential_headers_survive_a_redirect_inside_one_origin():
    """A redirect to another path of the API is the ordinary case and keeps the request whole."""
    headers = {"X-Vendor-Key": SENTINEL, "Authorization": "Bearer x", "Accept": "audio/mpeg"}
    same = _redirected("https://api.elevenlabs.io/v2/x", headers)
    assert same.get_header("X-vendor-key") == SENTINEL and same.get_header("Authorization") == "Bearer x"
    explicit = _redirected("https://api.elevenlabs.io:443/v2/x", headers)
    assert explicit.get_header("X-vendor-key") == SENTINEL  # the default port is the same origin


def _echo(request: Request) -> Response:
    """A service that names the key it refused, which is the case the scrubber exists for."""
    pad = "." * int(request.args.get("pad", 0))
    return Response(_ECHO_PREFIX % pad + (request.headers.get("X-API-Key") or "") + '"}', 401)


def _busy(server: HTTPServer, times: int, status: int, **headers: str) -> str:
    """A service that is busy for the first `times` requests and answers after that, which is what a
    voice account at its concurrency limit does. The URL to post to is returned."""
    for _ in range(times):
        server.expect_oneshot_request("/busy").respond_with_data("", status, headers)
    server.expect_request("/busy").respond_with_json({"ok": True})
    return server.url_for("/busy")


def _sent(server: HTTPServer) -> list[tuple[str, dict[str, str]]]:
    """The path and lower-cased headers of every request the service saw, in order."""
    return [(r.path, {k.lower(): v for k, v in r.headers.items()}) for r, _ in server.log]


def test_a_real_redirect_to_another_host_never_carries_the_key(httpserver):
    """127.0.0.1 and localhost are the same machine and two different hosts, which is all a redirect needs."""
    port = httpserver.port
    for host in ("localhost", "127.0.0.1"):
        moved = Response(status=302, headers={"Location": f"http://{host}:{port}/landed"})
        httpserver.expect_request(f"/to/{host}").respond_with_response(moved)
    httpserver.expect_request("/landed").respond_with_json({"ok": True})
    vendor = Vendor(httpserver.url_for(""), KEY)
    assert vendor.speak("/to/localhost") == {"ok": True}
    (first_path, first), (landed_path, landed) = _sent(httpserver)
    assert first_path == "/to/localhost" and first["x-api-key"] == SENTINEL
    assert landed_path == "/landed" and "x-api-key" not in landed and landed["accept"] == "audio/mpeg"
    httpserver.log.clear()
    assert vendor.speak("/to/127.0.0.1") == {"ok": True}
    (_, first), (_, landed) = _sent(httpserver)
    assert first["x-api-key"] == SENTINEL and landed["x-api-key"] == SENTINEL  # the same host keeps it


def test_a_redirect_inside_the_origin_and_then_out_of_it_never_carries_the_key(httpserver):
    """The request that follows the first redirect still knows the key, so the second one drops it."""
    port = httpserver.port
    httpserver.expect_request("/hop").respond_with_response(
        Response(status=302, headers={"Location": f"http://localhost:{port}/out"})
    )
    httpserver.expect_request("/out").respond_with_response(
        Response(status=302, headers={"Location": f"http://127.0.0.1:{port}/landed"})
    )
    httpserver.expect_request("/landed").respond_with_json({"ok": True})
    assert Vendor(f"http://localhost:{port}", KEY).speak("/hop") == {"ok": True}
    sent = _sent(httpserver)
    assert [path for path, _ in sent] == ["/hop", "/out", "/landed"]
    assert sent[1][1]["x-api-key"] == SENTINEL and "x-api-key" not in sent[2][1]


def test_the_token_after_an_authorization_scheme_is_scrubbed_on_its_own():
    """A reply quotes the token far more often than it quotes the whole `Bearer x` header value."""
    token = "tok_0123456789abcdef"
    carried = _http.credentials({"Authorization": f"Bearer {token}"}, ())
    text = _http.scrub(f"refused {token}", carried)
    assert text == f"refused {_http.CREDENTIAL}" and token not in text
    # The whole header value is taken out too, for a reply that quotes the header as it was sent.
    whole = _http.scrub(f"sent Bearer {token}", carried)
    assert token not in whole
    # A header that is not a credential is left alone, so an ordinary value is not blanked out.
    plain = _http.credentials({"Accept": "audio/mpeg"}, ())
    assert _http.scrub("accept audio/mpeg", plain) == "accept audio/mpeg"


def test_a_reply_that_echoes_the_key_never_reaches_the_error(httpserver):
    """The body is written by whatever host a `base_url` names, so the quote is scrubbed before it is used."""
    httpserver.expect_request("/echo").respond_with_handler(_echo)
    vendor = Vendor(httpserver.url_for(""), KEY)
    with pytest.raises(ProviderError) as info:
        vendor.speak("/echo")
    message = str(info.value)
    assert SENTINEL not in message
    assert _http.CREDENTIAL in message and "401" in message

    # The whole body is scrubbed before it is cut, so a key that straddles the cut leaves no prefix.
    # The padding puts the key's first twelve characters just inside the cut and the rest beyond it.
    pad = _http.BODY_CHARS - len(_ECHO_PREFIX % "") - 12
    with pytest.raises(ProviderError) as info:
        vendor.speak(f"/echo?pad={pad}")
    straddled = str(info.value)
    assert SENTINEL[:12] not in straddled, straddled[-160:]
    assert _http.CREDENTIAL in straddled


def _echo_all(request: Request) -> Response:
    """A service that quotes the query string and the body it refused, wherever the key sat in them."""
    return Response(f"refused {request.query_string.decode()} {request.get_data(as_text=True)}", 401)


def test_a_key_sent_in_the_query_or_the_body_is_scrubbed_too(httpserver):
    """A secret is matched by its value, so a key that travels outside every header is still taken out."""
    httpserver.expect_request("/echo-all").respond_with_handler(_echo_all)
    with pytest.raises(ProviderError) as info:
        _http.post_json(
            httpserver.url_for(f"/echo-all?key={SENTINEL}"),
            {"api_key": SENTINEL},
            {},
            secrets=(KEY,),
            timeout=5,
            retries=0,
        )
    message = str(info.value)
    assert SENTINEL not in message and message.count(_http.CREDENTIAL) == 2


# ---- what a caller is told ------------------------------------------------------------------------


def test_a_url_in_a_message_carries_no_query_string(httpserver):
    """A query string says nothing a reader can act on, and the path is what names the call."""
    httpserver.expect_request("/echo").respond_with_handler(_echo)
    with pytest.raises(ProviderError) as caught:
        _http.post_json(
            httpserver.url_for("/echo?output_format=mp3_44100_128"), {}, {}, secrets=(), timeout=5, retries=0
        )
    assert "output_format" not in str(caught.value)
    assert _http.shown("https://x.test/a/b?c=d#e") == "https://x.test/a/b"


def test_a_host_that_refused_the_connection_is_asked_again(monkeypatch, waits):
    """Nothing connected, so nothing was sent and nothing could have been charged."""

    def refuses() -> NoReturn:
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    sent = counted_opener(monkeypatch, refuses)
    with pytest.raises(ProviderError) as caught:
        _http.post_json("http://127.0.0.1:1/never", {}, {}, secrets=(), timeout=1, retries=2)
    assert caught.value.retryable is True
    assert "could not reach http://127.0.0.1:1/never" in str(caught.value)
    assert len(sent) == 3 and len(waits) == 2


def test_a_reply_that_is_not_an_object_is_refused_rather_than_read(httpserver, waits):
    """The caller reads fields off what comes back, so a list or a number is refused where it arrives."""
    httpserver.expect_request("/list").respond_with_data("[1, 2, 3]")
    with pytest.raises(ProviderError, match="rather than an object") as caught:
        _http.post_json(httpserver.url_for("/list"), {}, {}, secrets=(), timeout=5, retries=2)
    assert caught.value.retryable is False and waits == []


def test_a_reply_that_is_not_json_is_never_asked_for_again_and_says_it_was_possibly_charged(httpserver, waits):
    """The reply arrived, so the service may have billed it, and asking again could buy it twice."""
    httpserver.expect_request("/html").respond_with_data("<html>not json</html>")
    with pytest.raises(ProviderError, match="not JSON") as caught:
        _http.post_json(httpserver.url_for("/html"), {}, {}, secrets=(), timeout=5, retries=2)
    assert caught.value.retryable is False
    assert "possibly charged" in str(caught.value)
    assert len(httpserver.log) == 1 and waits == []


def test_every_call_carries_the_timeout_it_was_given(httpserver, monkeypatch):
    """A provider that stopped answering would otherwise hold a build open for as long as the socket did."""
    httpserver.expect_request("/plain").respond_with_json({"ok": True})
    seen: list[float] = []
    real = _http.urlopen

    def timed(request: urllib.request.Request, *, timeout: float) -> HTTPResponse:
        seen.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr(_http, "urlopen", timed)
    _http.post_bytes(httpserver.url_for("/plain"), {}, {}, secrets=(), timeout=7, retries=0)
    assert seen == [7]


# ---- a busy service ---------------------------------------------------------------------------------


def test_a_busy_service_is_asked_again_until_it_answers(httpserver, waits):
    assert _http.post_json(_busy(httpserver, 2, 429), {}, {}, secrets=(), timeout=5, retries=3) == {"ok": True}
    assert len(httpserver.log) == 3
    assert waits == [_http.FIRST_WAIT_SECONDS, 2 * _http.FIRST_WAIT_SECONDS]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_failure_on_the_services_side_is_asked_again(httpserver, waits, status):
    assert _http.post_json(_busy(httpserver, 1, status), {}, {}, secrets=(), timeout=5, retries=1) == {"ok": True}
    assert len(waits) == 1


def test_the_last_busy_answer_is_raised_once_the_retries_are_spent(httpserver, waits):
    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        _http.post_json(_busy(httpserver, 9, 429), {}, {}, secrets=(), timeout=5, retries=2)
    assert caught.value.retryable is True
    assert len(httpserver.log) == 3 and len(waits) == 2


def test_a_request_the_service_refused_as_wrong_is_never_sent_again(httpserver, waits):
    """A refusal about the request itself would be refused again, and each sending may be billed."""
    with pytest.raises(ProviderError, match="HTTP 400"):
        _http.post_json(_busy(httpserver, 9, 400), {}, {}, secrets=(), timeout=5, retries=3)
    assert len(httpserver.log) == 1 and waits == []


def test_the_wait_a_service_names_is_the_wait_taken_and_held_under_the_longest(httpserver, waits):
    _http.post_json(_busy(httpserver, 1, 429, **{"Retry-After": "7"}), {}, {}, secrets=(), timeout=5, retries=1)
    assert waits == [7.0]
    assert _http.wait_before(0, "86400") == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(10, None) == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(1, "a date rather than seconds") == 2 * _http.FIRST_WAIT_SECONDS


# ---- a reply that stops arriving --------------------------------------------------------------------


STALL_TIMEOUT_SECONDS = 0.1
"""The read timeout a stall test gives, which the stalled reply outlasts because it waits on an event."""

WAIT = 10
"""The longest the test waits for the service to have the request, which only a broken service reaches."""


def test_a_reply_that_stalls_after_the_request_was_sent_is_sent_once_and_said_to_be_possibly_charged(service, waits):
    """A read timeout comes after the service had the whole request, which it may already have billed.

    The reply is held until the test ends, so the timeout fires however short it is, and the service
    counts the request it was handed rather than its log, which it writes after the handler returns.
    """
    handed: list[Request] = []
    reached = threading.Event()

    def stalls(request: Request) -> Response:
        handed.append(request)
        reached.set()
        return service.stalls(request)

    service.expect_request("/stall").respond_with_handler(stalls)
    with pytest.raises(ProviderError) as caught:
        _http.post_json(service.url_for("/stall"), {}, {}, secrets=(), timeout=STALL_TIMEOUT_SECONDS, retries=3)
    assert reached.wait(WAIT), "the service never had the request"
    assert len(handed) == 1 and waits == []
    assert "possibly charged" in str(caught.value)
    assert caught.value.retryable is False
    assert isinstance(caught.value.__cause__, TimeoutError), "it ends as a provider error rather than a bare timeout"


class Opened:
    """A reply whose body breaks part way, the way a socket that dies mid-reply breaks it."""

    status = 200

    def __init__(self, broken: BaseException) -> None:
        self.broken = broken

    def __enter__(self) -> Opened:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        raise self.broken


def counted_opener(monkeypatch: pytest.MonkeyPatch, answer: Callable[[], object]) -> list[str]:
    """Every request the provider would have been sent, with `answer` standing for the network."""
    sent: list[str] = []

    def opened(request: urllib.request.Request, **_options: float) -> object:
        sent.append(request.full_url)
        return answer()

    monkeypatch.setattr(_http, "urlopen", opened)
    return sent


@pytest.mark.parametrize(
    "broken",
    [
        ConnectionResetError(54, "Connection reset by peer"),
        http.client.IncompleteRead(b"{", 99),
        TimeoutError("The read operation timed out"),
    ],
    ids=["reset", "cut-short", "timed-out"],
)
def test_a_reply_that_broke_while_it_was_read_is_sent_once_and_said_to_be_possibly_charged(monkeypatch, waits, broken):
    sent = counted_opener(monkeypatch, lambda: Opened(broken))
    with pytest.raises(ProviderError) as caught:
        _http.post_bytes("https://api.test/v1/sound", {}, {}, secrets=(), timeout=5, retries=3)
    assert len(sent) == 1 and waits == []
    assert "possibly charged" in str(caught.value)
    assert caught.value.retryable is False
    assert caught.value.__cause__ is broken


@pytest.mark.parametrize(
    "broken",
    [http.client.RemoteDisconnected("Remote end closed connection without response"), TimeoutError("timed out")],
    ids=["hung-up", "timed-out"],
)
def test_a_reply_that_never_began_after_the_request_was_sent_is_sent_once(monkeypatch, waits, broken):
    """urllib raises these bare from waiting on the reply, once the whole request has gone out."""

    def breaks() -> NoReturn:
        raise broken

    sent = counted_opener(monkeypatch, breaks)
    with pytest.raises(ProviderError) as caught:
        _http.post_bytes("https://api.test/v1/sound", {}, {}, secrets=(), timeout=5, retries=3)
    assert len(sent) == 1 and waits == []
    assert "possibly charged" in str(caught.value)


@pytest.mark.parametrize(
    ("reason", "asked"),
    [
        (socket.gaierror(8, "nodename nor servname provided"), 3),
        (ConnectionRefusedError(61, "Connection refused"), 3),
        (TimeoutError("timed out"), 1),
        (ConnectionResetError(54, "Connection reset by peer"), 1),
    ],
    ids=["no-such-host", "refused", "timed-out-connected", "reset-connected"],
)
@pytest.mark.usefixtures("waits")
def test_only_a_request_that_never_connected_is_sent_again(monkeypatch, reason, asked):
    """A request that connected may have been sent whole before it broke, so only one that never connected is safe."""

    def fails() -> NoReturn:
        raise urllib.error.URLError(reason)

    sent = counted_opener(monkeypatch, fails)
    with pytest.raises(ProviderError) as caught:
        _http.post_bytes("https://api.test/v1/sound", {}, {}, secrets=(), timeout=5, retries=2)
    assert len(sent) == asked
    assert caught.value.retryable is (asked > 1)
    assert ("possibly charged" in str(caught.value)) is (asked == 1)


# ---- what a retry leaves behind -----------------------------------------------------------------------


@pytest.mark.usefixtures("waits")
def test_every_retry_says_how_long_it_waits_and_why_and_every_attempt_is_traced(httpserver, caplog):
    url = _busy(httpserver, 2, 429, **{"Retry-After": "3"})
    with caplog.at_level("DEBUG", logger="decktalk"):
        _http.post_json(url, {}, {"X-API-Key": SENTINEL}, secrets=(KEY,), timeout=5, retries=3)
    records = [record for record in caplog.records if record.name == "decktalk.speech.http"]
    retries = [data_of(record) for record in records if record.levelname == "WARNING"]
    attempts = [data_of(record) for record in records if record.levelname == "DEBUG"]
    assert [(said["wait_seconds"], said["wait_source"], said["attempt"]) for said in retries] == [
        (3.0, _http.STATED, 1),
        (3.0, _http.STATED, 2),
    ]
    assert [(said["status"], said["attempt"]) for said in attempts] == [(429, 1), (429, 2), (200, 3)]
    assert attempts[-1]["path"] == "/busy" and attempts[-1]["bytes"] > 0
    assert SENTINEL not in caplog.text


@pytest.mark.usefixtures("waits")
def test_a_wait_worked_out_by_doubling_says_so(httpserver, caplog):
    with caplog.at_level("WARNING", logger="decktalk"):
        _http.post_json(_busy(httpserver, 1, 503), {}, {}, secrets=(), timeout=5, retries=1)
    [record] = [record for record in caplog.records if record.name == "decktalk.speech.http"]
    assert data_of(record)["wait_source"] == _http.DOUBLED
