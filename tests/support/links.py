"""One link a test plants, or a skip on a machine that will not let an unprivileged user make one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def link(path: Path, target: Path, *, hard: bool = False) -> None:
    """Make `path` a symbolic link to `target`, or a hard link with `hard`, or skip where neither is allowed."""
    try:
        if hard:
            os.link(target, path)
        else:
            path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
