"""Frozen frames kept by what drew them, and the rule that says whether one is still current."""

from __future__ import annotations

import os
from collections.abc import Callable
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


@pytest.mark.parametrize(
    "spoil",
    [
        lambda root, _store: (root / "deck" / "index.html").write_text("<html>edited</html>", encoding="utf-8"),
        lambda root, _store: (root / "deck" / "index.html").unlink(),
        # A run stopped between the picture and its manifest leaves a picture no later run may keep.
        lambda _root, store: store.manifest("k").unlink(),
        lambda _root, store: store.manifest("k").write_text("{", encoding="utf-8"),
    ],
    ids=["source-moved", "source-gone", "no-manifest", "manifest-unreadable"],
)
def test_a_frame_is_not_trusted_once_what_vouches_for_it_changed(
    tmp_path: Path, spoil: Callable[[Path, Stills], object]
) -> None:
    store = a_store(tmp_path)
    store.keep("k", drawn(tmp_path), ["deck/index.html"])
    spoil(tmp_path, store)
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
    """A manifest that would not parse draws again as a missing one does, and leaves a record saying so."""
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
        assert why("k") == (False, "no-manifest")
        assert any("built again" in record.getMessage() for record in caplog.records)
        caplog.clear()
        store.manifest("k").unlink()
        assert why("k") == (False, "no-manifest")
        store.keep("k", drawn(tmp_path), ["deck/index.html"])
        (tmp_path / "deck" / "index.html").write_text("<html>edited</html>", encoding="utf-8")
        assert why("k") == (False, "source-changed")
