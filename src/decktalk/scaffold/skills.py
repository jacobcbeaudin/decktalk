"""The six packaged skills, and where a project keeps them.

A project keeps one folder of skills, `.agents/skills/`, and `.claude/skills` is a symbolic link to
it, so a second harness needs no second copy. Windows refuses a symbolic link without a developer
mode or an elevated process, so there the link becomes a copy.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..toolchain import assets

log = logging.getLogger(__name__)

SKILLS_DIR = Path(".agents") / "skills"
"""Where a project keeps its skills, relative to the project root."""

LINK_DIR = Path(".claude") / "skills"
"""The Claude Code folder, a link to SKILLS_DIR, or a copy of it on Windows."""

SKILL_NAMES = (
    "decktalk-script",
    "decktalk-slide",
    "decktalk-cues",
    "decktalk-build",
    "decktalk-fix",
    "decktalk-revise",
)
"""Every skill in the wheel, in the order an author meets them."""


def skills_dir() -> Path:
    """The packaged skills directory inside the wheel."""
    return assets.package_file("skills")


def packaged_skills() -> list[Path]:
    """One directory per packaged skill, in SKILL_NAMES order."""
    root = skills_dir()
    return [root / name for name in SKILL_NAMES]


def install_skills(root: Path) -> list[Path]:
    """Write every packaged skill into `root/.agents/skills/`, with the Claude Code link beside it.

    Returns the paths written, relative to nothing and absolute, with the link last. A skill folder
    that is already there is replaced, because the packaged skills are the only source there is.
    """
    target = root / SKILLS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for src in packaged_skills():
        dst = target / src.name
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        written.append(dst)
    written.append(_link(root))
    return written


def _link(root: Path) -> Path:
    """`.claude/skills` pointing at `.agents/skills`, as a symbolic link or, on Windows, a copy."""
    link = root / LINK_DIR
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    relative = Path(os.path.relpath(root / SKILLS_DIR, link.parent))
    try:
        link.symlink_to(relative, target_is_directory=True)
    except OSError:
        # Windows without developer mode, and any filesystem that has no links.
        shutil.copytree(root / SKILLS_DIR, link)
        log.debug("copied the skills into %s, because this system refused a symbolic link", link)
    return link
