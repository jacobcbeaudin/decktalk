"""Launching Chromium under a page policy: the sandbox, the proxy, the scrubbed environment and the key."""

from __future__ import annotations

import inspect
import socket
import threading
import time
from pathlib import Path
from typing import Any, get_args

import pytest

from decktalk.errors import ApprovalRequired, ErrorCode, InputError, ToolError
from decktalk.media import browser, pages
from decktalk.media.environment import child_environment
from decktalk.media.origin import Allowed, page_url
from decktalk.settings import PAGE_POLICIES
from decktalk.toolchain.cache import caching_in
from support.fakes import BareBrowser, FakeChromium


def test_the_page_policies_this_module_accepts_are_the_ones_the_setting_publishes():
    assert set(get_args(browser.PagePolicy)) == set(PAGE_POLICIES)
    with pytest.raises(InputError, match="page_policy"):
        browser.page_policy("mostly")


INSTALLED = Path(__file__)
"""A file that is on disk, which is all a fake launch asks of the executable it would run."""


def test_an_untrusted_page_gets_the_sandbox_a_proxy_that_answers_nothing_and_no_webrtc_udp():
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.UNTRUSTED, spend=False)
    asked = chromium.asked[0]
    assert asked["chromium_sandbox"] is True
    assert asked["proxy"] == {"server": browser.DEAD_PROXY, "bypass": browser.EVERY_HOST}
    args = asked["args"]
    assert isinstance(args, list) and "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_a_trusted_page_keeps_the_machines_own_network_and_still_gets_a_scrubbed_environment():
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.TRUSTED, spend=False)
    asked = chromium.asked[0]
    assert "proxy" not in asked and "chromium_sandbox" not in asked
    assert asked["env"] == child_environment()


def test_a_machine_that_cannot_run_the_sandbox_is_refused_and_never_falls_back(monkeypatch):
    """A fetch cannot give a machine a sandbox, so the refusal says what the machine needs instead."""
    monkeypatch.setattr(browser.chromium_fetch, "fetch_chromium", lambda: pytest.fail("must not fetch"))
    chromium = FakeChromium(INSTALLED, refusal="No usable sandbox! Update your kernel.")
    with pytest.raises(ToolError, match="sandbox on") as raised:
        browser.launch(chromium.driver(), policy=browser.UNTRUSTED, spend=False)
    assert "seccomp" in (raised.value.hint or "")
    assert len(chromium.asked) == 1, "the sandbox was never dropped for a second try"


def test_a_run_that_may_spend_opens_no_untrusted_page_and_says_why():
    """The key is in reach of a run that may buy, even one with nothing to buy, so a stranger's page waits."""
    chromium = FakeChromium(INSTALLED)
    with pytest.raises(ApprovalRequired) as refused:
        browser.launch(chromium.driver(), policy=browser.UNTRUSTED, spend=True)
    assert chromium.asked == [], "the refusal comes before any browser starts"
    assert refused.value.code is ErrorCode.APPROVAL
    assert "may spend" in str(refused.value) and "untrusted page" in str(refused.value)
    assert "--spend" in (refused.value.hint or "")


def test_a_run_that_may_spend_opens_its_authors_own_page():
    """A trusted page is the author's own deck on the author's own machine, so a voiced build still records it."""
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.TRUSTED, spend=True)
    assert len(chromium.asked) == 1


def test_a_run_that_may_not_spend_opens_an_untrusted_page():
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.UNTRUSTED, spend=False)
    assert len(chromium.asked) == 1


def test_every_page_a_browser_opens_is_routed_by_the_policy_it_was_launched_under(monkeypatch, tmp_path):
    seen: list[bool] = []
    monkeypatch.setattr(pages, "route_pages", lambda *_a, trusted, **_k: seen.append(trusted))
    monkeypatch.setattr(pages, "instrument", lambda page: page)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakeChromium(INSTALLED).started())
    allowed = Allowed.of(tmp_path, ["deck"])
    for policy in (browser.TRUSTED, browser.UNTRUSTED):
        with caching_in(str(tmp_path / "cache")), browser.chromium(policy=policy, spend=False) as launched:
            pages.open_page(launched, allowed, width=10, height=10)
    # A browser this module never launched is routed as a stranger's page.
    pages.open_page(BareBrowser().browser(), allowed, width=10, height=10)
    assert seen == [True, False, False]


@pytest.mark.parametrize("name", ["policy", "spend"])
@pytest.mark.parametrize("opener", [browser.chromium, browser.launch])
def test_no_launch_has_a_page_policy_or_a_spend_to_fall_back_on(opener, name):
    """A default would be the guess of a caller that forgot one, and a thread that never saw the run's
    context would take it, so each launch is told by its caller whether the key is in reach."""
    assert inspect.signature(opener).parameters[name].default is inspect.Parameter.empty


LATE_SECONDS = 2.0
"""How long a channel is given to land after the page has tried it, which the trusted control always meets."""


LOCAL_NETWORK_ACCESS_OFF = "--disable-features=LocalNetworkAccessChecks"
"""The switch that stops Chromium refusing a loopback address on its own, so the policy is what refuses it.

With Chromium's own check on, a page at the project's origin reaches no listener on this machine
under either policy, and a test of the policy would pass with the policy switched off.
"""


CHANNELS = {"/fetch", "/img", "/beacon", "/ws", "/worker", "udp"}
"""What the listeners record for each channel the page opens, one name per channel."""


@pytest.mark.browser
@pytest.mark.parametrize("policy", ["trusted", "untrusted"])
def test_an_untrusted_page_reaches_nothing_through_any_channel_it_can_open(tmp_path, monkeypatch, httpserver, policy):
    """Routing alone let a WebSocket open and WebRTC send STUN packets, so each channel is tried here.

    The listeners sit on this machine, so a channel that reached one is a channel that could reach a
    cloud metadata address from a render host. The trusted page is the control: every channel reaches
    its listener there, so a channel the untrusted page does not reach is one the policy refused.
    """
    launched = browser.launch_options

    def unchecked(chosen: browser.PagePolicy) -> dict[str, Any]:
        options = launched(chosen)
        return options | {"args": [*options.get("args", []), LOCAL_NETWORK_ACCESS_OFF]}

    monkeypatch.setattr(browser, "launch_options", unchecked)
    udp_hits: set[str] = set()

    def hits() -> set[str]:
        return {request.path for request, _ in httpserver.log} | udp_hits

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)

    def stun() -> None:
        while True:
            try:
                udp.recvfrom(2048)
                udp_hits.add("udp")
            except TimeoutError:
                continue
            except OSError:
                return

    threading.Thread(target=stun, daemon=True).start()
    at = f"127.0.0.1:{httpserver.port}"
    stun_at = f"127.0.0.1:{udp.getsockname()[1]}"
    deck = tmp_path / "deck"
    deck.mkdir()
    deck.joinpath("index.html").write_text(
        f"""<!doctype html><meta charset=utf-8><title>t</title><script>
window.tried = (async () => {{
  try {{ await fetch('http://{at}/fetch'); }} catch (e) {{}}
  await new Promise(ok => {{ const i = new Image(); i.onload = i.onerror = ok; i.src = 'http://{at}/img'; }});
  try {{ navigator.sendBeacon('http://{at}/beacon', 'x'); }} catch (e) {{}}
  await new Promise(ok => {{ try {{ const w = new WebSocket('ws://{at}/ws');
    w.onopen = w.onerror = () => ok(); setTimeout(ok, 1500); }} catch (e) {{ ok(); }} }});
  await new Promise(ok => {{ const src = "fetch('http://{at}/worker').then(() => postMessage(1), () => postMessage(0))";
    const w = new Worker(URL.createObjectURL(new Blob([src], {{ type: 'text/javascript' }})));
    w.onmessage = ok; setTimeout(ok, 2000); }});
  await new Promise(ok => {{ try {{
    const pc = new RTCPeerConnection({{ iceServers: [{{ urls: 'stun:{stun_at}' }}] }});
    pc.createDataChannel('x'); pc.createOffer().then(o => pc.setLocalDescription(o)); setTimeout(ok, 2000);
  }} catch (e) {{ ok(); }} }});
  return true;
}})();
</script>""",
        encoding="utf-8",
    )
    try:
        with browser.chromium(policy=policy, spend=False) as real:
            page, assets = pages.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
            page.goto(page_url("deck/index.html"), wait_until="load")
            assert page.evaluate("() => window.tried") is True
            # A beacon and a STUN packet may land after the page is done, so both sides wait as long.
            deadline = time.monotonic() + LATE_SECONDS
            while hits() != CHANNELS and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        udp.close()
    assert hits() == (CHANNELS if policy == "trusted" else set())
    assert f"http://{at}" in assets.external
