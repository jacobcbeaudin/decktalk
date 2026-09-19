"""Writing a project into a directory.

`decktalk init DIR` writes the starter: three sections, one equation, markup slides and a silent
build of about a minute. `--example NAME` writes a finished project instead. Either way the
packaged runtime and KaTeX land beside the pages, the six skills land in `.agents/skills/`, and an
`AGENTS.md` is written when the directory has none.

A project is written once. A project that wants a newer runtime is created again.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError
from ..toolchain import assets
from .examples import STARTER, example, reserved_message
from .skills import install_skills

DECK_DIR = "deck"
"""The directory of a project that holds its pages, the runtime and KaTeX."""

AGENTS_FILE = "AGENTS.md"

# A file of a packaged project whose name a working copy has to change. A dotfile in the wheel
# would be skipped by some build backends and hidden from a reader browsing the source.
RENAMED = {"gitignore": ".gitignore", "env.example": ".env.example"}

FILLED_SUFFIXES = {".toml", ".md", ".json", ".html", ".css", ".js", ".txt", ".example", ""}
"""Suffixes of files that carry __NAME__ and __TITLE__. Everything else is copied byte for byte."""


@dataclass(frozen=True)
class InitResult:
    """What `init` wrote: the project directory, and every file in it, in the order written."""

    root: Path
    written: tuple[Path, ...]
    example: str
    """The name of the example written, or `starter`."""

    @property
    def skills(self) -> bool:
        return any(".agents" in p.parts for p in self.written)


def title_from(name: str) -> str:
    """The project name as a title, for the pages the starter writes."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "Untitled"


def init(
    target: Path,
    *,
    name: str | None = None,
    force: bool = False,
    example_name: str | None = None,
    skills: bool = True,
) -> InitResult:
    """Write a project into `target` and return what was written.

    `example_name` picks a packaged example instead of the starter. `skills` writes the six skills
    into `.agents/skills/` with the Claude Code link beside them.
    """
    target = target.resolve()
    name = name or target.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ConfigError(f"project name {name!r} must be letters, digits, dots, dashes or underscores")
    if target.exists() and any(target.iterdir()) and not force:
        raise ConfigError(f"{target} is not empty (pass force to write into it anyway)")

    source = _source(example_name)
    written = [_copy(src, target / _destination(src.relative_to(source)), name) for src in _files(source)]
    deck = target / DECK_DIR
    deck.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(assets.runtime_path(), deck / assets.RUNTIME_FILE)
    written.append(deck / assets.RUNTIME_FILE)
    written.append(assets.vendor_katex(deck))
    # An AGENTS.md the author already wrote is theirs, so it is never replaced.
    agents = target / AGENTS_FILE
    if not agents.exists():
        written.append(_copy(assets.package_file(f"template/{AGENTS_FILE}"), agents, name))
    if skills:
        written.extend(install_skills(target))
    return InitResult(target, tuple(written), example_name or STARTER)


def _source(example_name: str | None) -> Path:
    """The packaged project directory `init` copies, raising on an example that is only reserved."""
    if example_name is None:
        return assets.package_file(f"template/{STARTER}")
    ex = example(example_name)
    if not ex.shipped:
        raise ConfigError(reserved_message(ex))
    return assets.package_file(f"template/{ex.path}")


def _files(source: Path) -> list[Path]:
    """Every file of a packaged project, in a stable order."""
    return [p for p in sorted(source.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


def _destination(relative: Path) -> Path:
    """The path a packaged file takes in a working copy, which renames the two dotfiles."""
    return relative.parent / RENAMED.get(relative.name, relative.name)


def _copy(src: Path, dst: Path, name: str) -> Path:
    """Copy one packaged file, filling the project name into the kinds of file that carry it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix in FILLED_SUFFIXES:
        text = src.read_text(encoding="utf-8")
        dst.write_text(text.replace("__NAME__", name).replace("__TITLE__", title_from(name)), encoding="utf-8")
    else:
        shutil.copyfile(src, dst)
    return dst
