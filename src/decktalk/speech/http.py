"""Minimal HTTP on urllib, so a speech provider needs no HTTP dependency.

Every provider request goes through one opener whose redirect handler drops the credential headers
when a redirect leaves the origin the request was made to, so a key sent to the API host never
follows a 302 anywhere else. A credential belongs to an origin, which is the scheme, the host and
the port together, so a redirect that keeps the host and drops to http is a different origin and
loses the headers with it.

A reply is quoted back in an error, so `scrub` takes every credential the request carried out of it
first, because the body is written by the host and not by DeckTalk. The voice id is not scrubbed: it
is a published name that says which voice read the script, and a URL with it removed named nothing a
reader could act on.

Every call takes a timeout. A provider that stopped answering would otherwise hold a build open for
as long as the socket stayed up.

Every POST here buys something, a take or a sound, so a request the service may have billed is never
sent twice. Every call takes a number of retries. A service that answers that it is busy or failed,
which is a 408, a 429 or a 5xx, or that could not be connected to at all, is asked again after a wait
that doubles each time, or after the wait its own `Retry-After` names, up to that many more times. A
voice account limits how many requests run at once, so without this the first busy answer to one of
several concurrent sections failed the run after the others had already been paid for. A refusal
that says the request itself is wrong is never repeated, because it would be refused again. A reply
that broke once the request was connected, and a reply that arrived and could not be read, are never
repeated either, because the service may already have billed the request. Each becomes a `PROVIDER`
error that says the request was possibly charged, rather than escaping as a bare timeout.

Every attempt leaves a debug record of its path, status, size and time, and every retry leaves a
warning with the wait it takes and where that wait came from, so a run that took three minutes
because the account was throttled says so. No header is ever recorded, because the header map is
the one place the key sits in clear text.
"""

from __future__ import annotations

import http.client
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from http.client import HTTPResponse
from typing import Any

from pydantic_core import from_json, to_json

from ..errors import ProviderError

log = logging.getLogger(__name__)

CREDENTIAL = "<credential>"
# Every header that carries a credential. None of them follows a redirect to another origin.
AUTH_HEADERS = ("xi-api-key", "Authorization", "Proxy-Authorization", "Cookie")
DEFAULT_PORTS = {"https": 443, "http": 80}
"""Truth: the port a URL means when it names none, which is half of what an origin is."""

BODY_CHARS = 500
"""Truth: enough of a reply to say what was refused, cut once the credentials are out of it."""

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
"""Truth: the replies that say to try again, which are a slower pace or a failure on the service's side."""

FIRST_WAIT_SECONDS = 1.0
"""How long the first retry waits, which is doubled for each one after it."""

LONGEST_WAIT_SECONDS = 30.0
"""The longest any one retry waits, whatever the doubling or the service's `Retry-After` asks for."""

BROKEN_REPLIES = (TimeoutError, ConnectionError, http.client.HTTPException)
"""Truth: how a reply fails once the request was sent, which urllib raises bare rather than as a `URLError`.

A read that times out raises `TimeoutError`, a service that hangs up raises `ConnectionError`, and a
reply cut short raises `http.client.IncompleteRead`, which is an `HTTPException`. urllib wraps the
same errors in a `URLError` when they come while the request is still being sent, and part of a
request may already be with the service by then, so those count as broken too. A timeout while
connecting reads exactly like one while sending, so it counts as broken as well.
"""

UNCONNECTED = (socket.gaierror, ConnectionRefusedError)
"""Truth: how a request fails when nothing connected, a name that did not resolve or a port nobody serves.

Nothing was sent, so nothing could have been charged, and these are the only failures short of a busy
answer that are tried again.
"""

POSSIBLY_CHARGED = "The request was possibly charged, so it is not sent again."
"""What every failure that came after the service may have billed the request says about the bill."""

CHARGE_HINT = "Check the account's usage before you run the command again, which sends the request once more."
"""The advice beside a failure that was possibly charged, since running again buys the request again."""

STATED = "retry-after"
"""The source of a wait the service named in its own `Retry-After`."""

DOUBLED = "doubling"
"""The source of a wait worked out by doubling the first one, because the service named none."""


def origin(url: str) -> tuple[str, str, int]:
    """The scheme, the host and the port a URL names, which is what a credential belongs to."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    return scheme, (parts.hostname or "").lower(), parts.port or DEFAULT_PORTS.get(scheme, 0)


class DropAuthAcrossOrigins(urllib.request.HTTPRedirectHandler):
    """Follows redirects as urllib does, minus the credential headers when the origin changes.

    urllib reproduces custom headers on every redirect, so without this a 302 from the API host to
    anywhere else would hand that host the key. The comparison is on the whole origin, because a
    redirect to the same host over http would otherwise put the key on the wire in clear text.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and origin(new.full_url) != origin(req.full_url):
            for name in AUTH_HEADERS:
                new.remove_header(name.capitalize())  # Request stores header names capitalized.
        return new


_opener = urllib.request.build_opener(DropAuthAcrossOrigins)


def urlopen(request: urllib.request.Request, *, timeout: float) -> HTTPResponse:
    """Open a request through the package's one opener, so the redirect rule applies to every call."""
    return _opener.open(request, timeout=timeout)


def scrub(text: str, headers: Mapping[str, str]) -> str:
    """The text with every credential this request carried taken out of it.

    A reply body is written by whatever host `api_base` names, so a service that echoes the key back
    in a 401 would otherwise put it in an error message and on stderr, where a CI job keeps it. Both
    the whole header value and the token after an `Authorization` scheme are replaced, because a
    body may quote either.
    """
    names = {h.lower() for h in AUTH_HEADERS}
    for name, value in headers.items():
        if name.lower() not in names or not value:
            continue
        for secret in (value, value.partition(" ")[2]):
            if secret:
                text = text.replace(secret, CREDENTIAL)
    return text


def shown(url: str) -> str:
    """The URL an error names, without its query string, which carries no credential and no meaning."""
    return url.split("?")[0].split("#")[0]


def _http_error(url: str, exc: urllib.error.HTTPError, headers: Mapping[str, str]) -> ProviderError:
    """The failure as a message, with the reply body quoted and every credential taken out of it.

    The whole body is scrubbed before it is cut, because a credential that straddles the cut would
    otherwise survive as a prefix of itself.
    """
    with exc:  # The error holds the reply open, so it is closed once its body is read.
        body = exc.read().decode(errors="replace")
    detail = scrub(body, headers)[:BODY_CHARS]
    return ProviderError(f"HTTP {exc.code} from {shown(url)}: {detail}", retryable=exc.code in RETRYABLE_STATUS)


def _unreachable(url: str, exc: urllib.error.URLError, headers: Mapping[str, str]) -> ProviderError:
    """A request urllib could not send, worth trying again only when nothing connected at all."""
    if isinstance(exc.reason, BROKEN_REPLIES) and not isinstance(exc.reason, UNCONNECTED):
        return _broken(url, exc.reason, headers)
    return ProviderError(
        f"could not reach {shown(url)}: {scrub(str(exc.reason), headers)}",
        retryable=isinstance(exc.reason, UNCONNECTED),
    )


def _broken(url: str, exc: BaseException, headers: Mapping[str, str]) -> ProviderError:
    """A reply that broke once the request was connected, which the service may already have billed."""
    said = scrub(str(exc), headers) or "no reason given"
    return ProviderError(
        f"{shown(url)} stopped answering ({type(exc).__name__}: {said}). {POSSIBLY_CHARGED}", hint=CHARGE_HINT
    )


def pause(seconds: float) -> None:
    """Wait before asking again, which is the one place a retry sleeps and so the one a test replaces."""
    time.sleep(seconds)


def stated_wait(asked: str | None) -> float | None:
    """The wait a `Retry-After` names in seconds, or None when it names none or names a date."""
    return float(asked) if asked is not None and asked.strip().isdigit() else None


def wait_before(attempt: int, asked: str | None) -> float:
    """How long to wait before retry number `attempt`, counted from zero.

    A service that says how long to wait in `Retry-After` seconds is taken at its word, because it
    knows its own limit. Otherwise the wait doubles from the first one. Both are held under the
    longest wait, so a service cannot park a build for as long as it likes.
    """
    stated = stated_wait(asked)
    wanted = stated if stated is not None else FIRST_WAIT_SECONDS * 2**attempt
    return min(wanted, LONGEST_WAIT_SECONDS)


def _attempted(path: str, attempt: int, started: float, **measured: object) -> None:
    """Record one attempt at a request: its path, what came back, and how long it took."""
    seconds = round(time.monotonic() - started, 3)
    log.debug(
        "POST %s attempt %d took %.2f seconds.",
        path,
        attempt + 1,
        seconds,
        extra={"data": {"path": path, "attempt": attempt + 1, "seconds": seconds, **measured}},
    )


def post[T](
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout: float,
    retries: int,
    parse: Callable[[bytes], T],
) -> T:
    """One POST, with its reply read by `parse`, and any failure as a `PROVIDER` error that quotes no key.

    A failure that says the service was busy or could not be connected to is tried again up to
    `retries` more times, and the last failure is the one raised. A reply that arrived may have been
    billed, so whatever `parse` refuses in it is raised at once and never asked for again.
    """
    data = to_json(body)
    path = urllib.parse.urlsplit(url).path
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        started = time.monotonic()
        asked: str | None = None
        try:
            with urlopen(req, timeout=timeout) as resp:
                status, reply = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            _attempted(path, attempt, started, status=exc.code)
            failure, asked, cause = _http_error(url, exc, headers), exc.headers.get("Retry-After"), exc
        except urllib.error.URLError as exc:
            _attempted(path, attempt, started, reason=type(exc.reason).__name__)
            failure, cause = _unreachable(url, exc, headers), exc
        except BROKEN_REPLIES as exc:
            _attempted(path, attempt, started, reason=type(exc).__name__)
            failure, cause = _broken(url, exc, headers), exc
        else:
            _attempted(path, attempt, started, status=status, bytes=len(reply))
            return parse(reply)
        if not failure.retryable or attempt >= retries:
            raise failure from cause
        wait = wait_before(attempt, asked)
        log.warning(
            "%s Asking again in %.1f seconds, retry %d of %d.",
            failure,
            wait,
            attempt + 1,
            retries,
            extra={
                "data": {
                    "path": path,
                    "wait_seconds": wait,
                    "wait_source": STATED if stated_wait(asked) is not None else DOUBLED,
                    "attempt": attempt + 1,
                    "retries": retries,
                }
            },
        )
        pause(wait)
        attempt += 1


def post_bytes(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: float, retries: int) -> bytes:
    """One POST whose reply is the bytes it carries, which is what the two sound calls make."""
    return post(url, body, headers, timeout=timeout, retries=retries, parse=bytes)


def post_json(
    url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: float, retries: int
) -> dict[str, Any]:
    """One POST whose reply is a JSON object, which is every call a provider makes but the two sound ones."""

    def parse(reply: bytes) -> dict[str, Any]:
        try:
            answered = from_json(reply or b"{}")
        except ValueError as exc:
            # A gateway may have answered for a service that never had the request, and the service may
            # equally have billed a reply that arrived mangled, so it is reported and never sent again.
            raise ProviderError(
                f"{shown(url)} answered with something that is not JSON. {POSSIBLY_CHARGED}", hint=CHARGE_HINT
            ) from exc
        if not isinstance(answered, dict):
            raise ProviderError(f"{shown(url)} answered with a {type(answered).__name__} rather than an object.")
        return answered

    return post(url, body, headers, timeout=timeout, retries=retries, parse=parse)
