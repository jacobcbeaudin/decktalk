"""Minimal HTTP helpers on urllib, so the package has no HTTP dependency.

Every request goes through one opener whose redirect handler drops the credential headers when a
redirect leaves the host the request was made to, so a key sent to the API host never follows a
302 anywhere else.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..errors import ProviderError

# A path segment that names a voice, such as the id in /text-to-speech/<id>/with-timestamps.
_VOICE_SEGMENT = re.compile(r"/(text-to-speech|voices)/([^/?#]+)")
REDACTED = "<voice id>"
# The headers that carry a credential. They never follow a redirect to another host.
AUTH_HEADERS = ("xi-api-key", "Authorization")


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


class DropAuthAcrossHosts(urllib.request.HTTPRedirectHandler):
    """Follows redirects as urllib does, minus the credential headers when the host changes.

    urllib reproduces custom headers on every redirect, so without this a 302 from the API host
    to any other host would hand that host the key.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _host(new.full_url) != _host(req.full_url):
            for name in AUTH_HEADERS:
                new.remove_header(name.capitalize())  # Request stores header names capitalized
        return new


_opener = urllib.request.build_opener(DropAuthAcrossHosts)


def urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
    """Open a request through the package's one opener, so the redirect rule applies to every call."""
    return _opener.open(request, timeout=timeout)


def redact(url: str, detail: str = "") -> tuple[str, str]:
    """The URL without its query string or voice id, and the detail text without that voice id.

    Error messages reach terminals, logs, and CI output, so the voice id is replaced in both.
    The API key travels in a header and never appears in either.
    """
    shown = url.split("?")[0].split("#")[0]
    ids = {m.group(2) for m in _VOICE_SEGMENT.finditer(shown)}
    shown = _VOICE_SEGMENT.sub(lambda m: f"/{m.group(1)}/{REDACTED}", shown)
    for voice_id in ids:
        detail = detail.replace(voice_id, REDACTED)
    return shown, detail


def _http_error(url: str, exc: urllib.error.HTTPError) -> ProviderError:
    shown, detail = redact(url, exc.read().decode(errors="replace")[:500])
    return ProviderError(f"HTTP {exc.code} from {shown}: {detail}")


def _unreachable(url: str, exc: urllib.error.URLError) -> ProviderError:
    shown, reason = redact(url, str(exc.reason))
    return ProviderError(f"could not reach {shown}: {reason}")


def post_bytes(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc) from exc
    except urllib.error.URLError as exc:
        raise _unreachable(url, exc) from exc


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    return json.loads(post_bytes(url, body, headers, timeout=timeout) or b"{}")


def get_json(url: str, headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc) from exc
    except urllib.error.URLError as exc:
        raise _unreachable(url, exc) from exc


def download(url: str, headers: dict[str, str], out: Path, *, timeout: int) -> None:
    req = urllib.request.Request(url, headers=headers)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(req, timeout=timeout) as resp, out.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except urllib.error.URLError as exc:
        _, reason = redact(url, str(exc))
        raise ProviderError(f"download failed: {reason}") from exc
