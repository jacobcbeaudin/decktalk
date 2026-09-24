"""The local origin every page is opened at, its request routing, and the server `decktalk serve` runs.

A page opened from `file://` cannot `fetch()` a file beside it and cannot load an ES module, so a
deck that reads its own JSON or imports a library fails in the recorder while it works in a browser
tab. Every page DeckTalk drives is therefore opened at `http://project.localhost/`, and Playwright's
request routing answers every request under that origin from the project directory. No socket opens
and no port is chosen, `*.localhost` is a secure context in Chromium, and a relative URL in a page
resolves the way the author wrote it.

One rule decides what may leave the project directory, and `Allowed` is that rule, which both halves
of this module apply. A request is answered only from a file under a directory the project declared,
resolved, containing no name that begins with a dot. Everything else in a project is refused, which
is the change this release makes: the script, the cues, the build directory and the `.env` holding
the speech key are beside the deck and are not part of it, and a page that could fetch them could
put them on screen or send them to whoever it liked.

The router also keeps the project-relative path of every file it served, which is what lets `record`
key a section on the assets its page actually loaded rather than on the page file alone.

`decktalk serve` is the other half: an author previewing a page in their own browser needs a real
server, so this module also runs one from the standard library, bound to 127.0.0.1 by default.
"""

from __future__ import annotations

import errno
import io
import ipaddress
import logging
import mimetypes
import os
import socket
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, unquote, urlencode, urlsplit

from playwright.sync_api import BrowserContext, Page, Request, Route

from ..errors import ToolError
from ..page import Q

log = logging.getLogger(__name__)

ORIGIN = "http://project.localhost"
INDEX = "index.html"
# Why a request under the origin is refused. Each is printed to the page that asked, so each reads
# as a sentence about the request rather than about the file it wanted.
OUTSIDE = "that path is outside the project directory"
HIDDEN = "a name beginning with a dot is never served"
UNUSABLE = "that path is not a usable file name"
UNDECLARED = "that path is not in the deck directory and the project declares no such asset"
TEXT = "text/plain; charset=utf-8"
"""What a refusal is answered as, because a page that asked for a file is given a sentence instead."""
# A type the standard table gets wrong or does not know, and which a deck loads often enough to matter.
EXTRA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".wasm": "application/wasm",
}


def content_type(path: Path) -> str:
    """The media type a served file is answered with, which decides whether Chromium will run it."""
    known = EXTRA_TYPES.get(path.suffix.lower())
    if known:
        return known
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def page_url(page: str | Path, query: Mapping[Q, str] | None = None) -> str:
    """The origin URL of a project-relative page, with its query string.

    The path is the page as `decktalk.toml` spells it, so `deck/index.html` is served at
    `http://project.localhost/deck/index.html` and every relative URL inside it still resolves. The
    keys are the contract's own query vocabulary, so a key a page does not read cannot be written
    here, which is what the six hand-spelled query strings became.
    """
    rel = Path(page).as_posix().lstrip("/")
    url = f"{ORIGIN}/{quote(rel)}"
    if not query:
        return url
    return f"{url}?{urlencode({key.value: value for key, value in query.items()})}"


def path_names(rel: str) -> list[str]:
    """Every name in a request path, with the empty ones dropped."""
    return [part for part in rel.split("/") if part]


def hidden_name(parts: list[str]) -> bool:
    """Whether any name in a path begins with a dot, which is the one name this origin never serves.

    `.` and `..` are not such names. They say where to look rather than what to open, and the
    containment test below is what answers them.
    """
    return any(part.startswith(".") and part not in (".", "..") for part in parts)


@dataclass(frozen=True)
class Target:
    """What one URL names: the file to answer with, the reason to refuse it, or neither.

    Neither means the URL is not under this origin, so whatever DeckTalk is driving may go to the
    network for it, which is what lets a deck that loads a library behave as it does in a browser.
    """

    path: Path | None = None
    refused: str = ""

    @property
    def mine(self) -> bool:
        """Whether this origin owns the request, which is true of a refusal as much as of a file."""
        return self.path is not None or bool(self.refused)


@dataclass(frozen=True)
class Allowed:
    """What one project's origin may answer with, which is the deck directory and its declared assets.

    A project is a directory of things a person wrote, and only some of them are the page. Serving
    the rest gave a deck, and anyone who could reach an author's preview server, the script, the
    cues, everything under `build/` and every key in `.env`.
    """

    root: Path  # the project directory, which every served path is named relative to
    served: tuple[Path, ...]  # each declared directory or file, resolved, under the root

    @classmethod
    def of(cls, root: Path, declared: Iterable[Path | str]) -> Allowed:
        """The rule for one project: its root, and each directory or file it declares, resolved once.

        A declared path outside the project is dropped rather than refused at request time, because a
        rule that names a place the project does not own is a mistake in the project file and this
        module answers requests rather than reporting on `decktalk.toml`.
        """
        base = root.resolve()
        inside: list[Path] = []
        for path in declared:
            candidate = (base / path).resolve()
            if candidate == base or candidate.is_relative_to(base):
                inside.append(candidate)
        return cls(root=base, served=tuple(dict.fromkeys(inside)))

    def declares(self, target: Path) -> bool:
        """Whether a resolved file is the one a declaration names, or sits under a declared directory."""
        return any(target == place or target.is_relative_to(place) for place in self.served)

    def target(self, rel: str) -> Target:
        """The file a project-relative request path is answered from, or the reason it is refused.

        The tests are applied to the file that is opened rather than to the name that was asked for,
        so the path is resolved and a directory's `index.html` is appended before they run.
        """
        try:
            target = (self.root / rel).resolve() if rel else self.root
            target = (target / INDEX).resolve() if target.is_dir() else target
            contained = target.is_relative_to(self.root)
        except (OSError, ValueError):
            return Target(refused=UNUSABLE)
        if not contained:
            return Target(refused=OUTSIDE)
        if hidden_name(path_names(rel)) or hidden_name(list(target.relative_to(self.root).parts)):
            return Target(refused=HIDDEN)
        if not self.declares(target):
            return Target(refused=UNDECLARED)
        return Target(path=target)


def local_target(allowed: Allowed, url: str) -> Target:
    """The file an origin URL names, the reason it is refused, or neither when it names another host."""
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != ORIGIN:
        return Target()
    return allowed.target(unquote(parts.path).lstrip("/"))


@dataclass
class Assets:
    """The project-relative path of every file the router served, in the order it was first asked for."""

    root: Path
    paths: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # Every other origin the page reached for, as scheme and host, in the order first asked for. A
    # recording that depends on one depends on a host the project does not own.
    external: list[str] = field(default_factory=list)
    # Every path the origin refused, with the reason, so a page that reached for the script or for
    # `.env` is a fact the recording carries rather than a 403 only the page ever saw.
    refused: list[str] = field(default_factory=list)

    def reached(self, url: str) -> None:
        """Note an origin that is not this project's, which `record` reports as a CDN asset."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin != ORIGIN and origin not in self.external:
            self.external.append(origin)

    def record(self, path: Path, *, found: bool) -> None:
        rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        where = self.paths if found else self.missing
        if rel not in where:
            where.append(rel)

    def turned_away(self, url: str, why: str) -> None:
        """Note a request this origin would not answer, as the path it asked for and the reason."""
        asked = f"{unquote(urlsplit(url).path).lstrip('/')} ({why})"
        if asked not in self.refused:
            self.refused.append(asked)


def route_pages(
    target: Page | BrowserContext, allowed: Allowed, documents: Mapping[str, bytes] | None = None
) -> Assets:
    """Answer every request under the origin from what `allowed` names, and return what was served.

    `target` is a Playwright page or browser context. A request to any other origin is left alone, so
    a page that reaches for a CDN still does what it would do in a browser and `record` can report it.
    Every route is answered, because a route left unanswered hangs the page that made it.

    `documents` are the paths a caller answers itself, such as the cue times a run resolved, which no
    file on disk holds. They are answered from memory as JSON and never recorded as assets, because a
    recording keyed on them would be keyed on its own output.
    """
    assets = Assets(root=allowed.root)
    answered = dict(documents or {})

    def handler(route: Route, request: Request) -> None:
        try:
            asked = unquote(urlsplit(request.url).path)
            if asked in answered and urlsplit(request.url).netloc == urlsplit(ORIGIN).netloc:
                route.fulfill(status=HTTPStatus.OK, content_type=EXTRA_TYPES[".json"], body=answered[asked])
                return
            wanted = local_target(allowed, request.url)
            if not wanted.mine:
                assets.reached(request.url)
                route.continue_()
                return
            if wanted.refused or wanted.path is None:
                assets.turned_away(request.url, wanted.refused or OUTSIDE)
                route.fulfill(status=HTTPStatus.FORBIDDEN, content_type=TEXT, body=wanted.refused)
                return
            if not wanted.path.is_file():
                assets.record(wanted.path, found=False)
                body = f"no such file: {wanted.path.name}"
                route.fulfill(status=HTTPStatus.NOT_FOUND, content_type=TEXT, body=body)
                return
            assets.record(wanted.path, found=True)
            route.fulfill(status=HTTPStatus.OK, content_type=content_type(wanted.path), body=wanted.path.read_bytes())
        except Exception as exc:  # noqa: BLE001  (the page must learn its request failed rather than wait for it)
            log.warning("could not answer %s (%s)", request.url, exc)
            broke = HTTPStatus.INTERNAL_SERVER_ERROR
            route.fulfill(status=broke, content_type=TEXT, body="the origin could not answer")

    target.route("**/*", handler)
    return assets


# ---- the author's own server ---------------------------------------------------------------


class _Handler(SimpleHTTPRequestHandler):
    """A quiet file server for the project, under the same rule the router applies, and never cached.

    The rule is bound to the handler rather than read from anywhere, because the base class builds
    one handler per request and a handler with no rule would answer from the whole directory.
    """

    # The base class opens a directory's index itself, so it may open only the name the rule
    # approved. Its own default also tries `index.htm`, which would answer from a file the one rule
    # never saw.
    index_pages = (INDEX,)

    def __init__(
        self, allowed: Allowed, request: socket.socket, client_address: tuple[str, int], server: HTTPServer
    ) -> None:
        self.allowed = allowed
        super().__init__(request, client_address, server, directory=str(allowed.root))

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        log.debug("[serve] " + format, *args)

    def guess_type(self, path: str | os.PathLike[str]) -> str:
        return content_type(Path(path))

    def list_directory(self, path: str | os.PathLike[str]) -> io.BytesIO | None:  # noqa: ARG002  (nothing to list)
        """A directory with no index is not a listing, because a listing names every file there is."""
        self.send_error(HTTPStatus.NOT_FOUND, "no such file")
        return None

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        """Answer only what the one rule allows, so both halves of this module refuse the same file.

        The base class opens the file a second time below, so a name swapped for a link between the
        two lookups is served, which only someone who can already write into the project can do.
        """
        wanted = self.allowed.target(unquote(urlsplit(self.path).path).lstrip("/"))
        if wanted.path is None:
            self.send_error(HTTPStatus.FORBIDDEN, wanted.refused or OUTSIDE)
            return None
        return super().send_head()


def open_server(allowed: Allowed, host: str, port: int) -> ThreadingHTTPServer:
    """A stopped-in-a-context HTTP server for the project directory, bound to `host` and `port`.

    The address family comes from the host, so the safest address an author can ask for, the IPv6
    loopback, binds as readily as the IPv4 one.
    """
    handler = partial(_Handler, allowed)
    try:
        family = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM)[0][0]
    except OSError as exc:
        why = exc.strerror or exc
        raise ToolError(f"could not resolve the address {host!r} ({why}). Pass another --host.") from exc

    class Server(ThreadingHTTPServer):
        address_family = family

    try:
        return Server((host, port), handler)
    except OSError as exc:
        flag = "--port" if exc.errno == errno.EADDRINUSE else "--host"
        raise ToolError(f"could not serve {host}:{port} ({exc.strerror or exc}). Pass another {flag}.") from exc


def bound_host(server: ThreadingHTTPServer) -> str:
    """The address the socket is actually bound to, which is what the author needs to be told."""
    return str(server.server_address[0])


def reachable_warning(server: ThreadingHTTPServer) -> str:
    """One line for a bind that is not loopback, or an empty string when only this machine can reach it."""
    host = bound_host(server)
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host in ("localhost", "")
    if loopback:
        return ""
    return (
        f"serving on {host}, so every machine on this network can read the project directory. "
        "Pass --host 127.0.0.1 to keep it on this machine."
    )


def served_urls(server: ThreadingHTTPServer, pages: Iterator[str] | list[str]) -> list[str]:
    """The URL of each project page on a running server, which `serve` prints for an author to open.

    The host is the one the socket is bound to, so a server reachable from the network says so in
    every URL it prints.
    """
    host, port = bound_host(server), server.server_address[1]
    shown = host or "127.0.0.1"
    base = f"http://[{shown}]:{port}" if ":" in shown else f"http://{shown}:{port}"
    return [f"{base}/{quote(Path(page).as_posix())}" for page in pages]
