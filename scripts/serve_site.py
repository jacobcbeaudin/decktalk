"""Serve site/ the way Cloudflare serves it, so a local review is a review of the real thing.

`python3 -m http.server` is close enough to be misleading. It differs in two ways that both look
like bugs in the page:

- It infers `application/x-sh` for install.sh, so clicking "read it first" downloads the file
  instead of showing it. That is the whole argument for piping a URL into a shell, lost to a
  guessed MIME type. Cloudflare reads site/_headers and sends text/plain.
- It has no extensionless routing, so /how and /films/halfway 404 and only /how.html works, which
  is the reverse of production: Cloudflare serves the extensionless path and redirects the .html
  one to it.

    uv run scripts/serve_site.py [--port 4111]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import pathlib
import socketserver

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
HEADERS_FILE = SITE / "_headers"


def read_headers(path: pathlib.Path) -> dict[str, list[tuple[str, str]]]:
    """Parse Cloudflare's _headers: a path on its own line, then indented `Name: value` lines."""
    rules: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not raw[:1].isspace():
            current = line
            rules[current] = []
        elif current and ":" in line:
            name, _, value = line.partition(":")
            rules[current].append((name.strip(), value.strip()))
    return rules


class Handler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler plus _headers and Cloudflare's extensionless routing."""

    rules: dict[str, list[tuple[str, str]]] = {}

    def send_head(self):  # noqa: D102 - overrides a documented base method
        path = self.path.split("?", 1)[0].split("#", 1)[0]

        # Production redirects /how.html to /how, so a link that costs a redirect there costs one
        # here too, rather than being invisible until it ships.
        if path.endswith(".html") and (SITE / path.lstrip("/")).is_file():
            target = path[: -len(".html")]
            self.send_response(307)
            self.send_header("Location", target)
            self.end_headers()
            return None

        # /how and /films/halfway are the paths the page actually links to.
        if not path.endswith("/") and not (SITE / path.lstrip("/")).exists():
            if (SITE / (path.lstrip("/") + ".html")).is_file():
                self.path = path + ".html"

        return super().send_head()

    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        for name, value in self.rules.get(path, []):
            # Content-Type is already on the wire from the base class, so replace rather than add.
            if name.lower() == "content-type":
                continue
            self.send_header(name, value)
        super().end_headers()

    def guess_type(self, path):  # noqa: D102 - overrides a documented base method
        rel = "/" + str(pathlib.Path(path).resolve().relative_to(SITE))
        for name, value in self.rules.get(rel, []):
            if name.lower() == "content-type":
                return value
        return super().guess_type(path)

    def log_message(self, fmt: str, *args: object) -> None:
        # One line per request, without the date noise, so a review session stays readable.
        print(f"  {fmt % args}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=4111)
    args = ap.parse_args()

    Handler.rules = read_headers(HEADERS_FILE) if HEADERS_FILE.is_file() else {}
    handler = functools.partial(Handler, directory=str(SITE))

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"site/ on http://localhost:{args.port}  ({len(Handler.rules)} rule(s) from _headers)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
