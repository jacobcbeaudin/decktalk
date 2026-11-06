"""install.sh, the one-line installer, held to the promises its own comment makes.

`curl … | sh` runs whatever bytes arrive, so the script is written with everything inside `main()`
and `main "$@"` on the last line: a download cut off part way through then runs nothing at all
rather than half an install. That is a property worth testing rather than trusting, and it is the
reason for `test_a_truncated_download_installs_nothing`.

The container matrix (Debian, Ubuntu, Fedora, Alpine's busybox sh, Pixi's own image) runs in CI,
where a machine without Pixi, without Python and with an empty PATH can be had cheaply. These are the
parts that need no container: the script parses under a POSIX shell, it never asks for root, it
checks the Pixi it downloads, and the ways it can be called all do what they say.
"""

from __future__ import annotations

import hashlib
import io
import os
import signal
import subprocess
import tarfile
import time
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.installer import fake_path
from support.paths import REPO

# install.sh is a POSIX shell script, so every test here needs /bin/sh, and the terminal ones need
# a pty. Neither exists on Windows. Skipping at module scope rather than importing `pty` at the top:
# `pty` imports `termios`, which Windows does not have, so the import failed during collection and
# took the whole run down with it on a file whose tests were all deselected on that platform.
pytestmark = pytest.mark.skipif(os.name != "posix", reason="install.sh needs a POSIX shell and a pty")

SCRIPT = REPO / "install.sh"

INSTALLS = "pixi global install -c https://prefix.dev/jacobcbeaudin/jcbcodes -c conda-forge decktalk"
"""What a stub `pixi` records when the script installs DeckTalk from its channel, with conda-forge beside it."""

POLL_SECONDS = 0.2
"""How long one wait on the pty lasts before the run is asked again whether it is ready to interrupt."""

Ready = Callable[[], bool]
"""What a terminal test asks before it interrupts the run, which is whether the moment it tests has come."""
SHELLS = [sh for sh in ("/bin/sh", "/bin/dash", "/bin/busybox") if Path(sh).exists()]


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def run(args: list[str], env: dict[str, str], script: str) -> subprocess.CompletedProcess[str]:
    """install.sh as `curl | sh` runs it, read from stdin, so a test may hand it a cut or changed copy."""
    return subprocess.run(
        ["/bin/sh", "-s", "--", *args], input=script, capture_output=True, text=True, env=env, timeout=60, check=False
    )


@dataclass(frozen=True)
class Install:
    """One finished run of the installer, and the files it left for a test to read."""

    done: subprocess.CompletedProcess[str]
    marker: Path
    log: Path


def install(root: Path, source: str, *, fail: tuple[str, ...] = (), without: tuple[str, ...] = ()) -> Install:
    """Run the whole script once with its log at a known path, so several tests can read one run."""
    marker, log = root / "called", root / "install.log"
    env = fake_path(root, marker, fail=fail, without=without) | {"DECKTALK_INSTALL_LOG": str(log)}
    return Install(run([], env=env, script=source), marker, log)


# A launch of the script takes over a second, and nine tests read the same two runs, so each run
# happens once per module and every test keeps its own assertions against it.


@pytest.fixture(scope="module")
def clean_run(tmp_path_factory: pytest.TempPathFactory, source: str) -> Install:
    """The default install, with every stub on PATH and every step succeeding."""
    return install(tmp_path_factory.mktemp("clean"), source)


@pytest.fixture(scope="module")
def failed_run(tmp_path_factory: pytest.TempPathFactory, source: str) -> Install:
    """An install on a machine with no Pixi, where the download of Pixi fails.

    Both halves matter. Without `without=("pixi",)` the script finds a Pixi, returns early and never
    reaches a step that can fail, and a test of the failure passes while testing nothing.
    """
    return install(tmp_path_factory.mktemp("no-pixi"), source, fail=("curl", "wget"), without=("pixi",))


@pytest.mark.parametrize("shell", SHELLS)
def test_it_parses_under_every_posix_shell_here(shell: str) -> None:
    """A bashism would pass on macOS, whose /bin/sh is bash, and fail on a real POSIX shell.

    busybox is the strictest of them and is a multi-call binary, so it takes the shell as its first
    argument: `busybox -n file` asks for an applet called `-n` and exits 127.
    """
    argv = [shell, "sh", "-n", str(SCRIPT)] if shell.endswith("busybox") else [shell, "-n", str(SCRIPT)]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr


def test_it_never_asks_for_root(clean_run: Install) -> None:
    """The one-liner must not need root, and a grep for "sudo" cannot tell an invocation from the
    sentence that tells you `decktalk install` will ask for one. So run it with a sudo on PATH that
    records being called, and require that it never is.

    `decktalk install` is checked in the same breath, because it is the one command this script
    could plausibly run that reaches sudo of its own accord: on Linux it runs
    `playwright install chromium --with-deps`, and Playwright uses sudo for the system libraries.
    The installer briefly did run it, behind a prompt, and this is the assertion that says it does
    not any more rather than that it asks nicely."""
    assert clean_run.marker.exists(), "the run did nothing, so it proves nothing"
    called = clean_run.marker.read_text()
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


def test_the_whole_script_does_install(clean_run: Install) -> None:
    """The counterpart: the test above would pass on a script that never installs anything."""
    done = clean_run.done
    assert done.returncode == 0, done.stderr
    assert clean_run.marker.exists(), done.stdout
    assert INSTALLS in clean_run.marker.read_text()


def test_a_pinned_version_is_the_version_it_installs(tmp_path: Path, source: str) -> None:
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker) | {"DECKTALK_VERSION": "0.4.1"}
    run([], env=env, script=source)
    assert f"{INSTALLS}==0.4.1" in marker.read_text()


def test_a_dry_run_changes_nothing(tmp_path: Path, source: str) -> None:
    marker = tmp_path / "called"
    done = run(["--dry-run"], env=fake_path(tmp_path, marker), script=source)
    assert done.returncode == 0, done.stderr
    assert "nothing was installed" in done.stdout
    assert "pixi global" not in marker.read_text() if marker.exists() else True


def test_help_explains_itself_and_exits_zero(tmp_path: Path, source: str) -> None:
    done = run(["--help"], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert done.returncode == 0
    assert "curl -LsSf https://decktalk.ai/install.sh | sh" in done.stdout


def test_an_unknown_option_is_refused_rather_than_ignored(tmp_path: Path, source: str) -> None:
    done = run(["--wat"], env=fake_path(tmp_path, tmp_path / "called"), script=source)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "unknown option" in done.stderr


# ---- what the script does when something goes wrong ---------------------------------------------


def test_a_failing_step_stops_the_install(failed_run: Install) -> None:
    """A step that fails stops the script there, before the next step begins.

    `step` reads the command's own status. An `if` with no `else` whose condition is false exits 0,
    so a status read after one is the if statement's own, not the command's, and would record every
    failed step as a success. A 404 on the Pixi archive would then print its error, report a tick
    for checking the empty file it failed to download, and die two lines later for another reason.
    """
    # No Pixi anywhere, so the script must download it, and curl fails when it tries, which is the
    # run `failed_run` makes.
    done = failed_run.done
    assert "Downloading Pixi" in done.stdout, f"never reached the failing step:\n{done.stdout}"
    assert done.returncode != 0, f"a failed download exited 0:\n{done.stdout}\n{done.stderr}"
    # The load-bearing assertion, and the one that tells the fault apart from its symptom. A script
    # that misreads the status still exits non-zero, three steps later and for the wrong reason, so
    # asserting only on the exit code passes on a broken script. What it must not do is begin
    # the next step, checking and unpacking the empty file the download just failed to write.
    assert "Checking Pixi" not in done.stdout, f"it started the next step anyway:\n{done.stdout}"
    assert "Installing Pixi" not in done.stdout, f"it started the next step anyway:\n{done.stdout}"
    called = failed_run.marker.read_text() if failed_run.marker.exists() else ""
    assert "pixi global" not in called, f"it carried on after the failure: {called}"


def test_a_failure_shows_the_output_it_held_back(failed_run: Install) -> None:
    """Held-back output is only worth holding back if it arrives when it is needed."""
    done = failed_run.done
    assert "Downloading Pixi" in done.stdout, f"never reached the failing step:\n{done.stdout}"
    assert "deliberate failure" in done.stdout + done.stderr, done.stdout + done.stderr


def test_the_log_is_kept_and_named_when_something_fails(failed_run: Install) -> None:
    done, log = failed_run.done, failed_run.log
    assert done.returncode != 0, f"nothing failed, so there is no failure to keep a log for:\n{done.stdout}"
    assert log.exists(), "the log was removed on the one run where it was worth keeping"
    assert str(log) in done.stderr, f"the path was never printed: {done.stderr}"
    assert "exited 1" in log.read_text(), log.read_text()


def test_the_log_is_removed_when_nothing_fails(clean_run: Install) -> None:
    assert clean_run.done.returncode == 0, clean_run.done.stderr
    assert clean_run.marker.exists()
    assert not clean_run.log.exists(), "a clean run left a log file behind"


def test_keep_log_keeps_it(tmp_path: Path, source: str) -> None:
    log = tmp_path / "install.log"
    env = fake_path(tmp_path, tmp_path / "called") | {"DECKTALK_INSTALL_LOG": str(log)}
    done = run(["--keep-log"], env=env, script=source)
    assert done.returncode == 0, done.stderr
    assert log.exists()
    assert INSTALLS in log.read_text()


# ---- the Pixi it downloads, and the DeckTalk it updates ------------------------------------------


def test_the_pixi_it_downloads_is_the_pixi_ci_runs(source: str) -> None:
    """`requires-pixi` is the one home of the Pixi version, and the installer pins its own copy of it."""
    floor = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pixi"]["workspace"]
    pinned = [line for line in source.splitlines() if line.startswith("PIXI_VERSION=")]
    assert pinned == [f'PIXI_VERSION="{floor["requires-pixi"].removeprefix(">=")}"'], pinned


def serve_pixi(root: Path, marker: Path, *, digest: str | None = None) -> None:
    """Replace the curl stub with one that serves a Pixi archive and its checksum file.

    The archive holds a stub `pixi` that records its calls, so a run that unpacks it goes on to
    install DeckTalk with it. `digest` is what the checksum file says, which is the archive's own
    unless a test gives another.
    """
    stub = f'#!/bin/sh\necho "pixi $*" >> "{marker}"\nexit 0\n'.encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        entry = tarfile.TarInfo("pixi")
        entry.size, entry.mode = len(stub), 0o755
        tar.addfile(entry, io.BytesIO(stub))
    (root / "pixi.tar.gz").write_bytes(archive.getvalue())
    said = digest or hashlib.sha256(archive.getvalue()).hexdigest()
    (root / "pixi.tar.gz.sha256").write_text(f"{said}  pixi.tar.gz\n")
    curl = root / "bin" / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f'echo "curl $*" >> "{marker}"\n'
        f'case "$*" in *.sha256) cat "{root}/pixi.tar.gz.sha256" ;; *) cat "{root}/pixi.tar.gz" ;; esac\n'
    )
    curl.chmod(0o755)


def test_a_checked_download_is_unpacked_and_installs_decktalk(tmp_path: Path, source: str) -> None:
    """The whole download path, offline: the archive for this machine, its checksum, the unpack, the install."""
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker, without=("pixi",))
    serve_pixi(tmp_path, marker)
    done = run([], env=env, script=source)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / ".pixi" / "bin" / "pixi").exists(), done.stdout
    called = marker.read_text()
    assert "releases/download/v" in called and ".sha256" in called, called
    assert INSTALLS in called, called


def test_a_digest_that_does_not_match_stops_before_the_unpack(tmp_path: Path, source: str) -> None:
    marker = tmp_path / "called"
    env = fake_path(tmp_path, marker, without=("pixi",))
    serve_pixi(tmp_path, marker, digest="0" * 64)
    done = run([], env=env, script=source)
    assert done.returncode != 0, done.stdout
    assert "does not match" in done.stderr, done.stderr
    assert "Installing Pixi" not in done.stdout, done.stdout
    assert not (tmp_path / ".pixi" / "bin" / "pixi").exists(), "it unpacked an archive that failed its check"
    assert "pixi global" not in marker.read_text()


def test_a_second_run_updates_what_the_first_installed(tmp_path: Path, source: str) -> None:
    """`pixi global install` leaves an installed package where it is, so a rerun must also update."""
    marker = tmp_path / "called"
    (tmp_path / ".pixi" / "envs" / "decktalk").mkdir(parents=True)
    done = run([], env=fake_path(tmp_path, marker), script=source)
    assert done.returncode == 0, done.stderr
    called = marker.read_text().splitlines()
    assert called.index(INSTALLS) < called.index("pixi global update decktalk"), called


def test_a_pinned_rerun_moves_to_the_pin_and_updates_nothing(tmp_path: Path, source: str) -> None:
    """A pinned install writes the pin into Pixi's manifest, so an update after it would change nothing."""
    marker = tmp_path / "called"
    (tmp_path / ".pixi" / "envs" / "decktalk").mkdir(parents=True)
    env = fake_path(tmp_path, marker) | {"DECKTALK_VERSION": "0.4.1"}
    done = run([], env=env, script=source)
    assert done.returncode == 0, done.stderr
    called = marker.read_text()
    assert f"{INSTALLS}==0.4.1" in called and "pixi global update" not in called, called


def test_no_shell_startup_file_is_written(clean_run: Install) -> None:
    """The script prints the PATH line. Writing it into a startup file would be doing more than it says."""
    home = clean_run.marker.parent
    startup = (".profile", ".bashrc", ".bash_profile", ".zshrc", ".tcshrc", ".config")
    assert [name for name in startup if (home / name).exists()] == []


def test_the_path_line_names_the_pixi_home_it_installed_into(tmp_path: Path, source: str) -> None:
    home = tmp_path / "elsewhere"
    env = fake_path(tmp_path, tmp_path / "called") | {"PIXI_HOME": str(home)}
    done = run([], env=env, script=source)
    assert done.returncode == 0, done.stderr
    assert f'export PATH="{home}/bin:$PATH"' in done.stdout, done.stdout


# ---- the one step that can reach sudo -----------------------------------------------------------


def test_it_reports_the_version_that_is_actually_on_disk(clean_run: Install) -> None:
    """ "Installed" is a claim about the command the reader is about to type, not about the one that
    just ran, so the version is read back from the binary."""
    done = clean_run.done
    assert "decktalk 0.0.0 is installed" in done.stdout, done.stdout


def test_the_next_steps_are_the_ones_the_readme_gives(clean_run: Install) -> None:
    """The README and the quickstart start `my-lesson`, and macOS needs no `decktalk install` first, so the
    installer names the same project and says who needs the fetch first."""
    said = clean_run.done.stdout
    assert "decktalk init my-lesson" in said and "cd my-lesson && decktalk build --no-spend" in said, said
    assert "my-film" not in said
    assert "# Linux, or to fetch Chromium and ffmpeg now" in said, said
    # The starter names its voice in a [voices.narrator] table, so that is the table the note names.
    assert "set [voices.narrator] id in decktalk.toml" in said, said


# ---- presentation degrades rather than breaking -------------------------------------------------


def test_nothing_writes_escape_codes_when_stdout_is_not_a_terminal(clean_run: Install) -> None:
    """A CI log full of colour codes is a log nobody reads. `[ -t 1 ]` is the whole guard, and this
    is the assertion that it is actually consulted everywhere rather than in most places."""
    done = clean_run.done
    assert "\033[" not in done.stdout, repr(done.stdout)
    assert "\033[" not in done.stderr, repr(done.stderr)


def test_the_plain_path_still_names_every_step(clean_run: Install) -> None:
    """Without a spinner the labels are all a reader gets, so they must still be printed."""
    done = clean_run.done
    assert "Installing decktalk" in done.stdout, done.stdout


# ---- what needs a real terminal -----------------------------------------------------------------
#
# `[ -t 1 ]` is what the script branches on for colour and the spinner, and a pipe cannot
# reproduce it. pexpect forks the run under a pty that is its controlling terminal, which is what
# /dev/tty then opens. Popen with a pty on stdout would leave that pointing at pytest's own.


def interruptible() -> None:
    """Give the forked run the default Ctrl-C, whatever disposition the process that started pytest had.

    A shell that is not interactive starts a background job with SIGINT ignored, and a child inherits
    that. POSIX lets a shell refuse a `trap` on a signal that was ignored when it started, so without
    this a pytest started with `&` would run an installer whose INT trap never fires, and the
    interrupt test would see a run that finished as if nothing had interrupted it.
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def run_pty(
    args: list[str],
    env: dict[str, str],
    interrupt_when: Ready | None = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Run install.sh under a pty, interrupted once `interrupt_when` holds, and return its status and output.

    The interrupt waits for the run to reach the moment it is about rather than for a fixed time,
    because a wall-clock wait is a race with the machine's load: an interrupt that lands before the
    script has set its traps tests nothing, and under a parallel suite it landed there in half the runs.
    """
    import pexpect  # noqa: PLC0415 - its pty spawn needs termios, which is what pytestmark refuses Windows on

    child = pexpect.spawn(
        "/bin/sh",
        [str(SCRIPT), *args],
        env=env,
        timeout=timeout,
        encoding="utf-8",
        codec_errors="replace",
        preexec_fn=interruptible,
    )
    try:
        started = time.monotonic()
        while interrupt_when is not None and not interrupt_when():
            if time.monotonic() - started > timeout:
                raise AssertionError(f"install.sh never reached the moment to interrupt in {timeout}s")
            child.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=POLL_SECONDS)
        if interrupt_when is not None:
            child.sendintr()  # Ctrl-C itself, which the terminal hands the whole foreground process group.
        child.expect(pexpect.EOF)
        out = str(child.before)  # everything since the last match, and nothing has matched but the end
    finally:
        child.close(force=True)
    return os.waitstatus_to_exitcode(child.status or 0), out


def test_a_terminal_gets_the_spinner_and_a_tick(tmp_path: Path) -> None:
    code, out = run_pty([], fake_path(tmp_path, tmp_path / "called"))
    assert code == 0, out
    assert "\033[" in out, "a terminal got no colour at all"
    assert "✓" in out, out


def test_no_color_is_honoured_on_a_terminal(tmp_path: Path) -> None:
    """NO_COLOR is set by people who mean it, and a terminal is exactly where it has to be obeyed."""
    env = fake_path(tmp_path, tmp_path / "called") | {"NO_COLOR": "1"}
    code, out = run_pty([], env)
    assert code == 0, out
    assert "\033[" not in out, repr(out)


@pytest.fixture(params=["foreground", "background"], ids=["pytest in the foreground", "pytest as a background job"])
def started(request: pytest.FixtureRequest) -> Iterator[None]:
    """How the shell that started pytest left SIGINT: as it was, or ignored, as `pytest &` leaves it."""
    if request.param == "foreground":
        yield
        return
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.usefixtures("started")
def test_an_interrupt_puts_the_cursor_back_and_keeps_the_log(tmp_path: Path) -> None:
    """The spinner hides the cursor, so a trap restores it. Without one, a Ctrl-C mid-spinner leaves a
    terminal with no cursor in it until the next `reset`, and throws away the log of what happened."""
    log = tmp_path / "install.log"
    called = tmp_path / "called"
    env = fake_path(tmp_path, called, slow=("pixi",)) | {"DECKTALK_INSTALL_LOG": str(log)}

    def installing() -> bool:
        """Whether the slow stub has started, which is after the traps are set and inside the step.

        The step's label is printed before its child is forked, and an interrupt that lands in that
        gap reaches the shell alone, which holds the trap until the child it has not yet started ends.
        """
        return called.exists() and "pixi global install" in called.read_text(encoding="utf-8")

    code, out = run_pty([], env, interrupt_when=installing, timeout=30)
    assert code == 130, f"an interrupt should exit 130, got {code}:\n{out}"
    assert "\033[?25h" in out, "the cursor was left hidden"
    assert log.exists(), "the log of an interrupted run was thrown away"
