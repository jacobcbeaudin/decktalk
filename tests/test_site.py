"""The landing page's film player, in a real headless Chromium, with media.decktalk.app answered locally.

uv run pytest -m browser tests/test_site.py
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

SITE = Path(__file__).resolve().parent.parent / "site" / "index.html"
MEDIA = "https://media.decktalk.app"
PLAYER = "document.getElementById('demo-video')"


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    with sync_playwright() as pw:
        try:
            # The tests press play from a script, which Chromium allows only without its autoplay rule.
            browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {str(exc).splitlines()[0]}")
        yield browser
        browser.close()


@pytest.fixture
def site(browser):
    opened = []

    def open_site(**kwargs) -> FakeMedia:
        media = FakeMedia(browser.new_page(), **kwargs)
        opened.append(media)
        return media.open()

    yield open_site
    for media in opened:
        media.page.close()


@dataclass
class FakeMedia:
    """The landing page, with a fake media Worker and every other host refused.

    Link n that the fake signs ends in `link=n`. `sign_statuses[i]` is the status of sign request
    i, and `expires_in[i]` is how many seconds link i+1 lasts. Both default to 200 and an hour.
    Like the real Worker, the fake lets the page read a refusal. With `readable_refusals` off, a
    refusal carries no CORS header, as one from Cloudflare's rate limit does, so the page cannot read it.
    """

    page: object
    sign_statuses: list[int] = field(default_factory=list)
    expires_in: list[int] = field(default_factory=list)
    film_status: int = 403
    readable_refusals: bool = True
    query: str = ""
    signed: list[str] = field(default_factory=list)
    films: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)

    def open(self) -> FakeMedia:
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.console.append(m.text))
        self.page.route(re.compile(r"^https?://(?!media\.decktalk\.app/)"), lambda route: route.abort())
        self.page.route(f"{MEDIA}/**", self.answer)
        self.page.goto(SITE.as_uri() + self.query)
        return self

    def answer(self, route) -> None:
        url = route.request.url
        if "/sign/" not in url:
            self.films.append(url)
            route.fulfill(status=self.film_status, body="")
            return
        self.signed.append(url.rsplit("/sign/", 1)[1])
        n = len(self.signed)
        status = self.sign_statuses[n - 1] if n <= len(self.sign_statuses) else 200
        lasts = self.expires_in[n - 1] if n <= len(self.expires_in) else 3600
        body = {"url": f"{MEDIA}/v/film.mp4?link={n}", "expires": int(time.time()) + lasts}
        readable = status == 200 or self.readable_refusals
        headers = {"Access-Control-Allow-Origin": "*"} if readable else {}
        route.fulfill(status=status, headers=headers, content_type="application/json", body=json.dumps(body))

    def run(self, script: str) -> object:
        return self.page.evaluate(f"() => {{ const v = {PLAYER}; {script} }}")

    def wait_until(self, check: Callable[[], bool], what: str, timeout: float = 6.0) -> None:
        deadline = time.monotonic() + timeout
        while not check():
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {what}; signed {self.signed}, films {self.films}")
            self.page.wait_for_timeout(50)

    def src(self) -> str:
        return self.run("return v.getAttribute('src') || '';")

    def wait_for_link(self, n: int) -> None:
        self.wait_until(lambda: self.src().endswith(f"link={n}"), f"link {n} in the player")

    def wait_for_film(self, n: int) -> None:
        self.wait_until(lambda: any(f.endswith(f"link={n}") for f in self.films), f"a film request on link {n}")


def test_the_player_asks_for_a_link_to_its_own_cut_and_loads_nothing_yet(site):
    media = site()
    media.wait_for_link(1)
    key = media.page.get_attribute("#demo-video", "data-media")
    assert re.fullmatch(r"decktalk-demo-\d{8}T\d{6}Z-[0-9a-f]{12}\.mp4", key or "")
    assert media.signed == [key]
    media.page.wait_for_timeout(300)
    assert media.films == []
    assert media.errors == []


def test_a_server_error_on_the_link_is_retried(site):
    media = site(sign_statuses=[503])
    media.wait_for_link(2)
    assert len(media.signed) == 2
    assert media.errors == []


def test_a_refused_link_is_not_retried_until_the_visitor_presses_play(site):
    media = site(sign_statuses=[404])
    media.page.wait_for_timeout(1500)
    assert len(media.signed) == 1
    assert media.src() == ""
    assert media.page.get_attribute("#demo-video", "poster") == "media/decktalk-demo-poster.jpg"
    media.run("v.play().catch(() => {});")
    media.wait_for_film(2)
    assert media.errors == []


def test_a_refusal_the_page_cannot_read_is_retried_like_a_network_failure(site):
    media = site(sign_statuses=[429, 429, 429], readable_refusals=False)
    media.wait_until(lambda: len(media.signed) == 3, "three link requests")
    media.page.wait_for_timeout(1000)
    assert len(media.signed) == 3
    assert media.src() == ""
    assert media.errors == []


def test_pressing_play_before_the_link_arrives_plays_it_when_it_does(site):
    # The first request fails, so the link arrives a second later, after play was pressed.
    media = site(sign_statuses=[503])
    media.run("v.play().catch(() => {});")
    media.wait_for_film(2)
    assert media.errors == []


def test_a_link_that_stops_working_is_replaced_once_and_then_the_player_waits(site):
    media = site(film_status=403)
    media.wait_for_link(1)
    media.run("v.play().catch(() => {});")
    media.wait_for_film(2)
    media.page.wait_for_timeout(1000)
    # The fresh link failed too, so the player stops asking rather than looping.
    assert len(media.signed) == 2
    # Pressing play again is the visitor asking to try once more.
    media.run("v.play().catch(() => {});")
    media.wait_for_film(3)
    assert media.errors == []


def test_a_link_about_to_expire_is_replaced_when_the_visitor_presses_play(site):
    media = site(expires_in=[10])
    media.wait_for_link(1)
    media.run("v.play().catch(() => {});")
    media.wait_for_film(2)
    assert media.errors == []


def test_debug_logs_each_step_only_when_asked(site):
    quiet = site()
    quiet.wait_for_link(1)
    assert not any(line.startswith("[film]") for line in quiet.console)
    loud = site(query="?debug")
    loud.wait_for_link(1)
    loud.wait_until(lambda: any("new link in place" in line for line in loud.console), "the debug log")
    assert any(line.startswith("[film] reload") for line in loud.console)
