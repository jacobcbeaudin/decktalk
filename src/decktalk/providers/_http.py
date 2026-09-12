"""Minimal HTTP helpers on urllib, so the package has no HTTP dependency."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..errors import ProviderError


def post_bytes(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ProviderError(f"HTTP {exc.code} from {url.split('?')[0]}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"could not reach {url.split('?')[0]}: {exc.reason}") from exc


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    return json.loads(post_bytes(url, body, headers, timeout=timeout) or b"{}")


def get_json(url: str, headers: dict[str, str], *, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ProviderError(f"HTTP {exc.code} from {url.split('?')[0]}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"could not reach {url.split('?')[0]}: {exc.reason}") from exc


def download(url: str, headers: dict[str, str], out: Path, *, timeout: int) -> None:
    req = urllib.request.Request(url, headers=headers)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, out.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except urllib.error.URLError as exc:
        raise ProviderError(f"download failed: {exc}") from exc
