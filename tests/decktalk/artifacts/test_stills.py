"""Frozen frames kept by what drew them, and the rule that says whether one is still current."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import stills as module
from decktalk.artifacts.stills import Stills, still_key


def a_store(tmp_path: Path) -> Stills:
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<html></html>", encoding="utf-8")
    return Stills(tmp_path / "build" / "stills", tmp_path)


def drawn(tmp_path: Path, body: bytes = b"png") -> Path:
    shot = tmp_path / "shot.png"
    shot.write_bytes(body)
    return shot


def test_a_frame_kept_under_its_key_is_found_again(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    kept = store.keep("k", drawn(tmp_path), ["deck/index.html"])
    assert store.find("k") == kept
    assert kept.read_bytes() == b"png"


def test_nothing_is_found_under_a_key_nobody_kept(tmp_path: Path) -> None:
    assert a_store(tmp_path).find("k") is None


def test_a_frame_whose_loaded_file_moved_is_not_trusted(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    store.keep("k", drawn(tmp_path), ["deck/index.html"])
    (tmp_path / "deck" / "index.html").write_text("<html>edited</html>", encoding="utf-8")
    assert store.find("k") is None


def test_a_frame_whose_loaded_file_is_gone_is_not_trusted(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    store.keep("k", drawn(tmp_path), ["deck/index.html"])
    (tmp_path / "deck" / "index.html").unlink()
    assert store.find("k") is None


def test_a_frame_with_no_manifest_beside_it_is_not_trusted(tmp_path: Path) -> None:
    """A run stopped between the picture and its manifest leaves a picture no later run may keep."""
    store = a_store(tmp_path)
    store.keep("k", drawn(tmp_path), ["deck/index.html"])
    store.manifest("k").unlink()
    assert store.find("k") is None


def test_a_manifest_that_will_not_parse_is_not_trusted(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    store.keep("k", drawn(tmp_path), [])
    store.manifest("k").write_text("{", encoding="utf-8")
    assert store.find("k") is None


def test_the_key_moves_with_every_part_and_with_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    before = still_key(("url", "1920x1080"))
    assert still_key(("url", "1280x720")) != before
    monkeypatch.setattr(module, "ENGINE_VERSION", "999.0.0")
    assert still_key(("url", "1920x1080")) != before
