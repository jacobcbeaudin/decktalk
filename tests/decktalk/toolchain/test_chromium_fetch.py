"""Chromium on demand: a build fetches the browser, and only `decktalk install` may ask for sudo.

ffmpeg has always arrived by itself, and Chromium did not: every command that needed a browser died
with "Run `decktalk install`" until someone ran it. `media/browser.py` is the one place a browser is
launched, so the fetch lives there and every command has it.

The one thing the fetch must never do is ask for a root password. `playwright install chromium
--with-deps` installs Chromium's system libraries through sudo, and a build that stops on a password
prompt is a build that hangs in a script and in CI, so `--with-deps` belongs to `decktalk install`
and to nothing a build runs. That is what `test_the_fetch_a_build_runs_never_asks_for_the_system_
libraries` is for.

Nothing here reaches Playwright or a real browser. `pw` is a fake whose launch works when the
executable is on disk, and the fetch is a fake `playwright install` that puts it there, so these
tests say the same thing on a machine that has Chromium and on one that has never seen it.
"""

from __future__ import annotations

import ast
import contextlib
import re
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import ToolError
from decktalk.media import browser
from decktalk.media.environment import children_see
from decktalk.toolchain import chromium_fetch
from decktalk.toolchain.announce import announcing
from support.paths import REPO

SRC = REPO / "src" / "decktalk"


class FakeBrowser:
    version = "0.0.0.0"

    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    """`pw.chromium` as `browser.launch` uses it: an executable path, and a launch that needs it there.

    A launch works when the file it would run is on disk, which is what a fetch puts there. `broken`
    is the other way a launch fails: the browser is on disk and still will not start, which on Linux
    means its system libraries are missing and no download fixes it.
    """

    def __init__(self, executable: Path, *, broken: bool = False) -> None:
        self.executable_path = str(executable)
        self.broken = broken
        self.launches: list[str | None] = []

    def launch(self, executable_path: str | None = None, **_options: object) -> FakeBrowser:
        self.launches.append(executable_path)
        target = Path(executable_path or self.executable_path)
        if not target.is_file():
            raise PlaywrightError(f"Executable doesn't exist at {target}\nPlaywright was just installed")
        if self.broken:
            raise PlaywrightError("error while loading shared libraries: libnss3.so: cannot open shared object file")
        return FakeBrowser(str(target))


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


def fake_fetch(monkeypatch: pytest.MonkeyPatch, installs: Path | None, *, code: int = 0) -> list[list[str]]:
    """Stand in for `playwright install`, recording each command line and writing the browser it fetches.

    The real `fetch_chromium` still runs, so the command line these tests read is the one DeckTalk
    would run, and `--with-deps` cannot slip in behind a stubbed-out function.
    """
    seen: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(list(cmd))
        assert kwargs["capture_output"] is True, "the installer's output is kept, never printed"
        assert kwargs["timeout"] == chromium_fetch.FETCH_TIMEOUT_SECONDS
        if installs is not None and code == 0:
            installs.parent.mkdir(parents=True, exist_ok=True)
            installs.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, code, b"downloading", b"line 1\nERROR: host unreachable\n")

    monkeypatch.setattr(chromium_fetch.subprocess, "run", run)
    return seen


@pytest.fixture
def on_disk(tmp_path: Path) -> Path:
    """Where this machine's Chromium would be, which nothing has written yet."""
    return tmp_path / "ms-playwright" / "chromium-1243" / "chrome"


def test_a_missing_chromium_is_fetched_rather_than_refused(monkeypatch, on_disk) -> None:
    """The bug this change exists to fix: `chromium()` caught the launch failure and told the caller
    to go and run `decktalk install`, while ffmpeg had been downloading itself all along."""
    pw = FakePlaywright(FakeChromium(on_disk))
    commands = fake_fetch(monkeypatch, on_disk)
    launched = browser.launch(pw, policy=browser.TRUSTED)
    assert isinstance(launched, FakeBrowser)
    assert len(commands) == 1, f"the browser was not fetched: {commands}"
    assert pw.chromium.launches == [None, None], "the launch was not tried again after the fetch"


def test_the_fetch_a_build_runs_never_asks_for_the_system_libraries(monkeypatch, on_disk) -> None:
    """The constraint that decides the whole design. Playwright installs Chromium's system libraries
    by shelling out to sudo, so `--with-deps` on this path would stop an unattended build at a
    password prompt. The command line is read rather than the function stubbed, so the flag cannot
    reappear anywhere between here and the subprocess."""
    pw = FakePlaywright(FakeChromium(on_disk))
    commands = fake_fetch(monkeypatch, on_disk)
    browser.launch(pw, policy=browser.TRUSTED)
    assert commands, "nothing was fetched, so this asserts nothing"
    for cmd in commands:
        assert chromium_fetch.WITH_DEPS not in cmd, f"a build asked for the libraries that need sudo: {cmd}"
        assert cmd[1:] == list(chromium_fetch.INSTALL_ARGS), cmd


def test_a_launch_that_still_fails_after_the_fetch_names_the_install_command(monkeypatch, on_disk) -> None:
    """Chromium is there and will not start, which is the missing system libraries. That is the one
    thing a build cannot fix for itself, so this is where `decktalk install` is named."""
    pw = FakePlaywright(FakeChromium(on_disk, broken=True))
    commands = fake_fetch(monkeypatch, on_disk)
    with pytest.raises(ToolError) as caught:
        browser.launch(pw, policy=browser.TRUSTED)
    assert commands, "it refused without even trying to fetch the browser"
    said = f"{caught.value} {caught.value.hint}"
    assert "decktalk install" in said, said
    assert "libnss3" in said, f"the reason Chromium gave is not in the message: {said}"


def test_the_download_is_announced_before_it_starts(monkeypatch, on_disk) -> None:
    """A few hundred megabytes arriving in silence reads as a hung build, so the line goes out before
    the download rather than with its result, and it goes out as a `fetch` line and not as a log."""
    pw = FakePlaywright(FakeChromium(on_disk))
    heard: list[tuple[str, int, int | None]] = []

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert heard, "the download started with nothing said about it"
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(chromium_fetch.subprocess, "run", run)
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        browser.launch(pw, policy=browser.TRUSTED)
    # Playwright reports its own progress to its own output, so the start is all this download knows.
    assert heard == [(chromium_fetch.TOOL, 0, None)], heard


def test_a_browser_that_is_already_there_is_launched_without_a_fetch(monkeypatch, on_disk) -> None:
    """The common case is every build after the first, and it must not shell out to anything."""
    on_disk.parent.mkdir(parents=True)
    on_disk.write_text("#!/bin/sh\n", encoding="utf-8")
    pw = FakePlaywright(FakeChromium(on_disk))
    commands = fake_fetch(monkeypatch, on_disk)
    browser.launch(pw, policy=browser.TRUSTED)
    assert commands == [], f"a machine with Chromium fetched it again: {commands}"
    assert pw.chromium.launches == [None]


def test_a_machine_that_names_its_own_chromium_is_never_sent_to_download_one(monkeypatch, tmp_path, on_disk) -> None:
    """`[record] browser_path` is the managed machine's own executable. Fetching Playwright's build
    would download it for nothing, because the next launch would use that same path again."""
    named = tmp_path / "opt" / "chromium"
    pw = FakePlaywright(FakeChromium(on_disk))
    commands = fake_fetch(monkeypatch, on_disk)
    with pytest.raises(ToolError) as caught:
        browser.launch(pw, str(named), policy=browser.TRUSTED)
    assert commands == [], f"a named executable triggered a download: {commands}"
    said = f"{caught.value} {caught.value.hint}"
    assert str(named) in said, said
    assert "browser_path" in said, said


def test_a_fetch_that_fails_is_a_tool_error_rather_than_a_return_code(monkeypatch, on_disk) -> None:
    pw = FakePlaywright(FakeChromium(on_disk))
    fake_fetch(monkeypatch, on_disk, code=1)
    with pytest.raises(ToolError, match=re.escape("playwright install failed: line 1 | ERROR: host unreachable")):
        browser.launch(pw, policy=browser.TRUSTED)


def test_a_fetch_that_never_finishes_is_stopped_and_refused(monkeypatch) -> None:
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, float(kwargs["timeout"]))  # type: ignore[arg-type]

    monkeypatch.setattr(chromium_fetch.subprocess, "run", run)
    with pytest.raises(ToolError, match="longer than"):
        chromium_fetch.fetch_chromium(env={})


def test_the_context_manager_fetches_too_and_closes_what_it_opened(monkeypatch, on_disk) -> None:
    """`chromium()` is what every stage calls, so the wiring from it to the fetch is worth one test.
    Playwright itself is replaced here, so this never reaches a real browser either."""
    pw = FakePlaywright(FakeChromium(on_disk))
    commands = fake_fetch(monkeypatch, on_disk)
    monkeypatch.setattr(browser, "sync_playwright", lambda: contextlib.nullcontext(pw))
    with browser.chromium(policy=browser.TRUSTED) as opened:
        assert isinstance(opened, FakeBrowser)
    assert opened.closed, "the browser was left running"
    assert len(commands) == 1, commands


def test_the_installer_is_handed_the_scrubbed_environment_and_never_the_hosts_credentials(monkeypatch, on_disk) -> None:
    """The installer ran with the process's whole environment, so a host's own credentials reached it
    and every script it runs. It is now handed the environment every other child is handed."""
    handed: list[dict[str, str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        handed.append(dict(kwargs["env"]))  # type: ignore[call-overload]
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(chromium_fetch.subprocess, "run", run)
    host = {"PATH": "/usr/bin", "HOST_DB_PASSWORD": "pw_installer_canary_31f0", "AWS_SECRET_ACCESS_KEY": "aws-canary"}
    with children_see(host):
        browser.launch(FakePlaywright(FakeChromium(on_disk)), policy=browser.TRUSTED)
    [env] = handed
    assert env["PATH"] == "/usr/bin"
    assert "HOST_DB_PASSWORD" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert "pw_installer_canary_31f0" not in env.values()


def test_every_installer_run_leaves_its_command_exit_and_output_on_the_record(monkeypatch, on_disk, caplog) -> None:
    fake_fetch(monkeypatch, on_disk, code=1)
    with caplog.at_level("DEBUG", logger="decktalk"), pytest.raises(ToolError):
        chromium_fetch.fetch_chromium(env={})
    [record] = [record for record in caplog.records if record.name == "decktalk.toolchain.chromium_fetch"]
    assert record.levelname == "WARNING"
    data = record.data  # type: ignore[attr-defined]
    assert data["exit"] == 1 and "playwright install chromium" in data["argv"]
    assert data["output_tail"] == "line 1 | ERROR: host unreachable"


def test_an_installer_that_never_finishes_says_it_was_stopped(monkeypatch, caplog) -> None:
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, float(kwargs["timeout"]))  # type: ignore[arg-type]

    monkeypatch.setattr(chromium_fetch.subprocess, "run", run)
    with caplog.at_level("DEBUG", logger="decktalk"), pytest.raises(ToolError):
        chromium_fetch.fetch_chromium(env={})
    assert [record.data["reason"] for record in caplog.records if hasattr(record, "data")] == ["timeout"]


# ---- the one command that may ask for a password ------------------------------------------------


def test_only_a_caller_that_asks_for_them_reaches_the_system_libraries(monkeypatch, on_disk) -> None:
    """`decktalk install` is a command a person typed and is waiting on, so it alone may prompt.

    It is also the only fix for a Chromium that cannot load its libraries, which is why the flag stays
    on this function and why no build passes it.
    """
    commands = fake_fetch(monkeypatch, on_disk)
    chromium_fetch.fetch_chromium(env={}, with_deps=True)
    chromium_fetch.fetch_chromium(env={})
    assert chromium_fetch.WITH_DEPS in commands[0], commands[0]
    assert commands[1][1:] == list(chromium_fetch.INSTALL_ARGS), commands[1]


def test_every_command_that_needs_a_browser_goes_through_the_one_function() -> None:
    """The fetch is in `media/browser.py` because that is the only module that starts a browser, and
    the claim that every command gets it therefore rests on nothing else starting one. `doctor` is
    the exception by design: it reports what a machine has and fetches nothing, so the machine
    launches the browser it already holds and never asks for one."""
    starts = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            call = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            if call and node.func.attr == "launch":  # type: ignore[union-attr]
                starts.append(path.relative_to(SRC).as_posix())
    assert sorted(set(starts)) == ["machine.py", "media/browser.py"], starts
