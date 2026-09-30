"""The one `--write` and `--check` every generator runs, so every generator fails with one sentence.

    uv run scripts/build_<name>.py --write    # write every file the generator owns that is stale
    uv run scripts/build_<name>.py --check    # exit 1 if a committed file would change

A generator is a function from its sources to every file it owns, by path. `run` turns that function
into the command line, holds each file to what it returns, and writes only the files it calls stale,
so a write that changes nothing leaves the tree alone. A generator that owns a directory names it by
a path whose last part is a pattern, and a file there that the generator no longer returns is stale
and is removed on a write.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STALE = "stale: {path}, because {reason}. Run `uv run scripts/{script} --write` to bring it up to date."
"""The one sentence every generator fails with, naming the file, why, and the command that fixes it."""

GONE = "no generator writes it any more"
"""Why a file in a directory a generator owns is stale when the generator no longer returns it."""


def splice(text: str, markers: tuple[str, str], block: str, *, where: Path) -> str:
    """`text` with `block` written between its two markers, and every line outside them left alone."""
    start, end = markers
    if start not in text or end not in text:
        raise SystemExit(f"{where.relative_to(ROOT)} has no {start} ... {end} block to fill.")
    head, _, rest = text.partition(start)
    _, _, tail = rest.partition(end)
    return f"{head}{start}{block}{end}{tail}"


def differs(path: Path, text: str) -> str | None:
    """Why the committed file no longer says `text`, naming the first line that moved, or None."""
    if not path.exists():
        return "it is not committed"
    committed = path.read_text(encoding="utf-8")
    if committed == text:
        return None
    pairs = enumerate(zip(committed.splitlines(), text.splitlines(), strict=False), start=1)
    line = next((number for number, (old, new) in pairs if old != new), None)
    return f"line {line} differs from its source" if line else "its length differs from its source"


COMMAND_TIMEOUT_SECONDS = 600
"""Calibration: far longer than any bundle, type check or format takes, so only a tool that hung reaches it."""


def command(cmd: list[str | Path], *, stdin: str | None = None) -> str:
    """One tool, with its output returned and its failure raised with the command, its exit, its time and its output.

    The tool is stopped after `COMMAND_TIMEOUT_SECONDS`, so a hung `npm` or `esbuild` fails the
    generator with a sentence rather than holding a check run open until its job is killed.
    """
    # uv runs a generator in an environment of its own, and a nested `uv run` would warn about it.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    argv = [str(part) for part in cmd]
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            check=False,
            cwd=ROOT,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{' '.join(argv)} ran for {COMMAND_TIMEOUT_SECONDS} seconds and was stopped.") from None
    if done.returncode != 0:
        seconds = time.monotonic() - started
        raise SystemExit(
            f"{' '.join(argv)} exited {done.returncode} after {seconds:.1f} seconds:\n{done.stdout}{done.stderr}"
        )
    return done.stdout


def ruff(text: str, target: Path) -> str:
    """Generated Python as the project's own ruff sorts its imports and formats it, read as `target`.

    A generator writes plain text and lets ruff order and wrap it, so the committed module is what
    `ruff check` and `ruff format` would leave and the rule for either lives in one place.
    """
    name = f"--stdin-filename={target}"
    ordered = command(["uv", "run", "ruff", "check", "--select", "I", "--fix-only", "--quiet", name, "-"], stdin=text)
    return command(["uv", "run", "ruff", "format", name, "-"], stdin=ordered)


def run(
    files: Callable[[], dict[Path, str]],
    *,
    owned: Iterable[Path] = (),
    why_stale: Callable[[Path, str], str | None] = differs,
    wrote: Callable[[set[Path]], None] | None = None,
) -> int:
    """Parse the mode, then check or write every file `files` returns and every file `owned` matches.

    The mode is parsed before a source is read, so a generator run with neither mode or with both is
    refused before it touches anything. `why_stale` is the comparison, which a measured figure widens
    to a tolerance, and `wrote` hears which files a write changed.
    """
    main = sys.modules["__main__"]
    script = Path(main.__file__ or sys.argv[0]).name
    parser = argparse.ArgumentParser(description=main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write every file this generator owns that is stale")
    mode.add_argument("--check", action="store_true", help="exit 1 if a committed file would change")
    args = parser.parse_args()

    wanted = files()
    stale = {path: reason for path, text in wanted.items() if (reason := why_stale(path, text))}
    stale |= {
        path: GONE for pattern in owned for path in sorted(pattern.parent.glob(pattern.name)) if path not in wanted
    }
    if args.check:
        for path, reason in stale.items():
            print(STALE.format(path=path.relative_to(ROOT).as_posix(), reason=reason, script=script))
        return 1 if stale else 0
    for path in stale:
        if path in wanted:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(wanted[path], encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
        else:
            path.unlink()
            print(f"removed {path.relative_to(ROOT).as_posix()}")
    if not stale:
        print(f"every file {script} writes already says what its source says")
    if wrote is not None:
        wrote(set(stale))
    return 0
