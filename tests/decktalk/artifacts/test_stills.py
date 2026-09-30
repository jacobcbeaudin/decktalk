"""Frozen frames kept by what drew them, and the rule that says whether one is still current."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from decktalk.artifacts import stills as module
from decktalk.artifacts import stored
from decktalk.artifacts.stills import Stills, still_key
from support.logs import decisions


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
    monkeypatch.setattr(stored, "ENGINE_VERSION", "999.0.0")
    assert still_key(("url", "1920x1080")) != before


def idle(path: Path) -> None:
    """Age one kept frame past the idle window, as a fortnight without a question about it would."""
    past = path.stat().st_mtime - module.IDLE_SECONDS - 1
    os.utime(path, (past, past))


def test_a_frame_nobody_asked_for_is_removed_when_another_is_kept(tmp_path: Path) -> None:
    """Every edit gives a page's frames new keys, so the old ones would otherwise stay forever."""
    store = a_store(tmp_path)
    old = store.keep("old", drawn(tmp_path), [])
    idle(old)
    store.keep("new", drawn(tmp_path), [])
    assert not old.exists() and not store.manifest("old").exists()
    assert store.find("new") is not None


def test_a_frame_that_is_found_is_kept_however_old_it_was(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    used = store.keep("used", drawn(tmp_path), [])
    idle(used)
    assert store.find("used") == used
    store.keep("new", drawn(tmp_path), [])
    assert store.find("used") == used


def test_every_answer_says_why_it_kept_or_drew_again(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A manifest that would not parse is told apart from one that is not there, although both draw again."""
    store = a_store(tmp_path)

    def why(key: str) -> tuple[object, ...]:
        store.find(key)
        [said] = decisions(caplog, "still")
        return said

    with caplog.at_level("DEBUG", logger="decktalk"):
        assert why("k") == (False, "no-image")
        store.keep("k", drawn(tmp_path), ["deck/index.html"])
        assert why("k") == (True, "unchanged")
        store.manifest("k").write_text("{", encoding="utf-8")
        assert why("k") == (False, "manifest-unreadable")
        store.manifest("k").unlink()
        assert why("k") == (False, "no-manifest")
        store.keep("k", drawn(tmp_path), ["deck/index.html"])
        (tmp_path / "deck" / "index.html").write_text("<html>edited</html>", encoding="utf-8")
        assert why("k") == (False, "source-changed")
