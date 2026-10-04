"""Every path under the build directory, named once and agreeing with the pipeline's own table."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import take_file
from decktalk.errors import InputError
from decktalk.inputs.workspace import EVENTS_SUFFIX, Workspace
from decktalk.pipeline import Artifact
from support.links import link
from support.takes import TAKE_SUFFIX

ROOT = Path("/p")
SPACE = Workspace(
    root=ROOT, build=ROOT / "build", name="demo", suffix=TAKE_SUFFIX, takes=ROOT / "takes", score_dir=ROOT / "score"
)


@pytest.mark.parametrize("artifact", list(Artifact), ids=lambda artifact: artifact.name)
def test_every_artifact_the_pipeline_declares_moves_with_the_build_directory(artifact: Artifact) -> None:
    """`Artifact` publishes the path under the build directory, and `[project] build` may move the directory."""
    assert SPACE.of(artifact) == ROOT / "build" / artifact.value
    moved = Workspace(
        root=ROOT, build=ROOT / "out", name="demo", suffix=TAKE_SUFFIX, takes=ROOT / "takes", score_dir=ROOT / "score"
    )
    assert moved.of(artifact) == ROOT / "out" / artifact.value


def test_the_film_is_named_after_the_project() -> None:
    assert SPACE.film == ROOT / "build" / "final" / "demo.mp4"
    assert SPACE.deliverables()["film"] == SPACE.film


def test_every_work_file_moves_with_the_build_and_is_hidden_beside_the_film() -> None:
    """A work file a stage joins in `final/` follows `[project] build` and is never one a viewer opens."""
    moved = Workspace(
        root=ROOT, build=ROOT / "out", name="demo", suffix=TAKE_SUFFIX, takes=ROOT / "takes", score_dir=ROOT / "score"
    )
    assert moved.work_file("picture.mp4") == ROOT / "out" / "final" / ".demo.picture.mp4"
    assert moved.joined_music == ROOT / "out" / "score" / "music.mp3"
    assert moved.stamped_film("2000-01-01") == ROOT / "out" / "final" / "demo-2000-01-01.mp4"


def test_every_deliverable_sits_beside_the_film() -> None:
    assert {path.parent for path in SPACE.deliverables().values()} == {SPACE.final_dir}


def test_a_bought_take_lives_in_the_takes_directory_and_never_in_the_machines_store() -> None:
    assert SPACE.takes == ROOT / "takes"
    assert SPACE.takes_path == SPACE.of(Artifact.TAKES)
    shared = Workspace(
        root=ROOT,
        build=ROOT / "build",
        name="demo",
        suffix=TAKE_SUFFIX,
        takes=ROOT / "takes",
        score_dir=ROOT / "score",
        store=Path("/shared"),
    )
    assert shared.takes == ROOT / "takes"
    assert shared.takes_path == SPACE.takes_path  # the index is one project's, so a shared store never holds it


def test_a_projects_takes_dir_holds_its_takes_and_the_build_holds_its_index() -> None:
    own = Workspace(
        root=ROOT,
        build=ROOT / "build",
        name="demo",
        suffix=TAKE_SUFFIX,
        takes=ROOT / "voice",
        score_dir=ROOT / "score",
        store=Path("/shared"),
    )
    assert own.takes == ROOT / "voice"
    assert own.takes_path == ROOT / "build" / "narrate" / "takes.json", "the index is a cache, never committed"


def test_one_run_writes_one_events_file() -> None:
    assert SPACE.events_path("abc123") == SPACE.events_dir / f"abc123{EVENTS_SUFFIX}"


def test_a_section_names_its_recording_its_log_and_its_cut() -> None:
    assert SPACE.recording("03").name == "03.webm"
    assert SPACE.recording_log("03").name == "03.json"
    assert SPACE.section_video("03").name == "03.mp4"


def test_a_cut_left_by_a_renumbering_is_found_and_the_ones_still_declared_are_not(tmp_path: Path) -> None:
    space = Workspace(
        root=tmp_path,
        build=tmp_path / "build",
        name="demo",
        suffix=TAKE_SUFFIX,
        takes=tmp_path / "takes",
        score_dir=tmp_path / "score",
    )
    space.sections_dir.mkdir(parents=True)
    for name in ("01.mp4", "01.json", "02.mp4", "09.mp4", "09.json", "notes.txt"):
        (space.sections_dir / name).write_bytes(b"")
    assert [path.name for path in space.stray_videos(("01", "02"))] == ["09.json", "09.mp4"]


def test_a_project_that_has_never_been_built_has_no_stray_cut(tmp_path: Path) -> None:
    space = Workspace(
        root=tmp_path,
        build=tmp_path / "build",
        name="demo",
        suffix=TAKE_SUFFIX,
        takes=tmp_path / "takes",
        score_dir=tmp_path / "score",
    )
    assert space.stray_videos(()) == ()


def test_a_committed_take_linked_out_of_the_project_is_refused_before_a_run(tmp_path: Path) -> None:
    """A clone chose every name in its takes directory, so a link out of it is refused as one in `build/` is."""
    root = tmp_path / "proj"
    (root / "voice").mkdir(parents=True)
    (tmp_path / "secret").write_text("not a take", encoding="utf-8")
    link(root / "voice" / take_file("0123456789abcdef", TAKE_SUFFIX), tmp_path / "secret")
    space = Workspace(
        root=root, build=root / "build", name="demo", suffix=TAKE_SUFFIX, takes=root / "voice", score_dir=root / "score"
    )
    with pytest.raises(InputError, match="leads outside the takes directory"):
        space.confine()
