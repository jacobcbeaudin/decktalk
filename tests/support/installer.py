"""The PATH of stub tools install.sh runs against, shared by the installer suite and the docs claims."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path

STUBS = ("pixi", "curl", "sudo", "wget", "decktalk", "uname", "ldd")
"""Every tool install.sh may call whose answer a test decides."""

QUESTIONS = ("uname", "ldd")
"""The stubs that only describe the machine, which record no call, because asking changes nothing."""

ANSWERS: Mapping[str, tuple[int, str]] = {
    "uname -s": (0, "Linux"),
    "uname -m": (0, "x86_64"),
    "uname -a": (0, "Linux stub 6.0 x86_64"),
    "ldd --version": (0, "ldd (GNU libc) 2.41"),
    "pixi --version": (0, "pixi 99.0.0"),
    "decktalk --version": (0, "decktalk 0.0.0"),
}
"""What each stub answers unless a test says otherwise: a glibc x86_64 Linux with a Pixi new enough."""


def stub(name: str, marker: Path, answers: Mapping[str, tuple[int, str]], *, slow: bool) -> str:
    """One stub, which records its call and answers the first entry of `answers` its argv starts with.

    A call no entry names prints nothing and exits 0, which is a download, an install or an update
    that worked. A failing answer is printed to stderr, as a tool prints its own failure. An entry
    is matched on the words of the call, so `pixi global update` fails alone while `pixi global
    install` still works.
    """
    lines = ["#!/bin/sh"] if name in QUESTIONS else ["#!/bin/sh", f'echo "{name} $*" >> "{marker}"']
    if slow:
        lines.append("sleep 5")
    lines.append(f'case "{name} $*" in')
    for prefix, (code, said) in sorted(answers.items(), key=lambda item: -len(item[0])):
        if prefix.split()[0] == name:
            stream = "" if code == 0 else " >&2"
            lines.append(f"{shlex.quote(prefix)}*) printf '%s\\n' {shlex.quote(said)}{stream}; exit {code} ;;")
    lines.append("esac")
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def fake_path(
    tmp_path: Path,
    marker: Path,
    *,
    answers: Mapping[str, tuple[int, str]] | None = None,
    slow: tuple[str, ...] = (),
    without: tuple[str, ...] = (),
) -> dict[str, str]:
    """A PATH holding stubs that record that they were called and answer what a test decides.

    `decktalk` is one of them on purpose. Without it the script finds no `$PIXI_HOME/bin/decktalk`
    and nothing on PATH, so the tests would fail for a reason that has nothing to do with the script.
    `pixi` is one too, so a run finds Pixi already there and never downloads it, unless `without`
    removes it.

    `answers` maps the start of a call, such as `"pixi global update"` or `"uname -m"`, to the exit
    code and the line its stub gives, over `ANSWERS`. `slow` names stubs that take five seconds.
    `without` names tools that must not be found at all.

    PATH is the stub directory and /usr/bin:/bin, which is what makes `without` mean anything: Pixi
    lives in ~/.pixi/bin or Homebrew, so a PATH that inherited the author's would still find the
    real one after the stub was removed, and a test for "no Pixi on this machine" would silently be
    a test of the machine that has Pixi.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    said = {**ANSWERS, **(answers or {})}
    for name in STUBS:
        if name in without:
            continue
        path = bin_dir / name
        path.write_text(stub(name, marker, said, slow=name in slow))
        path.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    # HOME too, because Pixi's home defaults to $HOME/.pixi and find_decktalk reads its bin folder
    # first. Left pointing at the real home, a test would quietly exercise whatever DeckTalk and
    # Pixi the author happens to have. PIXI_HOME is dropped for the same reason.
    env["HOME"] = str(tmp_path)
    env.pop("PIXI_HOME", None)
    return env
