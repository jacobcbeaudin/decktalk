"""site/install.sh, the one-line installer, held to the promises its own comment makes.

`curl … | sh` runs whatever bytes arrive, so the script is written with everything inside `main()`
and `main "$@"` on the last line: a download cut off part way through then runs nothing at all
rather than half an install. That is a property worth testing rather than trusting, and it is the
reason for `test_a_truncated_download_installs_nothing`.

The container matrix (Debian, Alpine's busybox sh, Fedora) runs in CI, where a machine without uv,
without Python and with an empty PATH can be had cheaply. These are the parts that need no
container: the script parses under a POSIX shell, it never asks for root, and the ways it can be
called all do what they say.
"""

from __future__ import annotations

import contextlib
import os
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from support.paths import REPO

# install.sh is a POSIX shell script, so every test here needs /bin/sh, and the terminal ones need
# a pty. Neither exists on Windows. Skipping at module scope rather than importing `pty` at the top:
# `pty` imports `termios`, which Windows does not have, so the import failed during collection and
# took the whole run down with it on a file whose tests were all deselected on that platform.
pytestmark = pytest.mark.skipif(os.name != "posix", reason="install.sh needs a POSIX shell and a pty")

SCRIPT = REPO / "site" / "install.sh"
SHELLS = [sh for sh in ("/bin/sh", "/bin/dash", "/bin/busybox") if Path(sh).exists()]


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def fake_path(
    tmp_path: Path,
    marker: Path,
    *,
    fail: tuple[str, ...] = (),
    slow: tuple[str, ...] = (),
    without: tuple[str, ...] = (),
) -> dict[str, str]:
    """A PATH holding stubs that only record that they were called.

    `decktalk` is one of them on purpose. Without it the script falls through to
    `$HOME/.local/bin/decktalk`, so on a machine that has DeckTalk installed the tests would pass by
    reaching the real one, and on a machine that does not they would fail for a reason that has
    nothing to do with the script.

    `fail` names stubs that exit 1, for the paths where something goes wrong. `slow` names stubs
    that take five seconds. `without` names tools that must not be found at all.

    PATH is /usr/bin:/bin and nothing else, which is what makes `without` mean anything: uv lives in
    ~/.local/bin or Homebrew, so a PATH that inherited the author's would still find the real one
    after the stub was removed, and a test for "no uv on this machine" would silently be a test of
    the machine that has uv.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("uv", "curl", "sudo", "wget", "decktalk"):
        lines = [
            "#!/bin/sh",
            f'echo "{name} $*" >> "{marker}"',
            f'[ "$1" = "--version" ] && {{ echo "{name} 0.0.0"; exit 0; }}',
        ]
        if name in slow:
            lines.append("sleep 5")
        if name in fail:
            lines.append(f'echo "{name}: deliberate failure" >&2')
            lines.append("exit 1")
        lines.append("exit 0")
        if name in without:
            continue
        stub = bin_dir / name
        stub.write_text("\n".join(lines) + "\n")
        stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    # HOME too, because find_decktalk falls back to $HOME/.local/bin/decktalk. Left pointing at the
    # real home, a test would quietly exercise whatever DeckTalk the author happens to have.
    env["HOME"] = str(tmp_path)
    return env


def run(
    args: list[str], env: dict[str, str] | None = None, script: str | None = None
) -> subprocess.CompletedProcess[str]:
    if script is None:
        return subprocess.run(["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
    return subprocess.run(
        ["/bin/sh", "-s", "--", *args], input=script, capture_output=True, text=True, env=env, timeout=60
    )


@pytest.mark.parametrize("shell", SHELLS)
def test_it_parses_under_every_posix_shell_here(shell: str) -> None:
    """A bashism would pass on macOS, whose /bin/sh is bash, and fail on a real POSIX shell.

    busybox is the strictest of them and is a multi-call binary, so it takes the shell as its first
    argument: `busybox -n file` asks for an applet called `-n` and exits 127.
    """
    argv = [shell, "sh", "-n", str(SCRIPT)] if shell.endswith("busybox") else [shell, "-n", str(SCRIPT)]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def test_it_never_asks_for_root(tmp_path: Path, source: str) -> None:
    """The one-liner must not need root, and a grep for "sudo" cannot tell an invocation from the
    sentence that tells you `decktalk install` will ask for one. So run it with a sudo on PATH that
    records being called, and require that it never is.

    `decktalk install` is checked in the same breath, because it is the one command this script
    could plausibly run that reaches sudo of its own accord: on Linux it runs
    `playwright install chromium --with-deps`, and Playwright uses sudo for the system libraries.
    The installer briefly did run it, behind a prompt, and this is the assertion that says it does
    not any more rather than that it asks nicely."""
    marker = tmp_path / "called"
    run([], env=fake_path(tmp_path, marker), script=source)
    assert marker.exists(), "the run did nothing, so it proves nothing"
    called = marker.read_text()
    assert "sudo" not in called, called
    assert "decktalk install" not in called, f"the installer ran the step that can reach sudo: {called}"


def test_everything_runs_from_the_last_line(source: str) -> None:
    """The guard that makes a truncated download harmless: nothing executes before `main "$@"`."""
    lines = [line for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert lines[-1] == 'main "$@"', lines[-1]


@pytest.mark.parametrize("fraction", [0.25, 0.5, 0.75, 0.95])
def test_a_truncated_download_installs_nothing(tmp_path: Path, source: str, fraction: float) -> None:
    """A connection that drops mid-download leaves `sh` running whatever arrived. It must do nothing."""
    marker = tmp_path / f"called-{fraction}"
    env = fake_path(tmp_path, marker)
    cut = source[: int(len(source) * fraction)]
    done = run([], env=env, script=cut)
    assert not marker.exists(), f"a {fraction:.0%} download ran: {marker.read_text()}"
    assert "Installing" not in done.stdout, done.stdout


def test_the_whole_script_does_install(tmp_path: Path, source: str) -> None:
    """The counterpart: the test above would pass on a script that never installs anything."""
    marker = tmp_path / "called"
    done = run([], env=fake_path(tmp_path, marker), script=source)
    assert done.returncode == 0, done.stderr
    assert marker.exists(), done.stdout
    assert "uv tool install decktalk" in marker.read_text()


def test_a_pinned_version_is_the_version_it_installs(tmp_path: Path, source: str) -> None:
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker) | {"DECKTALK_VERSION": "0.4.1"}
    run([], env=env, script=source)
    assert "uv tool install decktalk==0.4.1" in marker.read_text()


def test_a_dry_run_changes_nothing(tmp_path: Path, source: str) -> None:
    marker = tmp_path / "called"
    done = run(["--dry-run"], env=fake_path(tmp_path, marker), script=source)
    assert done.returncode == 0, done.stderr
    assert "nothing was installed" in done.stdout
    assert "uv tool install" not in marker.read_text() if marker.exists() else True


def test_help_explains_itself_and_exits_zero(tmp_path: Path, source: str) -> None:
    done = run(["--help"], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert done.returncode == 0
    assert "curl -LsSf https://decktalk.ai/install.sh | sh" in done.stdout


def test_an_unknown_option_is_refused_rather_than_ignored(tmp_path: Path, source: str) -> None:
    done = run(["--wat"], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "unknown option" in done.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shellcheck_is_clean() -> None:
    done = subprocess.run(["shellcheck", "-s", "sh", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout


# ---- what the script does when something goes wrong ---------------------------------------------


def test_a_failing_step_stops_the_install(tmp_path: Path, source: str) -> None:
    """The regression this file exists to prevent from coming back.

    `step` used to read `$?` after an `if` whose condition had failed. An `if` with no `else` whose
    condition is false exits 0, so the status read there was the if statement's own, not the
    command's, and every failed step was recorded as a success. A 404 on uv's installer printed its
    error, then reported a tick for installing uv from the empty file it had just failed to
    download, then died three lines later complaining about PATH.
    """
    marker = tmp_path / "called"
    # No uv anywhere, so the script must download and run uv's installer, and curl fails when it
    # tries. Both halves matter: without `without=("uv",)` the script finds a uv, returns early and
    # never reaches a step that can fail, and the test passes while testing nothing.
    env = fake_path(tmp_path, marker, fail=("curl", "wget"), without=("uv",))
    done = run([], env=env, script=source)
    assert "Downloading uv" in done.stdout, f"never reached the failing step:\n{done.stdout}"
    assert done.returncode != 0, f"a failed download exited 0:\n{done.stdout}\n{done.stderr}"
    # The load-bearing assertion, and the one that tells the bug apart from its symptom. With the
    # bug the script did still exit non-zero, three steps later and for the wrong reason, so
    # asserting only on the exit code passes on the broken script. What it must not do is begin
    # the next step, running uv's installer over the empty file the download just failed to write.
    assert "Installing uv" not in done.stdout, f"it started the next step anyway:\n{done.stdout}"
    called = marker.read_text() if marker.exists() else ""
    assert "uv tool install" not in called, f"it carried on after the failure: {called}"


def test_a_failure_shows_the_output_it_held_back(tmp_path: Path, source: str) -> None:
    """Held-back output is only worth holding back if it arrives when it is needed."""
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker, fail=("curl", "wget"), without=("uv",))
    done = run([], env=env, script=source)
    assert "Downloading uv" in done.stdout, f"never reached the failing step:\n{done.stdout}"
    assert "deliberate failure" in done.stdout + done.stderr, done.stdout + done.stderr


def test_the_log_is_kept_and_named_when_something_fails(tmp_path: Path, source: str) -> None:
    log = tmp_path / "install.log"
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker, fail=("curl", "wget"), without=("uv",)) | {"DECKTALK_INSTALL_LOG": str(log)}
    done = run([], env=env, script=source)
    assert done.returncode != 0, f"nothing failed, so there is no failure to keep a log for:\n{done.stdout}"
    assert log.exists(), "the log was removed on the one run where it was worth keeping"
    assert str(log) in done.stderr, f"the path was never printed: {done.stderr}"
    assert "exited 1" in log.read_text(), log.read_text()


def test_the_log_is_removed_when_nothing_fails(tmp_path: Path, source: str) -> None:
    log = tmp_path / "install.log"
    env = fake_path(tmp_path, marker := tmp_path / "called") | {"DECKTALK_INSTALL_LOG": str(log)}
    done = run([], env=env, script=source)
    assert done.returncode == 0, done.stderr
    assert marker.exists()
    assert not log.exists(), "a clean run left a log file behind"


def test_keep_log_keeps_it(tmp_path: Path, source: str) -> None:
    log = tmp_path / "install.log"
    env = fake_path(tmp_path, tmp_path / "called") | {"DECKTALK_INSTALL_LOG": str(log)}
    done = run(["--keep-log"], env=env, script=source)
    assert done.returncode == 0, done.stderr
    assert log.exists()
    assert "uv tool install decktalk" in log.read_text()


# ---- the one step that can reach sudo -----------------------------------------------------------


def test_it_reports_the_version_that_is_actually_on_disk(tmp_path: Path, source: str) -> None:
    """ "Installed" was a claim about the command that had just run, not about the one the reader is
    about to type. It is now read back from the binary."""
    done = run([], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert "decktalk 0.0.0 is installed" in done.stdout, done.stdout


# ---- presentation degrades rather than breaking -------------------------------------------------


def test_nothing_writes_escape_codes_when_stdout_is_not_a_terminal(tmp_path: Path, source: str) -> None:
    """A CI log full of colour codes is a log nobody reads. `[ -t 1 ]` is the whole guard, and this
    is the assertion that it is actually consulted everywhere rather than in most places."""
    done = run([], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert "\033[" not in done.stdout, repr(done.stdout)
    assert "\033[" not in done.stderr, repr(done.stderr)


def test_the_plain_path_still_names_every_step(tmp_path: Path, source: str) -> None:
    """Without a spinner the labels are all a reader gets, so they must still be printed."""
    done = run([], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert "Installing decktalk" in done.stdout, done.stdout


# ---- what needs a real terminal -----------------------------------------------------------------
#
# `[ -t 1 ]` is what the script branches on for colour and the spinner, and a pipe cannot
# reproduce it. A pty is the only way to test that branch: pty.fork
# makes the pty the child's controlling terminal, which is what /dev/tty then opens. Popen with a
# pty on stdout would leave the child's controlling terminal pointing at pytest's own.


def run_pty(
    args: list[str],
    env: dict[str, str],
    interrupt_after: float | None = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Run install.sh under a pty. Returns (exit status, everything it wrote)."""
    import pty  # noqa: PLC0415 - termios is not on Windows, which is what pytestmark refuses this file on

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs or dies
        try:
            os.execvpe("/bin/sh", ["sh", str(SCRIPT), *args], env)
        finally:
            os._exit(127)
    out = b""
    reaped = False
    status = 0
    started = time.monotonic()
    interrupted = False
    try:
        while True:
            if time.monotonic() - started > timeout:
                raise AssertionError(f"install.sh did not finish in {timeout}s:\n{out.decode(errors='replace')}")
            if interrupt_after is not None and not interrupted and time.monotonic() - started >= interrupt_after:
                # Ctrl-C reaches the whole foreground process group, not just the shell.
                os.killpg(os.getpgid(pid), signal.SIGINT)
                interrupted = True
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    # The last process holding the slave closed it: EIO here means the run is over,
                    # not that it went wrong, so the status still has to be collected below.
                    chunk = b""
                if not chunk:
                    break
                out += chunk
                continue
            done, got = os.waitpid(pid, os.WNOHANG)
            if done:
                status, reaped = got, True
                break
        if not reaped:
            _, status = os.waitpid(pid, 0)
            reaped = True
        return os.waitstatus_to_exitcode(status), out.decode(errors="replace")
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        if not reaped:
            with contextlib.suppress(OSError, ChildProcessError):
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)


def test_a_terminal_gets_the_spinner_and_a_tick(tmp_path: Path, source: str) -> None:
    del source
    code, out = run_pty([], fake_path(tmp_path, tmp_path / "called"))
    assert code == 0, out
    assert "\033[" in out, "a terminal got no colour at all"
    assert "✓" in out, out


def test_no_color_is_honoured_on_a_terminal(tmp_path: Path, source: str) -> None:
    """NO_COLOR is set by people who mean it, and a terminal is exactly where it has to be obeyed."""
    del source
    env = fake_path(tmp_path, tmp_path / "called") | {"NO_COLOR": "1"}
    code, out = run_pty([], env)
    assert code == 0, out
    assert "\033[" not in out, repr(out)


def test_an_interrupt_puts_the_cursor_back_and_keeps_the_log(tmp_path: Path, source: str) -> None:
    """The spinner hides the cursor. Before there was a trap, a Ctrl-C mid-spinner left a terminal
    with no cursor in it until the next `reset`, and threw away the log of what had happened."""
    del source
    log = tmp_path / "install.log"
    env = fake_path(tmp_path, tmp_path / "called", slow=("uv",)) | {"DECKTALK_INSTALL_LOG": str(log)}
    code, out = run_pty([], env, interrupt_after=1.5, timeout=30)
    assert code == 130, f"an interrupt should exit 130, got {code}:\n{out}"
    assert "\033[?25h" in out, "the cursor was left hidden"
    assert log.exists(), "the log of an interrupted run was thrown away"
