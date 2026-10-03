"""The local origin: the URL a page is opened at, what the router will serve, and the author's server."""

from __future__ import annotations

import contextlib
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from decktalk.errors import ToolError
from decktalk.media import origin
from decktalk.media.browser import TRUSTED, UNTRUSTED, chromium
from decktalk.media.origin import (
    HIDDEN,
    OFF_ORIGIN,
    ORIGIN,
    OUTSIDE,
    RESERVED,
    UNDECLARED,
    Allowed,
    Assets,
    content_type,
    local_target,
    open_server,
    page_url,
    route_pages,
    served_url,
)
from decktalk.media.pages import open_page
from decktalk.page import ENGINE_PATH, Q
from decktalk.toolchain.assets import RUNTIME_FILE, katex_dir, runtime_path
from support.fakes import FakeRouter
from support.logs import data_of

WHOLE = ("deck", "envlink", "pub", "escape", "leak", "away", "leakhtm")
"""Every name the tests about the other rules ask for, declared so that those rules are what refuse."""


def whole(root: Path) -> Allowed:
    """The rule of a project that declared every name these tests reach, for the tests about the other rules."""
    return Allowed.of(root, WHOLE)


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
    assert local_target(whole(tmp_path), f"{ORIGIN}/").refused == UNDECLARED


@pytest.mark.parametrize("root", [".", "", "./", "deck/.."])
def test_a_declaration_of_the_project_root_declares_nothing(tmp_path, root):
    """The root holds the script, the cue file, the build directory and the credential."""
    for name in ("script.md", "cues.json", "build/narrate/takes.json"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}", encoding="utf-8")
    allowed = Allowed.of(tmp_path, [root])
    assert allowed.served == ()
    for name in ("script.md", "cues.json", "build/narrate/takes.json"):
        assert local_target(allowed, f"{ORIGIN}/{name}").refused == UNDECLARED
    assert not Allowed(root=tmp_path, served=("",)).declares("script.md")


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


def test_the_router_answers_a_project_file_and_leaves_every_other_origin_alone(tmp_path):
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    target = FakeRouter()
    assets = route_pages(target.page(), whole(tmp_path), trusted=True)

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


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/katex.js",
        "http://169.254.169.254/latest/meta-data/",
        "https://project.localhost/deck/index.html",
        "http://project.localhost:8080/deck/index.html",
        "http://127.0.0.1:9000/admin?key=secret",
    ],
)
def test_an_untrusted_page_reaches_no_origin_but_the_projects_own(tmp_path, url):
    """0.5.0 sent every request for another origin to the network, whoever wrote the page."""
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    target = FakeRouter()
    assets = route_pages(target.page(), whole(tmp_path), trusted=False)
    refused = target.request(url)
    assert refused.aborted == "blockedbyclient"
    assert not refused.continued and refused.answer is None
    assert assets.refused == [f"{url.split('?')[0]} ({OFF_ORIGIN})"]
    # The host is still named, so the recording says what the page reached for.
    assert assets.external == [url.split("/")[0] + "//" + url.split("/")[2]]
    served = target.request(f"{ORIGIN}/deck/index.html")
    assert served.answer is not None and served.answer["status"] == 200


SHUTDOWN_POLL_SECONDS = 0.01
"""How often a test's server looks for its stop, which `serve_forever` leaves at half a second a test."""


@contextlib.contextmanager
def serving(allowed: Allowed, documents: dict[str, bytes] | None = None) -> Iterator[str]:
    """A preview server answering on its own thread, and the URL it prints, stopped however the test ends."""
    server = open_server(allowed, "127.0.0.1", 0, documents)
    with server:
        Thread(target=server.serve_forever, args=(SHUTDOWN_POLL_SECONDS,), daemon=True).start()
        try:
            yield served_url(server)
        finally:
            server.shutdown()


def test_the_server_serves_the_project_and_names_every_page(tmp_path):
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>served</p>", encoding="utf-8")
    with serving(whole(tmp_path)) as base:
        assert base.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(f"{base}/deck/index.html", timeout=5) as response:
            assert response.read() == b"<p>served</p>"
            assert response.headers["Cache-Control"] == "no-store"


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
    with serving(whole(tmp_path)) as base:
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


def test_the_ipv6_loopback_binds_as_readily_as_the_ipv4_one(tmp_path):
    """`--host` offers a loopback address, so the safest one an author can name has to work."""
    server = open_server(whole(tmp_path), "::1", 0)
    with server:
        assert served_url(server).startswith("http://[::1]:")


def test_the_printed_url_names_the_address_the_socket_is_bound_to(tmp_path):
    """An author reads the URL to know who can reach the page, so it may not say loopback for any bind."""
    server = open_server(whole(tmp_path), "0.0.0.0", 0)
    with server:
        assert served_url(server).startswith("http://0.0.0.0:")


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
    with chromium(policy=TRUSTED, spend=False) as browser:
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
    target = FakeRouter()
    assets = route_pages(target.page(), project(tmp_path), trusted=True)
    refused = target.request(f"{ORIGIN}/script.md")
    assert refused.answer is not None and refused.answer["status"] == 403
    assert assets.refused == [f"script.md ({UNDECLARED})"]
    assert assets.paths == [] and assets.missing == []


def test_the_preview_server_answers_the_documents_the_router_answers(tmp_path):
    """An author previewing a deck reads its cue times off the origin exactly as the recorder does."""
    times = b'{"sections": []}'
    with serving(project(tmp_path), {"/__decktalk/cue-times.json": times}) as base:
        assert _get(f"{base}/__decktalk/cue-times.json") == (200, times)
        assert _get(f"{base}/__decktalk/nothing.json")[0] == 403


def test_the_preview_server_refuses_what_the_router_refuses(tmp_path):
    """Both halves apply one rule, so an author's own browser reaches exactly what the recorder does."""
    with serving(project(tmp_path)) as base:
        assert _get(f"{base}/deck/index.html") == (200, b"<p>hi</p>")
        assert _get(f"{base}/script.md")[0] == 403
        assert _get(f"{base}/build/cue-times.json")[0] == 403


# ---- what the two halves leave behind -------------------------------------------------------------


def test_a_request_the_router_could_not_answer_is_a_warning_without_its_query(tmp_path, monkeypatch, caplog):
    (tmp_path / "deck").mkdir()
    target = FakeRouter()
    route_pages(target.page(), whole(tmp_path), trusted=True)

    def broken(*_args: object) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(origin, "local_target", broken)
    with caplog.at_level("DEBUG", logger="decktalk"):
        answered = target.request(f"{ORIGIN}/deck/index.html?token=sk_query_canary")
    assert answered.answer is not None and answered.answer["status"] == 500
    [record] = [record for record in caplog.records if record.name == "decktalk.media.origin"]
    assert record.levelname == "WARNING" and "sk_query_canary" not in record.getMessage()
    assert data_of(record) == {"url": f"{ORIGIN}/deck/index.html"}


def test_the_preview_server_prints_nothing_when_a_request_raises_and_logs_no_query(
    tmp_path, monkeypatch, caplog, capsys
):
    """socketserver printed a traceback to stderr when a handler raised, which no renderer could hold."""
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<p>served</p>", encoding="utf-8")

    def broken(_handler: object) -> None:
        raise RuntimeError("the handler broke")

    with caplog.at_level("DEBUG", logger="decktalk"), serving(whole(tmp_path)) as base:
        assert _get(f"{base}/deck/index.html?token=sk_query_canary")[0] == 200
        monkeypatch.setattr(origin._Handler, "send_head", broken)
        with contextlib.suppress(OSError):
            _get(f"{base}/deck/index.html")
    assert capsys.readouterr().err == ""
    said = [record.getMessage() for record in caplog.records if record.name == "decktalk.media.origin"]
    assert any("GET /deck/index.html HTTP" in line for line in said)
    assert not any("sk_query_canary" in line for line in said)
    assert any("could not answer" in line for line in said)


# ---- the engine's own files ----------------------------------------------------------------------

ENGINE_SERVED = (
    f"{ENGINE_PATH}decktalk-runtime.js",
    f"{ENGINE_PATH}katex/katex.min.js",
    f"{ENGINE_PATH}katex/katex.min.css",
    f"{ENGINE_PATH}katex/fonts/KaTeX_Main-Regular.woff2",
)
"""Four of the files the engine answers under its own path, from the installed package."""


def _packaged(url_path: str) -> bytes:
    """The bytes the installed package holds for one engine path."""
    name = url_path.removeprefix(ENGINE_PATH)
    return (runtime_path() if name == RUNTIME_FILE else katex_dir() / name.removeprefix("katex/")).read_bytes()


@pytest.mark.parametrize("trusted", [True, False])
def test_the_router_answers_the_runtime_and_katex_from_the_engine(tmp_path, trusted):
    """A project holds no copy of either, so the origin reads both from the installed engine."""
    target = FakeRouter()
    assets = route_pages(target.page(), project(tmp_path), trusted=trusted)
    for asked in ENGINE_SERVED:
        served = target.request(f"{ORIGIN}{asked}")
        assert served.answer is not None and served.answer["status"] == 200, asked
        assert served.answer["body"] == _packaged(asked), asked
        assert served.answer["content_type"] == content_type(Path(asked)), asked
    # The engine's files are not the project's, so no recording is keyed on them as project assets.
    assert assets.paths == [] and assets.missing == [] and assets.refused == []


def test_the_preview_server_answers_the_runtime_and_katex_from_the_engine(tmp_path):
    with serving(project(tmp_path)) as base:
        for asked in ENGINE_SERVED:
            assert _get(f"{base}{asked}") == (200, _packaged(asked)), asked


def _raw(base: str, path: str) -> tuple[int, bytes]:
    """One request sent with its path exactly as written, which no client library folds first."""
    host, port = urlsplit(base).hostname, urlsplit(base).port
    connection = http.client.HTTPConnection(str(host), port, timeout=5)
    try:
        connection.putrequest("GET", path, skip_accept_encoding=True)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


ENGINE_ESCAPES = (
    f"{ENGINE_PATH}../script.md",
    f"{ENGINE_PATH}../.env",
    f"{ENGINE_PATH}katex/../../.env",
    f"{ENGINE_PATH}%2e%2e/.env",
    f"{ENGINE_PATH}..%2f.env",
    f"{ENGINE_PATH}katex/fonts/../../../decktalk.toml",
    f"{ENGINE_PATH}katex/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    f"{ENGINE_PATH}decktalk-probe.js",
    f"{ENGINE_PATH}runtime/decktalk-probe.js",
    f"{ENGINE_PATH}__init__.py",
    f"{ENGINE_PATH}katex/../template/AGENTS.md",
    f"{ENGINE_PATH}katex",
    f"{ENGINE_PATH}katex/",
    f"{ENGINE_PATH}mine.js",
    ENGINE_PATH,
)
"""Requests under the engine's path that name anything but the runtime and the KaTeX release."""

LEAKS = (b"DECKTALK_TEST_KEY", b"The opening", b"[project]", b"root:", b"import ", b"window.mine", b"The page contract")
"""What each of those requests would carry if the engine's path were a door into the project or the package."""


def _with_secrets(tmp_path: Path) -> Allowed:
    """A project with a credential beside it and a file of its own under the engine's path."""
    allowed = project(tmp_path)
    (tmp_path / ".env").write_text("DECKTALK_TEST_KEY=not-a-key", encoding="utf-8")
    (tmp_path / "deck" / "__decktalk").mkdir()
    reserved = tmp_path / ENGINE_PATH.strip("/")
    reserved.mkdir()
    (reserved / "mine.js").write_text("window.mine = 1;", encoding="utf-8")
    return Allowed.of(tmp_path, [*allowed.served, ENGINE_PATH.strip("/")])


@pytest.mark.parametrize("path", ENGINE_ESCAPES)
def test_the_engine_path_serves_the_runtime_and_katex_and_nothing_else(tmp_path, path):
    """The engine's path is a closed list of packaged files, so no spelling under it reaches another file."""
    allowed = _with_secrets(tmp_path)
    with serving(allowed) as base:
        status, body = _raw(base, path)
    assert status in (403, 404), (path, status)
    assert not any(leak in body for leak in LEAKS), path
    target = FakeRouter()
    route_pages(target.page(), allowed, trusted=True)
    routed = target.request(f"{ORIGIN}{path}")
    assert routed.answer is not None and routed.answer["status"] in (403, 404), path
    assert not routed.continued
    answered = routed.answer["body"]
    said = answered.encode("utf-8") if isinstance(answered, str) else answered
    assert isinstance(said, bytes) and not any(leak in said for leak in LEAKS), path


def test_a_project_file_under_the_engine_path_is_never_served(tmp_path):
    """The engine owns its path, so a project that declares a folder of that name cannot shadow it."""
    allowed = _with_secrets(tmp_path)
    assert local_target(allowed, f"{ORIGIN}{ENGINE_PATH}mine.js").refused == RESERVED
    assert local_target(allowed, f"{ORIGIN}/deck/..{ENGINE_PATH}mine.js").refused == RESERVED


def test_a_project_file_named_like_the_runtime_is_an_ordinary_project_file(tmp_path):
    """Only the engine's path is special, so a project file of that name is served or refused like any other."""
    allowed = project(tmp_path)
    mine = tmp_path / "deck" / RUNTIME_FILE
    mine.write_text("window.mine = 1;\n", encoding="utf-8")
    (tmp_path / RUNTIME_FILE).write_text("window.mine = 2;\n", encoding="utf-8")
    assert local_target(allowed, f"{ORIGIN}/deck/{RUNTIME_FILE}").path == mine.resolve()
    assert local_target(allowed, f"{ORIGIN}/{RUNTIME_FILE}").refused == UNDECLARED
    with serving(allowed) as base:
        assert _get(f"{base}/deck/{RUNTIME_FILE}") == (200, b"window.mine = 1;\n")
        assert _get(f"{base}/{RUNTIME_FILE}")[0] == 403


@pytest.mark.browser
def test_a_page_with_no_copy_of_the_runtime_gets_the_engines(tmp_path):
    """The page names the engine's path, the project holds no runtime and no KaTeX, and the page runs."""
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><title>engine</title>'
        f'<link rel="stylesheet" href="{ENGINE_PATH}katex/katex.min.css">'
        f'<script src="{ENGINE_PATH}katex/katex.min.js"></script>'
        f'<script src="{ENGINE_PATH}decktalk-runtime.js"></script>',
        encoding="utf-8",
    )
    with chromium(policy=UNTRUSTED, spend=False) as browser:
        page, assets = open_page(browser, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
        page.goto(page_url("deck/index.html"), wait_until="load")
        assert page.evaluate("() => window.DeckTalk.version") == page.evaluate("() => window.__decktalk.version")
        assert page.evaluate("() => typeof window.katex.render") == "function"
        assert assets.paths == ["deck/index.html"] and assets.refused == []
    assert sorted(p.name for p in (tmp_path / "deck").iterdir()) == ["index.html"]
