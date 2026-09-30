"""Where a credential may go: never across a redirect, never into an error, and never without a deadline."""

from __future__ import annotations

import urllib.request
from http.client import HTTPResponse

import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from decktalk.errors import ProviderError
from decktalk.speech import http as _http
from support.logs import data_of

SENTINEL = "sk_sentinel_key_that_must_never_print"

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


def _echo(request: Request) -> Response:
    """A service that names the key it refused, which is the case the scrubber exists for."""
    pad = "." * int(request.args.get("pad", 0))
    return Response(_ECHO_PREFIX % pad + (request.headers.get("xi-api-key") or "") + '"}', 401)


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
    headers = {"xi-api-key": SENTINEL, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    assert _http.post_json(httpserver.url_for("/to/localhost"), {"text": "hi"}, headers, timeout=5, retries=0) == {
        "ok": True
    }
    (first_path, first), (landed_path, landed) = _sent(httpserver)
    assert first_path == "/to/localhost" and first["xi-api-key"] == SENTINEL
    assert landed_path == "/landed" and "xi-api-key" not in landed and landed["accept"] == "audio/mpeg"
    httpserver.log.clear()
    assert _http.post_json(httpserver.url_for("/to/127.0.0.1"), {}, headers, timeout=5, retries=0) == {"ok": True}
    (_, first), (_, landed) = _sent(httpserver)
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


def test_a_reply_that_echoes_the_key_never_reaches_the_error(httpserver):
    """The body is written by whatever host `api_base` names, so the quote is scrubbed before it is used."""
    httpserver.expect_request("/echo").respond_with_handler(_echo)
    headers = {"xi-api-key": SENTINEL, "Authorization": f"Bearer {SENTINEL}", "Content-Type": "application/json"}
    with pytest.raises(ProviderError) as info:
        _http.post_json(httpserver.url_for("/echo"), {"text": "hi"}, headers, timeout=5, retries=0)
    message = str(info.value)
    assert SENTINEL not in message
    assert _http.CREDENTIAL in message and "401" in message

    # The whole body is scrubbed before it is cut, so a key that straddles the cut leaves no prefix.
    # The padding puts the key's first twelve characters just inside the cut and the rest beyond it.
    pad = _http.BODY_CHARS - len(_ECHO_PREFIX % "") - 12
    with pytest.raises(ProviderError) as info:
        _http.post_json(httpserver.url_for(f"/echo?pad={pad}"), {"text": "hi"}, headers, timeout=5, retries=0)
    straddled = str(info.value)
    assert SENTINEL[:12] not in straddled, straddled[-160:]
    assert _http.CREDENTIAL in straddled


# ---- what a caller is told ------------------------------------------------------------------------


def test_a_url_in_a_message_carries_no_query_string(httpserver):
    """A query string says nothing a reader can act on, and the path is what names the call."""
    httpserver.expect_request("/echo").respond_with_handler(_echo)
    with pytest.raises(ProviderError) as caught:
        _http.post_json(httpserver.url_for("/echo?output_format=mp3_44100_128"), {}, {}, timeout=5, retries=0)
    assert "output_format" not in str(caught.value)
    assert _http.shown("https://x.test/a/b?c=d#e") == "https://x.test/a/b"


def test_a_host_that_cannot_be_reached_is_worth_trying_again():
    """Nothing about the request was wrong, so a caller that waits and retries is right to."""
    with pytest.raises(ProviderError) as caught:
        _http.post_json("http://127.0.0.1:1/never", {}, {}, timeout=1, retries=0)
    assert caught.value.retryable is True
    assert "could not reach http://127.0.0.1:1/never" in str(caught.value)


def test_a_reply_that_is_not_an_object_is_refused_rather_than_read(httpserver, waits):
    """The caller reads fields off what comes back, so a list or a number is refused where it arrives."""
    httpserver.expect_request("/list").respond_with_data("[1, 2, 3]")
    with pytest.raises(ProviderError, match="rather than an object") as caught:
        _http.post_json(httpserver.url_for("/list"), {}, {}, timeout=5, retries=2)
    assert caught.value.retryable is False and waits == []


def test_a_reply_that_is_not_json_is_asked_for_again_as_its_flag_promises(httpserver, waits):
    """A gateway answering for the service is worth asking again, and the retry is made rather than promised."""
    httpserver.expect_request("/html").respond_with_data("<html>not json</html>")
    with pytest.raises(ProviderError, match="not JSON") as caught:
        _http.post_json(httpserver.url_for("/html"), {}, {}, timeout=5, retries=2)
    assert caught.value.retryable is True
    assert len(httpserver.log) == 3 and len(waits) == 2


def test_every_call_carries_the_timeout_it_was_given(httpserver, monkeypatch):
    """A provider that stopped answering would otherwise hold a build open for as long as the socket did."""
    httpserver.expect_request("/plain").respond_with_json({"ok": True})
    seen: list[float] = []
    real = _http.urlopen

    def timed(request: urllib.request.Request, *, timeout: float) -> HTTPResponse:
        seen.append(timeout)
        return real(request, timeout=timeout)

    monkeypatch.setattr(_http, "urlopen", timed)
    _http.post_bytes(httpserver.url_for("/plain"), {}, {}, timeout=7, retries=0)
    assert seen == [7]


# ---- a busy service ---------------------------------------------------------------------------------


def test_a_busy_service_is_asked_again_until_it_answers(httpserver, waits):
    assert _http.post_json(_busy(httpserver, 2, 429), {}, {}, timeout=5, retries=3) == {"ok": True}
    assert len(httpserver.log) == 3
    assert waits == [_http.FIRST_WAIT_SECONDS, 2 * _http.FIRST_WAIT_SECONDS]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_failure_on_the_services_side_is_asked_again(httpserver, waits, status):
    assert _http.post_json(_busy(httpserver, 1, status), {}, {}, timeout=5, retries=1) == {"ok": True}
    assert len(waits) == 1


def test_the_last_busy_answer_is_raised_once_the_retries_are_spent(httpserver, waits):
    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        _http.post_json(_busy(httpserver, 9, 429), {}, {}, timeout=5, retries=2)
    assert caught.value.retryable is True
    assert len(httpserver.log) == 3 and len(waits) == 2


def test_a_request_the_service_refused_as_wrong_is_never_sent_again(httpserver, waits):
    """A refusal about the request itself would be refused again, and each sending may be billed."""
    with pytest.raises(ProviderError, match="HTTP 400"):
        _http.post_json(_busy(httpserver, 9, 400), {}, {}, timeout=5, retries=3)
    assert len(httpserver.log) == 1 and waits == []


def test_the_wait_a_service_names_is_the_wait_taken_and_held_under_the_longest(httpserver, waits):
    _http.post_json(_busy(httpserver, 1, 429, **{"Retry-After": "7"}), {}, {}, timeout=5, retries=1)
    assert waits == [7.0]
    assert _http.wait_before(0, "86400") == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(10, None) == _http.LONGEST_WAIT_SECONDS
    assert _http.wait_before(1, "a date rather than seconds") == 2 * _http.FIRST_WAIT_SECONDS


# ---- a reply that stops arriving --------------------------------------------------------------------


def test_a_reply_that_stalls_is_a_provider_failure_asked_for_again(service, waits):
    """A read timeout escaped the retry loop as a bare TimeoutError, which the CLI reported as a bug."""
    service.expect_oneshot_request("/stall").respond_with_handler(service.stalls)
    service.expect_request("/stall").respond_with_json({"ok": True})
    assert _http.post_json(service.url_for("/stall"), {}, {}, timeout=1, retries=1) == {"ok": True}
    assert len(service.log) == 2 and len(waits) == 1


def test_a_reply_that_stalls_on_the_last_attempt_ends_as_a_provider_error_rather_than_a_timeout(service):
    """The retry before it is the test above, so one attempt is the whole of what this one needs."""
    service.expect_request("/stall").respond_with_handler(service.stalls)
    with pytest.raises(ProviderError, match="stopped answering") as caught:
        _http.post_json(service.url_for("/stall"), {}, {}, timeout=1, retries=0)
    assert caught.value.retryable is True
    assert isinstance(caught.value.__cause__, TimeoutError)


# ---- what a retry leaves behind -----------------------------------------------------------------------


@pytest.mark.usefixtures("waits")
def test_every_retry_says_how_long_it_waits_and_why_and_every_attempt_is_traced(httpserver, caplog):
    url = _busy(httpserver, 2, 429, **{"Retry-After": "3"})
    with caplog.at_level("DEBUG", logger="decktalk"):
        _http.post_json(url, {}, {"xi-api-key": SENTINEL}, timeout=5, retries=3)
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
        _http.post_json(_busy(httpserver, 1, 503), {}, {}, timeout=5, retries=1)
    [record] = [record for record in caplog.records if record.name == "decktalk.speech.http"]
    assert data_of(record)["wait_source"] == _http.DOUBLED
