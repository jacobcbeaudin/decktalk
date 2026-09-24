"""Where a file is, said the one way every result and every finding says it."""

from __future__ import annotations

from pathlib import Path

from decktalk.inputs.paths import at, relative


def test_a_path_under_the_project_is_published_relative_to_it(tmp_path: Path) -> None:
    assert relative(tmp_path / "deck" / "index.html", tmp_path) == Path("deck/index.html")


def test_a_path_outside_the_project_is_published_as_it_is(tmp_path: Path) -> None:
    """A relative path with a step back up it names nothing a reader can open."""
    outside = tmp_path.parent / "elsewhere" / "take.mp3"
    assert relative(outside, tmp_path) == outside


def test_a_location_is_named_by_the_file_it_is_about(tmp_path: Path) -> None:
    place = at(tmp_path / "cues.json", tmp_path, line=4)
    assert (place.where, place.file, place.line) == ("cues.json", Path("cues.json"), 4)


def test_a_location_carries_whatever_else_the_caller_knew(tmp_path: Path) -> None:
    place = at(tmp_path / "deck" / "index.html", tmp_path, section=3, cue="3.1:expand")
    assert (place.section, place.cue) == (3, "3.1:expand")
    assert place.file == Path("deck/index.html")
