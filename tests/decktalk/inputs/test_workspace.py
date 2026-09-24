"""Every path under the build directory, named once and agreeing with the pipeline's own table."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.inputs.workspace import EVENTS_SUFFIX, Workspace
from decktalk.pipeline import Artifact

ROOT = Path("/p")
SPACE = Workspace(root=ROOT, build=ROOT / "build", name="demo")


@pytest.mark.parametrize(
    ("artifact", "path"),
    [
        (Artifact.TAKES, SPACE.takes_path),
        (Artifact.CUE_TIMES, SPACE.cue_times_path),
        (Artifact.RECORDINGS, SPACE.recordings_dir),
        (Artifact.SOUNDSCAPE, SPACE.soundscape_dir),
        (Artifact.FINAL, SPACE.final_dir),
    ],
    ids=lambda value: getattr(value, "name", ""),
)
def test_every_artifact_the_pipeline_declares_is_the_path_this_module_names(artifact: Artifact, path: Path) -> None:
    """Two declarations of one path can disagree, so this holds them equal rather than trusting them."""
    assert artifact.under(ROOT) == path


def test_the_film_is_named_after_the_project() -> None:
    assert SPACE.film == ROOT / "build" / "final" / "demo.mp4"
    assert SPACE.deliverables()["film"] == SPACE.film


def test_every_deliverable_sits_beside_the_film() -> None:
    assert {path.parent for path in SPACE.deliverables().values()} == {SPACE.final_dir}


def test_the_takes_live_in_the_build_directory_unless_a_key_moves_them() -> None:
    assert SPACE.takes_dir == SPACE.narrate_dir
    moved = Workspace(root=ROOT, build=ROOT / "build", name="demo", takes=Path("/shared/takes"))
    assert moved.takes_dir == Path("/shared/takes")
    assert moved.takes_path == SPACE.takes_path  # the index is one project's, wherever the takes are


def test_one_run_writes_one_events_file() -> None:
    assert SPACE.events_path("abc123") == SPACE.events_dir / f"abc123{EVENTS_SUFFIX}"


def test_a_section_names_its_recording_its_log_and_its_cut() -> None:
    assert SPACE.recording("03").name == "03.webm"
    assert SPACE.recording_log("03").name == "03.json"
    assert SPACE.section_video("03").name == "03.mp4"


def test_a_cut_left_by_a_renumbering_is_found_and_the_ones_still_declared_are_not(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path, build=tmp_path / "build", name="demo")
    space.sections_dir.mkdir(parents=True)
    for name in ("01.mp4", "02.mp4", "09.mp4", "notes.txt"):
        (space.sections_dir / name).write_bytes(b"")
    assert [path.name for path in space.stray_section_videos(("01", "02"))] == ["09.mp4"]


def test_a_project_that_has_never_been_built_has_no_stray_cut(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path, build=tmp_path / "build", name="demo")
    assert space.stray_section_videos(()) == ()
