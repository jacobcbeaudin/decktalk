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

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "site" / "install.sh"
SHELLS = [sh for sh in ("/bin/sh", "/bin/dash", "/bin/busybox") if Path(sh).exists()]


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def fake_path(tmp_path: Path, marker: Path) -> dict[str, str]:
    """A PATH holding a uv and a curl that only record that they were called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("uv", "curl", "sudo", "wget"):
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{marker}"\n[ "$1" = "--version" ] && echo "{name} 0.0.0"\nexit 0\n'
        )
        stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
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
    records being called, and require that it never is."""
    marker = tmp_path / "called"
    run([], env=fake_path(tmp_path, marker), script=source)
    assert marker.exists(), "the run did nothing, so it proves nothing"
    assert "sudo" not in marker.read_text(), marker.read_text()


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
