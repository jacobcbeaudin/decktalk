"""The projects and the skills packaged in the wheel, and writing one of them into a directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk import template
from decktalk.errors import ErrorCode, InputError
from decktalk.template import EXAMPLES, SKILL_NAMES, STARTER, example, listed_names, title_from, write_project


def test_every_example_is_named_once_and_a_reserved_one_says_so() -> None:
    assert [found.name for found in EXAMPLES] == ["lesson", "product", "tutorial"]
    assert "product (reserved)" in listed_names()
    assert example("lesson").shipped and not example("product").shipped


def test_an_example_nobody_ships_is_refused_with_what_it_is_waiting_for(tmp_path: Path) -> None:
    with pytest.raises(InputError) as refused:
        write_project(tmp_path, name="demo", example_name="product", skills=False, force=False)
    assert refused.value.code is ErrorCode.INPUT
    assert "reserved name" in str(refused.value)
    assert "decktalk init --example lesson" in (refused.value.hint or "")


def test_an_example_nobody_has_heard_of_names_every_example_there_is() -> None:
    with pytest.raises(InputError) as refused:
        example("nope")
    assert "lesson" in (refused.value.hint or "")


@pytest.mark.parametrize(
    ("name", "title"),
    [("my-lesson", "My Lesson"), ("uv_tutorial", "Uv Tutorial"), ("demo", "Demo"), ("  ", "Untitled")],
)
def test_a_project_name_becomes_the_title_its_pages_print(name: str, title: str) -> None:
    assert title_from(name) == title


@pytest.mark.parametrize("name", ["-leading", "with space", "", "a/b"])
def test_a_name_a_file_could_not_carry_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(InputError, match="letters, digits"):
        write_project(tmp_path, name=name, example_name=None, skills=False, force=False)


def test_a_directory_with_something_in_it_is_left_alone_unless_the_caller_insists(tmp_path: Path) -> None:
    (tmp_path / "mine.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(InputError, match="not empty"):
        write_project(tmp_path, name="demo", example_name=None, skills=False, force=False)
    written = write_project(tmp_path, name="demo", example_name=None, skills=False, force=True)
    assert written
    assert (tmp_path / "mine.txt").read_text(encoding="utf-8") == "keep me"


def test_the_starter_writes_a_project_that_already_builds(tmp_path: Path) -> None:
    written = write_project(tmp_path, name="demo", example_name=None, skills=False, force=False)
    assert (tmp_path / "decktalk.toml").exists()
    assert (tmp_path / "script.md").exists()
    assert (tmp_path / "cues.json").exists()
    assert (tmp_path / template.DECK_DIR / "decktalk-runtime.js").exists()
    assert set(written) == set(written)  # every path is reported once, in the order it was written


def test_the_project_name_is_filled_into_the_files_that_carry_it(tmp_path: Path) -> None:
    write_project(tmp_path, name="demo", example_name=None, skills=False, force=False)
    toml = (tmp_path / "decktalk.toml").read_text(encoding="utf-8")
    assert template.NAME_MARK not in toml and template.TITLE_MARK not in toml
    assert 'name = "demo"' in toml


def test_the_two_dotfiles_a_wheel_cannot_carry_are_renamed_on_the_way_in(tmp_path: Path) -> None:
    """A dotfile inside a wheel is skipped by some build backends and hidden from a reader."""
    write_project(tmp_path, name="demo", example_name=None, skills=False, force=False)
    assert (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".env.example").exists()
    assert not (tmp_path / "gitignore").exists()


def test_an_agents_file_the_author_already_wrote_is_never_replaced(tmp_path: Path) -> None:
    (tmp_path / template.AGENTS_FILE).write_text("mine", encoding="utf-8")
    write_project(tmp_path, name="demo", example_name=None, skills=False, force=True)
    assert (tmp_path / template.AGENTS_FILE).read_text(encoding="utf-8") == "mine"


def test_the_skills_land_in_one_folder_with_the_second_harness_beside_it(tmp_path: Path) -> None:
    written = write_project(tmp_path, name="demo", example_name=None, skills=True, force=False)
    for name in SKILL_NAMES:
        assert (tmp_path / template.SKILLS_DIR / name).is_dir(), name
    assert (tmp_path / template.LINK_DIR).exists()
    assert written[-1] == tmp_path / template.LINK_DIR


def test_writing_the_skills_again_replaces_them_rather_than_merging(tmp_path: Path) -> None:
    """The packaged skills are the only source there is, so a copy is replaced whole."""
    template.write_skills(tmp_path)
    stale = tmp_path / template.SKILLS_DIR / SKILL_NAMES[0] / "gone.md"
    stale.write_text("old", encoding="utf-8")
    template.write_skills(tmp_path)
    assert not stale.exists()


def test_the_starter_is_the_default_and_is_not_itself_an_example() -> None:
    assert STARTER not in {found.name for found in EXAMPLES}
