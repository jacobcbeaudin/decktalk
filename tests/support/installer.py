"""The PATH of stub tools install.sh runs against, shared by the installer suite and the docs claims."""

from __future__ import annotations

import os
from pathlib import Path


def fake_path(
    tmp_path: Path,
    marker: Path,
    *,
    fail: tuple[str, ...] = (),
    slow: tuple[str, ...] = (),
    without: tuple[str, ...] = (),
    uname: str | None = None,
) -> dict[str, str]:
    """A PATH holding stubs that only record that they were called.

    `decktalk` is one of them on purpose. Without it the script falls through to
    `$HOME/.local/bin/decktalk`, so on a machine that has DeckTalk installed the tests would pass by
    reaching the real one, and on a machine that does not they would fail for a reason that has
    nothing to do with the script.

    `fail` names stubs that exit 1, for the paths where something goes wrong. `slow` names stubs
    that take five seconds. `without` names tools that must not be found at all. `uname` is what a
    stub `uname -s` prints, for the platform the script is made to believe it runs on.

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
    if uname is not None:
        (bin_dir / "uname").write_text(f"#!/bin/sh\necho {uname}\n")
        (bin_dir / "uname").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    # HOME too, because find_decktalk falls back to $HOME/.local/bin/decktalk. Left pointing at the
    # real home, a test would quietly exercise whatever DeckTalk the author happens to have.
    env["HOME"] = str(tmp_path)
    return env
