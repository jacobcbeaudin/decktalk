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
import sys
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
