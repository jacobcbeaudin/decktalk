"""Where a file is, said the one way every result and every finding says it."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from decktalk.errors import ErrorCode, InputError
from decktalk.inputs.paths import at, confined, contained, relative


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


ROOT = Path(tempfile.gettempdir()).resolve() / "decktalk-containment" / "project"
"""A project that is never created, because the lexical half of the rule needs no file to judge."""

STEPS = st.lists(st.sampled_from(("deck", "index.html", ".", "..", ROOT.name)), min_size=1, max_size=6)
"""A path a project could name, built from steps down, steps back up and the project's own name."""


@given(STEPS, st.booleans())
def test_a_path_is_joined_to_its_root_exactly_when_it_stays_inside_the_project(
    steps: list[str], absolute: bool
) -> None:
    """A step back up may leave and come in again, so only where the whole path lands decides."""
    named = Path(*steps) if not absolute else ROOT.parent / Path(*steps)
    lands = Path(os.path.normpath(ROOT / named))
    if lands.is_relative_to(ROOT):
        assert contained(ROOT, named) == ROOT / named
    else:
        with pytest.raises(InputError):
            contained(ROOT, named)


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


def test_a_build_directory_that_is_not_there_yet_is_confined_as_it_stands(tmp_path: Path) -> None:
    assert confined(tmp_path, tmp_path / "build") == (tmp_path / "build").resolve()


def test_a_link_that_stays_inside_the_build_directory_is_left_alone(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "final").mkdir(parents=True)
    (build / "latest").symlink_to(build / "final", target_is_directory=True)
    assert confined(tmp_path, build) == build.resolve()


@pytest.mark.parametrize("depth", ["narrate", "narrate/takes"])
def test_a_directory_under_the_build_linked_out_of_it_refuses_the_tree(tmp_path: Path, depth: str) -> None:
    """Every path under `build/` is a plain join, so a link anywhere in it carries writes with it."""
    root, outside = tmp_path / "project", tmp_path / "elsewhere"
    outside.mkdir()
    link = root / "build" / depth
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InputError) as refused:
        confined(root, root / "build")
    assert refused.value.code is ErrorCode.INPUT
    assert "elsewhere" not in str(refused.value)
    assert refused.value.location is not None and refused.value.location.file == Path("build", depth)


def test_a_file_under_the_build_linked_out_of_it_refuses_the_tree(tmp_path: Path) -> None:
    """ffmpeg and a plain write both follow a link at the file they overwrite."""
    root, outside = tmp_path / "project", tmp_path / "victim.mp3"
    outside.write_bytes(b"")
    (root / "build" / "narrate").mkdir(parents=True)
    (root / "build" / "narrate" / "narration.mp3").symlink_to(outside)
    with pytest.raises(InputError):
        confined(root, root / "build")


def test_a_file_under_the_build_hard_linked_elsewhere_refuses_the_tree(tmp_path: Path) -> None:
    """A write that truncates a hard link truncates every other name for the same contents."""
    root, outside = tmp_path / "project", tmp_path / "victim.json"
    outside.write_text("{}", encoding="utf-8")
    (root / "build").mkdir(parents=True)
    (root / "build" / "takes.json").hardlink_to(outside)
    with pytest.raises(InputError):
        confined(root, root / "build")
