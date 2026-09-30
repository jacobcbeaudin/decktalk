"""The local origin every page is opened at, its request routing, and the server `decktalk serve` runs.

A page opened from `file://` cannot `fetch()` a file beside it and cannot load an ES module, so a
deck that reads its own JSON or imports a library fails in the recorder while it works in a browser
tab. Every page DeckTalk drives is therefore opened at `http://project.localhost/`, and Playwright's
request routing answers every request under that origin from the project directory. No socket opens
and no port is chosen, `*.localhost` is a secure context in Chromium, and a relative URL in a page
resolves the way the author wrote it.

One rule decides what may leave the project directory, and `Allowed` is that rule, which both halves
of this module apply. A request is answered only when the names it asks for are the ones a project
declared or sit under a directory it declared, and only when the file those names open is inside the
project and carries no name that begins with a dot. Everything else in a project is refused, which
is the change this release makes: the script, the cues, the build directory and the `.env` holding
the speech key are beside the deck and are not part of it, and a page that could fetch them could
put them on screen or send them to whoever it liked.

The router also keeps the project-relative path of every file it served, which is what lets `record`
key a section on the assets its page actually loaded rather than on the page file alone.

A request for another origin is where the two page policies part. A trusted page is the author's own
work, so its request goes to the network the way it would in the author's browser and the recording
names the host it reached. An untrusted page is a stranger's, so its request is refused before it
leaves the browser, whatever its scheme, and the recording names the host it reached for all the same.

`decktalk serve` is the other half: an author previewing a page in their own browser needs a real
server, so this module also runs one from the standard library, bound to 127.0.0.1 by default.
"""

from __future__ import annotations

import errno
import io
import logging
import mimetypes
import os
import re
import socket
from collections.abc import Iterable, Mapping
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

QUERY = re.compile(r"\?[^\s\"]*")
"""The query of a URL inside a request line, up to the space or quote that ends it."""
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


def project_path(rel: str) -> str | None:
    """A request path folded to the names it opens under the project, or None when it climbs out.

    The folding is over the names alone, because whether two spellings are one file is what a
    filesystem answers and what this rule may never ask it. `.` says to stay and `..` says to go
    back, so a path that goes back further than the project is not a path under it at all.
    """
    names: list[str] = []
    for name in path_names(rel):
        if name == ".":
            continue
        if name == "..":
            if not names:
                return None
            names.pop()
            continue
        names.append(name)
    return "/".join(names)


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
    served: tuple[str, ...]  # each declared directory or file, as the project spells it under the root

    @classmethod
    def of(cls, root: Path, declared: Iterable[Path | str]) -> Allowed:
        """The rule for one project: its root, and the name of each directory or file it declares.

        A declaration is kept as the name it was written with rather than as the file that name
        opens, because a request is compared against the declaration and a filesystem that folds two
        spellings into one file would otherwise widen the rule to a spelling nobody wrote down.

        A declared path outside the project is dropped rather than refused at request time, because a
        rule that names a place the project does not own is a mistake in the project file and this
        module answers requests rather than reporting on `decktalk.toml`. A declaration of the root
        itself is dropped for the same reason, because the root holds the script, the cue file, the
        build directory and the credential, which no page may reach.
        """
        base = root.resolve()
        inside: list[str] = []
        for path in declared:
            spelled = Path(path)
            candidate = (base / spelled).resolve()
            if not candidate.is_relative_to(base):
                continue
            # A declaration made as an absolute path is named from the root, which is the only way
            # to say where it sits under the project. A relative one already says it.
            named = candidate.relative_to(base).as_posix() if spelled.is_absolute() else spelled.as_posix()
            place = project_path(named)
            if place:
                inside.append(place)
        return cls(root=base, served=tuple(dict.fromkeys(inside)))

    def declares(self, asked: str) -> bool:
        """Whether a request names a declared file, or a name under a declared directory.

        The comparison is over the names rather than over the files they open, because a declaration
        names a spelling and only the filesystem knows whether two spellings open one file. A
        declared directory is a place, so every name under it is declared with it. An empty place
        declares nothing, because the name it would stand for is the project root.
        """
        return any(place and (asked == place or asked.startswith(f"{place}/")) for place in self.served)

    def target(self, rel: str) -> Target:
        """The file a project-relative request path is answered from, or the reason it is refused.

        The declaration is answered over the names the request asks for, so a spelling nobody
        declared is refused on a folding filesystem exactly as it is on a case-sensitive one. The
        other three refusals are about the file that is opened rather than about the name that was
        asked for, so the path is resolved and a directory's `index.html` is appended before they run.
        """
        asked = project_path(rel)
        if asked is None:
            return Target(refused=OUTSIDE)
        try:
            named = (self.root / asked).resolve() if asked else self.root
            directory = named.is_dir()
            opened = (named / INDEX).resolve() if directory else named
            contained = opened.is_relative_to(self.root)
        except (OSError, ValueError):
            # silent: a path the system cannot resolve is refused as unusable.
            return Target(refused=UNUSABLE)
        if not contained:
            return Target(refused=OUTSIDE)
        if hidden_name(path_names(rel)) or hidden_name(list(opened.relative_to(self.root).parts)):
            return Target(refused=HIDDEN)
        # A directory is served as its index, so the name the declaration is asked about is the name
        # the request really opens.
        if not self.declares(f"{asked}/{INDEX}".lstrip("/") if directory else asked):
            return Target(refused=UNDECLARED)
        return Target(path=opened)


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
        """Note a request this origin would not answer, as what it asked for and the reason.

        A request under the origin is named by its project path. One for another origin is named by
        its host and path, and never by its query, which is where a page would put what it meant to send.
        """
        parts = urlsplit(url)
        path = unquote(parts.path).lstrip("/")
        mine = f"{parts.scheme}://{parts.netloc}" == ORIGIN
        asked = f"{path if mine else f'{parts.scheme}://{parts.netloc}/{path}'} ({why})"
        if asked not in self.refused:
            self.refused.append(asked)


OFF_ORIGIN = "a page that is not trusted may reach no origin but the project's own"
"""Why a request for another origin is refused under the untrusted policy, which the recording keeps."""


def route_pages(
    target: Page | BrowserContext,
    allowed: Allowed,
    documents: Mapping[str, bytes] | None = None,
    *,
    trusted: bool,
) -> Assets:
    """Answer every request under the origin from what `allowed` names, and return what was served.

    `target` is a Playwright page or browser context. A request to any other origin is noted either
    way. A trusted page's request then goes on, so a page that reaches for a CDN still does what it
    would do in a browser and `record` can report it. An untrusted page's request is aborted, so
    nothing it asks for off the origin ever reaches the network stack. Every route is answered,
    because a route left unanswered hangs the page that made it.

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
                if trusted:
                    route.continue_()
                    return
                assets.turned_away(request.url, OFF_ORIGIN)
                route.abort("blockedbyclient")
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
            # The query is left out, because it is where a page puts what it means to send somewhere.
            shown = request.url.split("?", 1)[0]
            log.warning("The origin could not answer %s.", shown, exc_info=exc, extra={"data": {"url": shown}})
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
        self,
        allowed: Allowed,
        documents: Mapping[str, bytes],
        request: socket.socket,
        client_address: tuple[str, int],
        server: HTTPServer,
    ) -> None:
        self.allowed = allowed
        self.documents = documents
        super().__init__(request, client_address, server, directory=str(allowed.root))

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Record one request line with its query left out, because the query is what a page means to send."""
        log.debug("[serve] %s", QUERY.sub("", format % args))

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
        asked = unquote(urlsplit(self.path).path)
        body = self.documents.get(asked)
        if body is not None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", EXTRA_TYPES[".json"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        wanted = self.allowed.target(asked.lstrip("/"))
        if wanted.path is None:
            self.send_error(HTTPStatus.FORBIDDEN, wanted.refused or OUTSIDE)
            return None
        return super().send_head()


def open_server(
    allowed: Allowed, host: str, port: int, documents: Mapping[str, bytes] | None = None
) -> ThreadingHTTPServer:
    """A stopped-in-a-context HTTP server for the project directory, bound to `host` and `port`.

    The address family comes from the host, so the safest address an author can ask for, the IPv6
    loopback, binds as readily as the IPv4 one.

    `documents` are the paths the caller answers itself, which are the same ones the router answers
    for a recorded page. An author previewing a deck reads its cue times from the origin exactly as
    the recorder does, so a page that works in the preview is the page that is recorded.

    The server outlives the run that opened it, so what it records, a request line or a request that
    raised, reaches a host's own logging and no run's events file, and nothing it records is printed.
    """
    handler = partial(_Handler, allowed, dict(documents or {}))
    try:
        family = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM)[0][0]
    except OSError as exc:
        why = exc.strerror or exc
        raise ToolError(f"could not resolve the address {host!r} ({why}). Pass another --host.") from exc

    class Server(ThreadingHTTPServer):
        address_family = family

        def handle_error(self, request: object, client_address: tuple[str, int]) -> None:  # noqa: ARG002  (the base's signature)
            """Record a request that raised, where the base class prints its traceback to stderr."""
            log.debug("The preview server could not answer %s.", client_address[0], exc_info=True)

    try:
        return Server((host, port), handler)
    except OSError as exc:
        flag = "--port" if exc.errno == errno.EADDRINUSE else "--host"
        raise ToolError(f"could not serve {host}:{port} ({exc.strerror or exc}). Pass another {flag}.") from exc


def served_url(server: ThreadingHTTPServer) -> str:
    """Where a running server answers, named by the address its socket is bound to.

    An author reads the URL to know who can reach the page, so it names the real bind rather than
    loopback, and an IPv6 address is bracketed as a URL requires.
    """
    host, port = str(server.server_address[0]) or "127.0.0.1", server.server_address[1]
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
