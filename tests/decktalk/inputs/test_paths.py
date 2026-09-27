"""Where a file is, said the one way every result and every finding says it."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import ErrorCode, InputError
from decktalk.inputs.paths import at, contained, relative


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


def test_a_file_inside_the_project_is_joined_to_its_root(tmp_path: Path) -> None:
    assert contained(tmp_path, "deck/index.html") == tmp_path / "deck" / "index.html"


def test_a_link_that_stays_inside_the_project_keeps_the_name_the_author_wrote(tmp_path: Path) -> None:
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "real.png").write_bytes(b"png")
    (tmp_path / "shared.png").symlink_to(tmp_path / "media" / "real.png")
    assert contained(tmp_path, "shared.png") == tmp_path / "shared.png"


def test_a_link_out_of_the_project_is_refused_without_naming_where_it_leads(tmp_path: Path) -> None:
    """The lexical check in tomlmap cannot see a link, so this is the rule that does."""
    root, outside = tmp_path / "project", tmp_path / "secret-host-file"
    root.mkdir()
    outside.write_text("SECRET-HOST-LINE\n", encoding="utf-8")
    (root / "script.md").symlink_to(outside)
    with pytest.raises(InputError) as refused:
        contained(root, "script.md")
    assert refused.value.code is ErrorCode.INPUT
    assert "secret-host-file" not in str(refused.value)
    assert refused.value.location is not None and refused.value.location.file == Path("script.md")


def test_a_directory_linked_out_of_the_project_refuses_every_file_under_it(tmp_path: Path) -> None:
    root, outside = tmp_path / "project", tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    (root / "deck").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InputError):
        contained(root, "deck/index.html")


def test_an_absolute_path_outside_the_project_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(InputError):
        contained(root, tmp_path / "elsewhere.txt")
