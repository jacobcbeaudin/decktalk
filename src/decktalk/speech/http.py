"""Minimal HTTP helpers on urllib, so a speech provider needs no HTTP dependency.

Every provider request goes through one opener whose redirect handler drops the credential headers
when a redirect leaves the origin the request was made to, so a key sent to the API host never
follows a 302 anywhere else. A reply is quoted back in an error, so `scrub` takes every credential
the request carried out of it first, because the body is written by the host and not by DeckTalk.
A credential belongs to an origin, which is the scheme, the host and the port together, so a
redirect that keeps the host and drops to http is a different origin and loses the headers with it.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import ProviderError

# A path segment that names a voice, such as the id in /text-to-speech/<id>/with-timestamps.
_VOICE_SEGMENT = re.compile(r"/(text-to-speech|voices)/([^/?#]+)")
REDACTED = "<voice id>"
CREDENTIAL = "<credential>"
# Every header that carries a credential. None of them follows a redirect to another origin.
AUTH_HEADERS = ("xi-api-key", "Authorization", "Proxy-Authorization", "Cookie")
DEFAULT_PORTS = {"https": 443, "http": 80}


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


def urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
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


def redact(url: str, detail: str = "") -> tuple[str, str]:
    """The URL without its query string or voice id, and the detail text without that voice id.

    Error messages reach terminals, logs, and CI output, so the voice id is replaced in both. A
    credential travels in a header, and `scrub` takes it out of a reply the caller quotes back.
    """
    shown = url.split("?")[0].split("#")[0]
    ids = {m.group(2) for m in _VOICE_SEGMENT.finditer(shown)}
    shown = _VOICE_SEGMENT.sub(lambda m: f"/{m.group(1)}/{REDACTED}", shown)
    for voice_id in ids:
        detail = detail.replace(voice_id, REDACTED)
    return shown, detail


BODY_CHARS = 500  # How much of a reply a message quotes, cut after the credentials are taken out.


def _http_error(url: str, exc: urllib.error.HTTPError, headers: Mapping[str, str]) -> ProviderError:
    """The failure as a message, with the reply body quoted and every credential taken out of it.

    The whole body is scrubbed before it is cut, because a credential that straddles the cut would
    otherwise survive as a prefix of itself.
    """
    with exc:  # The error holds the reply open, so it is closed once its body is read.
        body = exc.read().decode(errors="replace")
    shown, detail = redact(url, scrub(body, headers)[:BODY_CHARS])
    return ProviderError(f"HTTP {exc.code} from {shown}: {detail}")


def _unreachable(url: str, exc: urllib.error.URLError, headers: Mapping[str, str]) -> ProviderError:
    shown, reason = redact(url, scrub(str(exc.reason), headers))
    return ProviderError(f"could not reach {shown}: {reason}")


def post_bytes(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc, headers) from exc
    except urllib.error.URLError as exc:
        raise _unreachable(url, exc, headers) from exc


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    return json.loads(post_bytes(url, body, headers, timeout=timeout) or b"{}")


def get_json(url: str, headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc, headers) from exc
    except urllib.error.URLError as exc:
        raise _unreachable(url, exc, headers) from exc


def download(url: str, headers: dict[str, str], out: Path, *, timeout: int) -> None:
    req = urllib.request.Request(url, headers=headers)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(req, timeout=timeout) as resp, out.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc, headers) from exc
    except urllib.error.URLError as exc:
        _, reason = redact(url, scrub(str(exc), headers))
        raise ProviderError(f"download failed: {reason}") from exc
