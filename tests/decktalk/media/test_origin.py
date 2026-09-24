"""The local origin: the URL a page is opened at, what the router will serve, and the author's server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from decktalk.errors import ToolError
from decktalk.media.browser import chromium, open_page
from decktalk.media.origin import (
    HIDDEN,
    ORIGIN,
    OUTSIDE,
    UNDECLARED,
    Allowed,
    Assets,
    content_type,
    local_target,
    open_server,
    page_url,
    reachable_warning,
    route_pages,
    served_urls,
)
from decktalk.page import Q


def whole(root: Path) -> Allowed:
    """The rule of a project that declared its whole directory, for the tests about the other rules."""
    return Allowed.of(root, ["."])


FETCH_PAGE = """<!doctype html><meta charset="utf-8"><title>fetch</title>
<script>
  window.loaded = fetch("data/facts.json").then((r) => r.json()).then((d) => { window.answer = d.answer; });
</script>
"""


def test_a_page_url_keeps_its_relative_path_and_its_query():
    assert page_url("deck/index.html") == f"{ORIGIN}/deck/index.html"
    assert page_url(Path("deck/index.html"), {Q.SCENE: "1", Q.CUES: "a@1.5"}) == (
        f"{ORIGIN}/deck/index.html?scene=1&cues=a%401.5"
    )


def test_a_url_outside_the_origin_is_left_to_the_network(tmp_path):
    for url in ("https://cdn.example.com/katex.js", "file:///etc/passwd"):
        assert local_target(whole(tmp_path), url).mine is False


def test_a_request_may_not_climb_out_of_the_project(tmp_path):
    (tmp_path / "deck").mkdir()
    assert local_target(whole(tmp_path), f"{ORIGIN}/../../etc/passwd").refused == OUTSIDE
    assert local_target(whole(tmp_path), f"{ORIGIN}/deck/../deck/index.html").path == tmp_path / "deck" / "index.html"


def test_a_link_inside_the_project_to_a_dot_name_is_refused(tmp_path):
    """The rule is about the file the origin opens, and a link is one more name for that file."""
    (tmp_path / ".env").write_text("DECKTALK_TEST_KEY=not-a-key", encoding="utf-8")
    (tmp_path / "envlink").symlink_to(tmp_path / ".env")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "key.txt").write_text("no", encoding="utf-8")
    (tmp_path / "pub").symlink_to(tmp_path / ".hidden", target_is_directory=True)
    assert local_target(whole(tmp_path), f"{ORIGIN}/envlink").refused == HIDDEN
    assert local_target(whole(tmp_path), f"{ORIGIN}/pub/key.txt").refused == HIDDEN


def test_a_symlink_that_leaves_the_project_is_refused(tmp_path):
    """A deck may be one an agent wrote, so a link out of the project is a door and not a shortcut."""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("no", encoding="utf-8")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    assert local_target(whole(tmp_path), f"{ORIGIN}/escape/secret.txt").refused == OUTSIDE


@pytest.mark.parametrize("path", ["/.env", "/deck/../.env", "/.git/config", "/sub/.ssh/id_rsa", "/%2Eenv"])
def test_a_dot_leading_name_is_never_served(tmp_path, path):
    """`.env` holds the speech key, so no page and no visitor of `serve` may ask for it."""
    assert local_target(whole(tmp_path), f"{ORIGIN}{path}").refused == HIDDEN


def test_a_path_that_is_not_a_usable_file_name_is_refused_rather_than_raised(tmp_path):
    """A null byte raises out of `resolve`, and a route that raises leaves the page waiting for ever."""
    assert local_target(whole(tmp_path), f"{ORIGIN}/%00.html").refused != ""


def test_a_directory_is_served_as_its_index(tmp_path):
    (tmp_path / "deck").mkdir()
    assert local_target(whole(tmp_path), f"{ORIGIN}/deck/").path == tmp_path / "deck" / "index.html"
    assert local_target(whole(tmp_path), f"{ORIGIN}/").path == tmp_path / "index.html"


def test_a_directory_whose_index_is_a_link_to_a_dot_name_is_refused(tmp_path):
    """The index is the file the request opens, so the rule is about it rather than the directory."""
    (tmp_path / ".env").write_text("DECKTALK_TEST_KEY=not-a-key", encoding="utf-8")
    (tmp_path / "leak").mkdir()
    (tmp_path / "leak" / "index.html").symlink_to(tmp_path / ".env")
    beyond = tmp_path.parent / "beyond.html"
    beyond.write_text("no", encoding="utf-8")
    (tmp_path / "away").mkdir()
    (tmp_path / "away" / "index.html").symlink_to(beyond)
    assert local_target(whole(tmp_path), f"{ORIGIN}/leak/").refused == HIDDEN
    assert local_target(whole(tmp_path), f"{ORIGIN}/away/").refused == OUTSIDE


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("a.js", "text/javascript; charset=utf-8"),
        ("a.mjs", "text/javascript; charset=utf-8"),
        ("a.json", "application/json; charset=utf-8"),
        ("a.woff2", "font/woff2"),
        ("a.glb", "model/gltf-binary"),
        ("a.png", "image/png"),
        ("a.unheard-of", "application/octet-stream"),
    ],
)
def test_every_type_a_deck_loads_is_served_as_itself(name, want):
    """An ES module served as text/plain does not run, which is the whole reason this table exists."""
    assert content_type(Path(name)) == want


def test_the_asset_record_keeps_each_path_once_and_in_order(tmp_path):
    assets = Assets(root=tmp_path)
    for name in ("deck/index.html", "deck/style.css", "deck/index.html"):
        assets.record(tmp_path / name, found=True)
    assets.record(tmp_path / "media/gone.png", found=False)
    assert assets.paths == ["deck/index.html", "deck/style.css"]
    assert assets.missing == ["media/gone.png"]


class _Route:
    """Playwright's route object, as far as the handler under test uses it."""

    def __init__(self) -> None:
        self.answer: dict[str, object] | None = None
        self.continued = False

    def fulfill(self, **kwargs: object) -> None:
        self.answer = kwargs

    def continue_(self) -> None:
        self.continued = True


class _Target:
    """A Playwright page or context, as far as `route_pages` uses it."""

    def __init__(self) -> None:
        self.handler: Any = None

    def route(self, _pattern: str, handler: Any) -> None:  # noqa: ANN401  (Playwright's own handler type)
        self.handler = handler

    def request(self, url: str) -> _Route:
        route = _Route()
        self.handler(route, type("Request", (), {"url": url})())
        return route


def test_the_router_answers_a_project_file_and_leaves_every_other_origin_alone(tmp_path):
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    target = _Target()
    assets = route_pages(target, whole(tmp_path))

    served = target.request(f"{ORIGIN}/deck/index.html")
    assert served.answer == {"status": 200, "content_type": "text/html", "body": b"<p>hi</p>"}
    assert assets.paths == ["deck/index.html"]

    missing = target.request(f"{ORIGIN}/deck/gone.css")
    assert missing.answer is not None and missing.answer["status"] == 404
    assert assets.missing == ["deck/gone.css"]

    outside = target.request("https://cdn.example.com/katex.js")
    assert outside.continued and outside.answer is None

    secret = target.request(f"{ORIGIN}/.env")
    assert secret.answer is not None and secret.answer["status"] == 403
    assert not secret.continued and assets.paths == ["deck/index.html"]

    climbed = target.request(f"{ORIGIN}/../../etc/passwd")
    assert climbed.answer is not None and climbed.answer["status"] == 403
    assert not climbed.continued, "an escape is refused rather than sent to the network"


def test_the_server_serves_the_project_and_names_every_page(tmp_path):
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>served</p>", encoding="utf-8")
    server = open_server(whole(tmp_path), "127.0.0.1", 0)
    with server:
        [url] = served_urls(server, ["deck/index.html"])
        assert url.startswith("http://127.0.0.1:") and url.endswith("/deck/index.html")
        Thread(target=server.serve_forever, daemon=True).start()
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.read() == b"<p>served</p>"
            assert response.headers["Cache-Control"] == "no-store"
        server.shutdown()


def _get(url: str) -> tuple[int, bytes]:
    """One request against a running server, with the status of a refusal rather than an exception."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()


def test_the_server_refuses_a_dotfile_a_listing_and_a_link_out_of_the_project(tmp_path):
    """The preview server applies the rule the router applies, so it is not the wider door."""
    (tmp_path / ".env").write_text("DECKTALK_TEST_KEY=not-a-key", encoding="utf-8")
    (tmp_path / "envlink").symlink_to(tmp_path / ".env")
    (tmp_path / "deck").mkdir()
    outside = tmp_path.parent / "beyond"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("no", encoding="utf-8")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    (tmp_path / "leak").mkdir()
    (tmp_path / "leak" / "index.html").symlink_to(tmp_path / ".env")
    (tmp_path / "leakhtm").mkdir()
    (tmp_path / "leakhtm" / "index.htm").symlink_to(tmp_path / ".env")
    server = open_server(whole(tmp_path), "127.0.0.1", 0)
    with server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        Thread(target=server.serve_forever, daemon=True).start()
        assert _get(f"{base}/.env")[0] == 403
        assert _get(f"{base}/deck/../.env")[0] == 403
        assert _get(f"{base}/escape/secret.txt")[0] == 403
        assert _get(f"{base}/envlink")[0] == 403, "a link inside the project is one more name for the file"
        assert _get(f"{base}/leak/")[0] == 403, "a directory index is the file the request opens"
        status, body = _get(f"{base}/leakhtm/")
        assert status == 404 and b".env" not in body, "the base class may open only the index the rule approved"
        status, body = _get(f"{base}/deck/")
        assert status == 404 and b".env" not in body
        assert b".env" not in _get(f"{base}/")[1]
        server.shutdown()


def test_the_ipv6_loopback_binds_as_readily_as_the_ipv4_one(tmp_path):
    """`--host` offers a loopback address, so the safest one an author can name has to work."""
    server = open_server(whole(tmp_path), "::1", 0)
    with server:
        [url] = served_urls(server, ["index.html"])
        assert url.startswith("http://[::1]:") and reachable_warning(server) == ""


def test_the_printed_url_names_the_address_the_socket_is_bound_to(tmp_path):
    """An author reads the URL to know who can reach the page, so it may not say loopback for any bind."""
    server = open_server(whole(tmp_path), "0.0.0.0", 0)
    with server:
        [url] = served_urls(server, ["index.html"])
        assert url.startswith("http://0.0.0.0:")
        assert "every machine on this network" in reachable_warning(server)


def test_a_loopback_bind_warns_about_nothing(tmp_path):
    server = open_server(whole(tmp_path), "127.0.0.1", 0)
    with server:
        assert reachable_warning(server) == ""


def test_a_port_already_in_use_says_which_flag_to_change(tmp_path):
    first = open_server(whole(tmp_path), "127.0.0.1", 0)
    with first:
        port = first.server_address[1]
        with pytest.raises(ToolError, match="--port"):
            open_server(whole(tmp_path), "127.0.0.1", int(port))


@pytest.mark.browser
def test_a_page_fetches_a_file_beside_it_from_the_origin(tmp_path):
    """`fetch` does not support the file scheme, so this is the whole point of the origin."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "facts.json").write_text(json.dumps({"answer": 42}), encoding="utf-8")
    (tmp_path / "page.html").write_text(FETCH_PAGE, encoding="utf-8")
    with chromium() as browser:
        allowed = Allowed.of(tmp_path, ["page.html", "data"])
        page, assets = open_page(browser, allowed, width=400, height=300)
        page.goto(page_url("page.html"), wait_until="load")
        page.wait_for_function("() => window.answer !== undefined")
        assert page.evaluate("() => window.answer") == 42
        assert page.evaluate("() => location.origin") == ORIGIN
        assert assets.paths == ["page.html", "data/facts.json"]


# ---- the allowlist -------------------------------------------------------------------------------


def project(tmp_path: Path) -> Allowed:
    """A project as an author leaves one: a deck, a media directory, and everything else beside them."""
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "clip.mp4").write_bytes(b"clip")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "cue-times.json").write_text("{}", encoding="utf-8")
    (tmp_path / "script.md").write_text("# 1. The opening", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    return Allowed.of(tmp_path, ["deck", "media", "logo.svg"])


def test_the_origin_serves_the_declared_directories_and_files_and_nothing_else(tmp_path):
    """The script, the cues and everything under `build/` are beside the deck and are not part of it."""
    allowed = project(tmp_path)
    assert local_target(allowed, f"{ORIGIN}/deck/index.html").path == tmp_path / "deck" / "index.html"
    assert local_target(allowed, f"{ORIGIN}/media/clip.mp4").path == tmp_path / "media" / "clip.mp4"
    assert local_target(allowed, f"{ORIGIN}/logo.svg").path == tmp_path / "logo.svg"
    for refused in ("script.md", "decktalk.toml", "build/cue-times.json", "build/"):
        assert local_target(allowed, f"{ORIGIN}/{refused}").refused == UNDECLARED, refused


def test_a_declared_file_opens_that_file_and_not_the_directory_it_sits_in(tmp_path):
    """One declared file is one door, so the files beside it stay shut."""
    allowed = project(tmp_path)
    assert local_target(allowed, f"{ORIGIN}/logo.svg").path is not None
    assert local_target(allowed, f"{ORIGIN}/script.md").refused == UNDECLARED


def test_a_declaration_names_a_spelling_and_a_declared_directory_names_a_place(tmp_path):
    """The comparison is over names, so no filesystem can fold a request onto a file nobody declared.

    A machine whose filesystem folds case opens one file for `logo.svg` and `Logo.svg`, and the rule
    is that the project declared one of those names. The declared directory is the other half: it is
    a place, so every name under it is served and what the two spellings open is the disk's business.
    The platform half of this pair is `tests/platform/test_case.py`.
    """
    allowed = project(tmp_path)
    assert local_target(allowed, f"{ORIGIN}/logo.svg").path is not None
    assert local_target(allowed, f"{ORIGIN}/Logo.svg").refused == UNDECLARED
    assert local_target(allowed, f"{ORIGIN}/deck/Index.html").path == tmp_path / "deck" / "Index.html"


def test_a_declared_file_is_served_to_the_directory_that_opens_it(tmp_path):
    """A request for a directory opens its index, so the name the declaration answers is that index."""
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    allowed = Allowed.of(tmp_path, ["deck/index.html"])
    assert local_target(allowed, f"{ORIGIN}/deck/").path == tmp_path / "deck" / "index.html"
    assert local_target(allowed, f"{ORIGIN}/deck/other.css").refused == UNDECLARED


def test_the_rules_are_applied_in_the_order_that_names_the_worst_problem_first(tmp_path):
    """A dot name and an escape are refused as themselves, because they say more than undeclared would."""
    allowed = project(tmp_path)
    (tmp_path / ".env").write_text("DECKTALK_TEST_KEY=not-a-key", encoding="utf-8")
    assert local_target(allowed, f"{ORIGIN}/.env").refused == HIDDEN
    assert local_target(allowed, f"{ORIGIN}/../../etc/passwd").refused == OUTSIDE


def test_a_declaration_outside_the_project_is_dropped_rather_than_opened(tmp_path):
    """A rule that names a place the project does not own is a mistake in the project file."""
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("no", encoding="utf-8")
    allowed = Allowed.of(tmp_path, ["deck", "../elsewhere", str(outside)])
    assert allowed.served == ("deck",)
    assert local_target(allowed, f"{ORIGIN}/../elsewhere/secret.txt").refused == OUTSIDE


def test_the_router_records_what_it_turned_away(tmp_path):
    """A page reaching for the script is a fact the recording carries, not a 403 only the page saw."""
    target = _Target()
    assets = route_pages(target, project(tmp_path))
    refused = target.request(f"{ORIGIN}/script.md")
    assert refused.answer is not None and refused.answer["status"] == 403
    assert assets.refused == [f"script.md ({UNDECLARED})"]
    assert assets.paths == [] and assets.missing == []


def test_the_preview_server_answers_the_documents_the_router_answers(tmp_path):
    """An author previewing a deck reads its cue times off the origin exactly as the recorder does."""
    times = b'{"sections": []}'
    server = open_server(project(tmp_path), "127.0.0.1", 0, {"/__decktalk/cue-times.json": times})
    with server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        Thread(target=server.serve_forever, daemon=True).start()
        assert _get(f"{base}/__decktalk/cue-times.json") == (200, times)
        assert _get(f"{base}/__decktalk/nothing.json")[0] == 403
        server.shutdown()


def test_the_preview_server_refuses_what_the_router_refuses(tmp_path):
    """Both halves apply one rule, so an author's own browser reaches exactly what the recorder does."""
    server = open_server(project(tmp_path), "127.0.0.1", 0)
    with server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        Thread(target=server.serve_forever, daemon=True).start()
        assert _get(f"{base}/deck/index.html") == (200, b"<p>hi</p>")
        assert _get(f"{base}/script.md")[0] == 403
        assert _get(f"{base}/build/cue-times.json")[0] == 403
        server.shutdown()
