"""Headless Chromium through Playwright: launching it under the page policy a project sets.

This is the only module that launches a browser, and therefore the one place that fetches one: a
machine without Chromium gets it here, the first time a command needs it, the way `ffmpeg.py` gets
ffmpeg. `pages.py` drives a page in the browser this launches, and `recording.py` records one, so a
stage above asks for a recording or a frame and never for a browser.

`[record] page_policy` decides how far the page itself is trusted, and every launch reads it, so
`check`, `storyboard` and the poster follow the same policy as `record`. A trusted page is the
author's own work and reaches the network as it would in the author's browser. An untrusted page is
a stranger's. Its Chromium runs with the sandbox on and refuses to start without it, its requests
off the origin are aborted by the router, and its browser is pointed at a proxy that answers nothing,
which closes the channels routing never sees: a WebSocket, a DNS lookup and a WebRTC probe. Under
both policies the browser is given the environment `environment.py` builds rather than the
process's own, so a key the host holds never reaches the process that runs a page's script.

A run that may spend never opens an untrusted page. The key is in reach of such a run whether or not
it finds anything to buy, so `launch` refuses before any browser starts, and a host voices in one run
and renders a stranger's deck in another. A trusted page is the author's own deck on the author's
own machine, so a voiced build still records it.
"""

from __future__ import annotations

import logging
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from playwright.sync_api import Browser, Playwright
from playwright.sync_api import Error as PlaywrightError

from ..errors import ApprovalRequired, InputError, ToolError
from ..settings import PAGE_POLICIES
from ..toolchain import chromium_fetch
from .environment import child_environment

log = logging.getLogger(__name__)


def choice_of[S: str](key: str, value: str, choices: tuple[S, ...]) -> S:
    """One closed `[record]` key's value, refused unless it is a settings choice, whose own entry it returns."""
    if value not in choices:
        raise InputError(
            f"[record] {key} = {value!r} is not one of {', '.join(choices)}.",
            hint=f"Set it to one of {', '.join(choices)}.",
        )
    return choices[choices.index(value)]


PagePolicy = Literal["trusted", "untrusted"]
"""How far a page is trusted, which is the closed set `[record] page_policy` publishes."""

TRUSTED: PagePolicy = "trusted"
UNTRUSTED: PagePolicy = "untrusted"

DEAD_PROXY = "http://127.0.0.1:9"
"""Truth: the discard port on this machine, where no proxy answers, so a request sent to it goes nowhere.

The router answers the project's origin before the network stack sees a request, so the proxy is
reached only by what routing cannot stop, which is every channel an untrusted page must not have.
"""

EVERY_HOST = "<-loopback>"
"""Truth: Chromium's word for taking loopback off its implicit bypass list, so no host skips the proxy."""

UNTRUSTED_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",)
"""What an untrusted page's Chromium is started with, which keeps WebRTC from sending UDP past the proxy."""

SANDBOX_HINT = (
    "An untrusted page runs only inside Chromium's sandbox. In a container that means a user other than "
    "root and a seccomp profile that allows user namespaces."
)
"""What a machine that cannot start the sandbox is told, because the sandbox is a property of the machine."""


KEY_BESIDE_PAGE = (
    "this run may spend, and a run that may spend opens no untrusted page, because the voice key would be "
    "in reach of the process that runs a stranger's script."
)
"""What a run that may spend is told when it reaches for a page it does not trust."""

KEY_BESIDE_PAGE_HINT = (
    "Voice in a run of its own with `decktalk narrate --spend`, then render in a run without --spend."
)
"""The next step, which splits the work the refusal would not let one run do."""


def page_policy(value: str) -> PagePolicy:
    """The policy a page is opened under, refused here rather than read as the weaker of the two.

    An unknown value is a project file that says something untrue about the render, and reading it as
    trusted would open the network to a page whose host meant to close it.
    """
    return choice_of("page_policy", value, PAGE_POLICIES)


def launch_options(policy: PagePolicy) -> dict[str, Any]:
    """The keyword arguments a launch under `policy` passes to Playwright, beyond the executable.

    The environment is scrubbed under both policies. The sandbox, the proxy and the WebRTC switch are
    the untrusted policy's alone, because a trusted page is a deck on its author's own machine and
    the machine's own proxy is the one it should use.
    """
    options: dict[str, Any] = {"env": child_environment()}
    if policy == UNTRUSTED:
        options |= {
            "chromium_sandbox": True,
            "proxy": {"server": DEAD_PROXY, "bypass": EVERY_HOST},
            "args": list(UNTRUSTED_ARGS),
        }
    return options


_POLICIES: weakref.WeakKeyDictionary[Browser, PagePolicy] = weakref.WeakKeyDictionary()
"""The policy each open browser was launched under, which every page it opens is routed by."""


def trusts(browser: Browser) -> bool:
    """Whether a browser was launched for a trusted page, which a browser this module never launched was not."""
    return _POLICIES.get(browser) == TRUSTED


@contextmanager
def chromium(browser_path: str = "", *, policy: str, spend: bool) -> Iterator[Browser]:
    """A launched headless Chromium, as the machine and the page policy configure it, closed on exit.

    Under the trusted policy no proxy argument is passed. Request routing answers the local origin
    before the network stack reaches it, so no proxy ever sees that host, and every other request a
    recorded page makes goes the way the machine sends it, through its own proxy and its own logging.
    Under the untrusted policy the browser is sealed as `launch_options` says.

    `browser_path` is `[record] browser_path`, the executable a machine that manages its own
    Chromium names. It is empty on a machine DeckTalk fetches the browser for, whose driver looks in
    the browser directory of the run's tool cache, which is where `launch` fetches it. `policy` is
    `[record] page_policy`, which every caller that opens a project's page passes on. It has no
    default, because a default would be the policy a caller that forgot it gets, and a caller that
    forgot it is the one most likely to open a stranger's page. `spend` is whether the run may buy,
    which the caller passes from its run for the same reason: a default would be a fail-open guess.
    """
    with chromium_fetch.driver(chromium_fetch.browsers_dir()) as pw:
        browser = launch(pw, browser_path, policy=policy, spend=spend)
        _POLICIES[browser] = page_policy(policy)
        try:
            yield browser
        finally:
            browser.close()


def launch(pw: Playwright, browser_path: str = "", *, policy: str, spend: bool) -> Browser:
    """A launched Chromium, fetching the build Playwright manages when this machine has not got it.

    This is the one place a browser starts, so every command gets the browser it needs without
    anyone running an install step first, the way `ffmpeg_paths` gets ffmpeg. The fetch downloads
    Chromium alone and never its system libraries, because installing those goes through sudo and a
    build that stops for a root password is a build that hangs in a script and in CI. When Chromium
    still will not launch after it has been fetched, those libraries are what is missing, and the
    error says to run `decktalk install`, which is the one command that may ask for a password.

    A machine that names its own executable is told about that executable instead. Fetching would
    not help it: the next launch would use the same path again. An untrusted page whose Chromium is
    on disk and will not start is a machine that cannot run the sandbox, which a fetch does not
    change either, so it is refused and never started without one. An untrusted page under a run that
    may spend is refused before anything starts, which is the one check that keeps a key and a
    stranger's page out of one run.
    """
    sealed = page_policy(policy)
    if sealed == UNTRUSTED and spend:
        raise ApprovalRequired(KEY_BESIDE_PAGE, hint=KEY_BESIDE_PAGE_HINT)
    options = launch_options(sealed)
    started = time.monotonic()
    try:
        return _launched(pw.chromium.launch(executable_path=browser_path or None, **options), sealed, started)
    except PlaywrightError as exc:
        said = str(exc).splitlines()[0]
        if browser_path:
            raise ToolError(
                f"could not launch the Chromium at {browser_path} ({said}).",
                hint="[record] browser_path names it. Clear that setting to use the build DeckTalk fetches.",
            ) from exc
        if sealed == UNTRUSTED and chromium_fetch.installed_chromium(pw) is not None:
            raise ToolError(f"could not launch Chromium with its sandbox on ({said}).", hint=SANDBOX_HINT) from exc
    # A launch that failed with no executable named falls through to here, which is the fetch.
    if chromium_fetch.installed_chromium(pw) is not None:
        log.info(
            "Chromium is on this machine and did not launch, so the build is being fetched again.",
            extra={"data": {"reason": said}},
        )
    chromium_fetch.fetch_chromium(env=child_environment())
    started = time.monotonic()
    try:
        return _launched(pw.chromium.launch(**options), sealed, started)
    except PlaywrightError as exc:
        raise ToolError(
            f"Chromium was fetched and still would not launch ({str(exc).splitlines()[0]}).",
            hint=SANDBOX_HINT
            if sealed == UNTRUSTED
            else "Run `decktalk install`, which also installs the system libraries Chromium needs and is the one "
            "command that may ask for a password.",
        ) from exc


def _launched(browser: Browser, policy: PagePolicy, started: float) -> Browser:
    """Record which Chromium started, under which policy and how long it took, and hand it back."""
    seconds = time.monotonic() - started
    log.debug(
        "Chromium %s started in %.2f seconds.",
        browser.version,
        seconds,
        extra={"data": {"version": browser.version, "policy": policy, "seconds": round(seconds, 3)}},
    )
    return browser
