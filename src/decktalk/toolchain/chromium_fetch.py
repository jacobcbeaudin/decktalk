"""The headless Chromium Playwright manages: where it lives, whether this machine has it, and fetching it.

Playwright downloads one pinned Chromium revision per version of the playwright package. DeckTalk
keeps it in `ms-playwright` inside the machine's tool cache, beside the ffmpeg build in
`ffmpeg_fetch.py`, so `[tools] cache_dir` moves both and a job caches one directory. Playwright reads
where its browsers live from `PLAYWRIGHT_BROWSERS_PATH`, so the installer and the driver are both
handed that directory under that name, and a value the host set for it is not used. The fetch runs
the first time a command needs a browser, so a build works on a machine where nothing was installed
by hand, and `decktalk install` runs the same fetch up front.

The two callers differ in one flag. `--with-deps` asks Playwright to install Chromium's system
libraries, which it does by shelling out to apt-get through sudo, so it can ask for a root
password. Only `decktalk install` passes it, because that is a command a person typed and is
waiting on. A build never does: builds run unattended in scripts and in CI, where a password
prompt is a hang rather than a question. When Chromium then will not launch because those
libraries are genuinely missing, the error says to run `decktalk install`.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Generator, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from playwright import sync_api
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright

from ..errors import ToolError
from . import command_line, tail, traced
from .announce import announce
from .cache import cache_dir

log = logging.getLogger(__name__)

TOOL = "chromium"
"""What a `fetch` line calls this download, which is the name `doctor` and `install` print too."""

# What both callers run. Playwright resolves the revision from its own version, so nothing is
# pinned here: the pin is the playwright dependency in pyproject.toml.
INSTALL_ARGS = ("-m", "playwright", "install", "chromium")
# The flag that reaches sudo, named once so it is clear which call passes it and which does not.
WITH_DEPS = "--with-deps"

BROWSERS_VARIABLE = "PLAYWRIGHT_BROWSERS_PATH"
"""The variable Playwright's installer and its driver read for the directory its browsers live in."""

BROWSERS_DIR = "ms-playwright"
"""The folder inside the tool cache that Playwright's browsers live in, named as Playwright names its own."""

_STARTING = threading.Lock()
"""Held while a driver starts, because the driver copies this process's environment as it starts."""


def browsers_in(cache: Path) -> Path:
    """The directory Playwright keeps its browsers in for a machine whose tool cache is `cache`."""
    return cache / BROWSERS_DIR


def browsers_dir() -> Path:
    """The browser directory of the machine this run belongs to, or a `TOOL` refusal when no machine bound one."""
    return browsers_in(cache_dir())


@contextmanager
def driver(browsers: Path) -> Generator[Playwright]:
    """A started Playwright driver that finds and launches the browsers in `browsers`, stopped on exit.

    Playwright starts its driver with a copy of this process's environment and takes no other, so
    `PLAYWRIGHT_BROWSERS_PATH` names `browsers` in the process for as long as the driver takes to
    start, under a lock so two starts cannot see each other's value, and is put back as it was once
    the driver has its copy. The driver is looked up on `playwright.sync_api` when it starts, which
    is the one seam a test replaces it through.
    """
    with ExitStack() as stack:
        with _STARTING:
            held = os.environ.get(BROWSERS_VARIABLE)
            os.environ[BROWSERS_VARIABLE] = str(browsers)
            try:
                started = stack.enter_context(sync_api.sync_playwright())
            finally:
                if held is None:
                    del os.environ[BROWSERS_VARIABLE]
                else:
                    os.environ[BROWSERS_VARIABLE] = held
        yield started


def installed_chromium(pw: Playwright) -> str | None:
    """The Chromium executable Playwright has on disk for this machine, or None when it has none.

    `pw` is a started `sync_playwright`. The path it names is the revision this playwright package
    expects, so upgrading playwright makes this None again and the next command fetches the build
    the new version wants.
    """
    try:
        path = pw.chromium.executable_path
    except PlaywrightError:  # pragma: no cover - a driver that cannot answer is a machine without it
        # silent: a driver that cannot answer is a machine without the browser.
        return None
    return str(path) if Path(path).is_file() else None


FETCH_TIMEOUT_SECONDS = 1800
"""Calibration: half an hour, far longer than 330 MB takes on a usable connection, so only a stuck install hits it."""

MIB = 1024 * 1024
"""Truth: the mebibyte Playwright's installer states each download's size in."""

COLOURS = re.compile(r"\x1b\[[0-9;]*m")
"""The colour codes Playwright's installer prints around a URL even in a pipe, dropped before a line is read."""

DOWNLOADING = re.compile(r"^Downloading .*\(playwright (?P<name>[\w-]+) v\w+\)")
"""The line Playwright's installer starts each part of the download with, which names the part as Playwright does."""

PROGRESS = re.compile(r"\|\s*(?P<percent>\d+)% of (?P<mib>\d+(?:\.\d+)?) MiB$")
"""The line Playwright's installer writes at each tenth of a part, which carries the size its host stated."""


def part_name(name: str) -> str:
    """What a `fetch` line calls one part of Playwright's download.

    `chromium install` fetches the browser, its headless shell and Playwright's own ffmpeg. A part
    whose name does not already say chromium is named with chromium in front, so Playwright's
    ffmpeg is never read as the encoder DeckTalk fetches under the name ffmpeg.
    """
    return name if name.startswith(TOOL) else f"{TOOL} {name}"


def follow(said: Iterable[bytes], kept: list[bytes]) -> None:
    """Read the installer's output as it arrives, keeping every line and announcing each part's progress.

    Playwright's installer tells this process nothing but what it prints, and in a pipe it prints
    each part's size, as its host states it in MiB to a tenth, and how far that part has got at each
    tenth. Those lines are announced as they arrive, and the last of a part counts all of its size,
    which is what closes it. A line in no form this reads is kept and announced as nothing, so an
    installer that changed its wording leaves a download announced without a size, never with a
    made-up one. The installer owns its download, so how often a line arrives is its choice too: on
    a slow link a tenth of a part can take longer than the ten seconds the ffmpeg fetch keeps to.
    """
    part = TOOL
    for raw in said:
        kept.append(raw)
        line = COLOURS.sub("", raw.decode(errors="replace")).strip()
        if started := DOWNLOADING.match(line):
            part = part_name(started["name"])
            announce(part, 0, None)
        elif counted := PROGRESS.search(line):
            total = round(float(counted["mib"]) * MIB)
            announce(part, total * int(counted["percent"]) // 100, total)


def fetch_chromium(*, env: Mapping[str, str], with_deps: bool = False) -> None:
    """Run `playwright install chromium` for this machine, raising a ToolError when it fails.

    `env` is the environment the installer runs with, which the caller builds from the machine's
    scrubbed child environment, so a credential the host holds in its own environment never reaches
    the installer or any script it runs. `PLAYWRIGHT_BROWSERS_PATH` is added to it, naming the
    browser directory of the machine this run belongs to, so the browser lands where the driver
    looks for it.

    `with_deps` adds Chromium's system libraries and may ask for a root password, so only
    `decktalk install` passes it. The password prompt goes to the terminal itself, so it is seen
    although the installer's own output is kept. That output is kept rather than printed, because
    nothing in the library prints, and its last lines are the reason a failed fetch gives.

    The download is announced before it starts, and each part of it is then announced with the size
    and the progress Playwright's installer prints, as `follow` reads them. A fetch that runs past
    `FETCH_TIMEOUT_SECONDS` is stopped and refused, so a stalled mirror cannot hold a build forever.
    The call is traced like every tool call, with its command, exit code, time and last lines.
    """
    installer = {**env, BROWSERS_VARIABLE: str(browsers_dir())}
    announce(TOOL, 0, None)
    cmd = [sys.executable, *INSTALL_ARGS, *([WITH_DEPS] if with_deps else [])]
    started = time.monotonic()
    # The installer's output is followed on a thread, in a copy of this context, because a thread
    # starts in a fresh context and the listener a run set travels in the context.
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=installer)
    kept: list[bytes] = []
    reader = threading.Thread(target=contextvars.copy_context().run, args=(follow, process.stdout, kept), daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=FETCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        reader.join()
        log.warning(
            "playwright install was stopped after %d seconds (timeout).",
            FETCH_TIMEOUT_SECONDS,
            extra={"data": {"argv": command_line(cmd), "reason": "timeout", "limit": FETCH_TIMEOUT_SECONDS}},
        )
        raise ToolError(
            f"playwright install ran for longer than {FETCH_TIMEOUT_SECONDS} seconds, so it was stopped.",
            hint="Check the network, then run `decktalk install`.",
        ) from exc
    reader.join()
    said = b"".join(kept)
    traced(log, "playwright install", cmd, code=code, seconds=time.monotonic() - started, said=said)
    if code != 0:
        raise ToolError(
            f"playwright install failed: {tail(said)}",
            hint="Check the network, then run `decktalk install`.",
        )
