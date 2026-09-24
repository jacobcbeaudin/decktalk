"""The platform fact: Windows refuses to unlink a file something still holds open, and POSIX does not.

The recorder writes a webm and a log beside it, and the order it deletes and writes them in decides
what a crash leaves behind. On Linux and macOS an open handle survives the unlink, so the test of
that order passes whatever the code does and proves nothing. On Windows the unlink raises, which is
what makes the order a real constraint rather than a preference.

The policy half is `tests/decktalk/stages/record/test_record.py`, which spies the call order and
holds everywhere. This is the environment half, and it holds the assumption that policy rests on.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform
"""Every test here is about this machine, so only the group that names this platform runs one."""


WINDOWS = os.name == "nt"
"""Whether this is the platform whose filesystem refuses the unlink, asked once and named here."""


def test_an_open_handle_decides_whether_the_unlink_raises(tmp_path: Path) -> None:
    """Both answers are asserted, so this file records what the platform does rather than hoping."""
    target = tmp_path / "section.webm"
    target.write_bytes(b"frames")

    with target.open("rb") as handle:
        assert handle.read() == b"frames"
        if WINDOWS:
            raised = False
            try:
                target.unlink()
            except PermissionError:
                raised = True
            assert raised, "Windows is expected to refuse the unlink while a handle is open"
            assert target.exists()
        else:
            target.unlink()
            assert not target.exists()
            assert handle.read() == b"", "an unlinked file is still readable through the handle that held it"


def test_a_file_nothing_holds_is_unlinked_on_every_platform(tmp_path: Path) -> None:
    """The other side of the pair, so a failure above is the handle and never the unlink itself."""
    target = tmp_path / "section.log"
    target.write_text("one line", encoding="utf-8")
    target.unlink()
    assert not target.exists()
