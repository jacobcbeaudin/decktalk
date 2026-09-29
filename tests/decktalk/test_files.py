"""A file a person owns is replaced whole, together with every other file of the same change or not at all."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from decktalk.files import replace_all


def test_every_file_is_replaced_and_keeps_the_mode_it_had(tmp_path: Path) -> None:
    (tmp_path / "kept.txt").write_text("old\n", encoding="utf-8")
    (tmp_path / "kept.txt").chmod(0o640)
    replace_all({tmp_path / "kept.txt": "new\n", tmp_path / "deep" / "made.bin": b"\x00\x01"})
    assert (tmp_path / "kept.txt").read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE((tmp_path / "kept.txt").stat().st_mode) == 0o640
    assert (tmp_path / "deep" / "made.bin").read_bytes() == b"\x00\x01"
    assert [p.name for p in tmp_path.rglob("*.draft")] == []


def test_a_move_that_fails_puts_back_every_file_already_moved_and_removes_one_it_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    real = Path.replace
    moved: list[Path] = []

    def refuse_the_third(self: Path, target: Path) -> Path:
        moved.append(Path(target))
        if len(moved) == 3:
            raise OSError(28, "No space left on device", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", refuse_the_third)
    texts = {tmp_path / "first.txt": "changed\n", tmp_path / "new.txt": "made\n", tmp_path / "third.txt": "x\n"}
    with pytest.raises(OSError, match="No space"):
        replace_all(texts)
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert not (tmp_path / "new.txt").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first.txt"]


def test_a_draft_is_never_written_through_a_link_already_under_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("decktalk.files.secrets.token_hex", lambda _size: "planted")
    (tmp_path / "notes.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("mine\n", encoding="utf-8")
    try:
        os.symlink(elsewhere, tmp_path / ".notes.txt.planted.draft")
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
    with pytest.raises(FileExistsError):
        replace_all({tmp_path / "notes.txt": "changed\n"})
    assert elsewhere.read_text(encoding="utf-8") == "mine\n"
    assert (tmp_path / ".notes.txt.planted.draft").is_symlink()
