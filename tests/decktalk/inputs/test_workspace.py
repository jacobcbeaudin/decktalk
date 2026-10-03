"""Every path under the build directory, named once and agreeing with the pipeline's own table."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import PLACEHOLDER_PREFIX, take_file, words_file
from decktalk.errors import InputError
from decktalk.inputs.workspace import EVENTS_SUFFIX, Workspace
from decktalk.pipeline import Artifact
from support.links import link

ROOT = Path("/p")
SPACE = Workspace(root=ROOT, build=ROOT / "build", name="demo")


@pytest.mark.parametrize("artifact", list(Artifact), ids=lambda artifact: artifact.name)
def test_every_artifact_the_pipeline_declares_moves_with_the_build_directory(artifact: Artifact) -> None:
    """`Artifact` publishes the default path, and `[project] build` may move the directory under it."""
    assert SPACE.of(artifact) == ROOT.joinpath(artifact.value)
    moved = Workspace(root=ROOT, build=ROOT / "out", name="demo")
    assert moved.of(artifact) == ROOT / "out" / Path(artifact.value).relative_to("build")


def test_the_film_is_named_after_the_project() -> None:
    assert SPACE.film == ROOT / "build" / "final" / "demo.mp4"
    assert SPACE.deliverables()["film"] == SPACE.film


def test_every_deliverable_sits_beside_the_film() -> None:
    assert {path.parent for path in SPACE.deliverables().values()} == {SPACE.final_dir}


def test_the_takes_live_in_the_build_directory_unless_a_key_moves_them() -> None:
    assert SPACE.takes_dir == SPACE.narrate_dir
    assert SPACE.takes_path == SPACE.of(Artifact.TAKES)
    shared = Workspace(root=ROOT, build=ROOT / "build", name="demo", shared=Path("/shared/takes"))
    assert shared.takes_dir == Path("/shared/takes")
    assert shared.takes_path == SPACE.takes_path  # the index is one project's, so a shared store never holds it


def test_a_projects_takes_dir_holds_its_takes_and_its_index() -> None:
    own = Workspace(root=ROOT, build=ROOT / "build", name="demo", takes=ROOT / "voice", shared=Path("/shared"))
    assert own.takes_dir == ROOT / "voice"
    assert own.takes_path == ROOT / "voice" / "takes.json"
    assert own.take_places == (ROOT / "voice", Path("/shared"), ROOT / "build" / "narrate")


def _hold(directory: Path, digest: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / take_file(digest)).write_bytes(b"take")
    (directory / words_file(digest)).write_text("{}", encoding="utf-8")


def test_a_take_is_looked_for_in_the_project_then_the_machine_then_the_build(tmp_path: Path) -> None:
    space = Workspace(
        root=tmp_path, build=tmp_path / "build", name="demo", takes=tmp_path / "voice", shared=tmp_path / "shared"
    )
    digest = "0123456789abcdef"
    assert space.holding(digest) is None
    assert space.take_path(digest) == tmp_path / "voice" / take_file(digest)
    _hold(space.narrate_dir, digest)
    assert space.holding(digest) == space.narrate_dir
    _hold(tmp_path / "shared", digest)
    assert space.holding(digest) == tmp_path / "shared"
    _hold(tmp_path / "voice", digest)
    assert space.holding(digest) == tmp_path / "voice"
    assert space.words_path(digest) == tmp_path / "voice" / words_file(digest)


def test_a_placeholder_lives_in_the_build_directory_wherever_the_takes_are(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path, build=tmp_path / "build", name="demo", takes=tmp_path / "voice")
    digest = f"{PLACEHOLDER_PREFIX}0123456789"
    assert space.take_path(digest) == space.narrate_dir / take_file(digest)


def test_one_run_writes_one_events_file() -> None:
    assert SPACE.events_path("abc123") == SPACE.events_dir / f"abc123{EVENTS_SUFFIX}"


def test_a_section_names_its_recording_its_log_and_its_cut() -> None:
    assert SPACE.recording("03").name == "03.webm"
    assert SPACE.recording_log("03").name == "03.json"
    assert SPACE.section_video("03").name == "03.mp4"


def test_a_cut_left_by_a_renumbering_is_found_and_the_ones_still_declared_are_not(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path, build=tmp_path / "build", name="demo")
    space.sections_dir.mkdir(parents=True)
    for name in ("01.mp4", "01.json", "02.mp4", "09.mp4", "09.json", "notes.txt"):
        (space.sections_dir / name).write_bytes(b"")
    assert [path.name for path in space.stray_cuts(("01", "02"))] == ["09.json", "09.mp4"]


def test_a_project_that_has_never_been_built_has_no_stray_cut(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path, build=tmp_path / "build", name="demo")
    assert space.stray_cuts(()) == ()


def test_a_committed_take_linked_out_of_the_project_is_refused_before_a_run(tmp_path: Path) -> None:
    """A clone chose every name in its take directory, so a link out of it is refused as one in `build/` is."""
    root = tmp_path / "proj"
    (root / "voice").mkdir(parents=True)
    (tmp_path / "secret").write_text("not a take", encoding="utf-8")
    link(root / "voice" / take_file("0123456789abcdef"), tmp_path / "secret")
    space = Workspace(root=root, build=root / "build", name="demo", takes=root / "voice")
    with pytest.raises(InputError, match="leads outside the take directory"):
        space.confine()
