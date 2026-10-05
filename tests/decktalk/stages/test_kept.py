"""The record a build keeps of its last assemble and verify, and whether the film on disk still stands."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import file_digest
from decktalk.inputs import Inputs
from decktalk.pipeline import Artifact
from decktalk.stages import kept
from support.pages import SCENE_ONE
from support.projects import load_project

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
"""
"""One page section, which is enough for a film to be assembled from."""

OPTIONS = {"only": None}
"""The options a whole build passes both stages, which the record keeps beside each digest."""


def a_film(tmp_path: Path) -> Inputs:
    """A project with a film on disk and the record of the assemble that wrote it."""
    inputs = load_project(tmp_path, TOML, page=SCENE_ONE)
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"film")
    film = inputs.relative(inputs.workspace.film).as_posix()
    made = kept.assemble_digest(inputs, OPTIONS)
    kept.Kept(
        assemble=kept.KeptStage(digest=made, options=OPTIONS, outputs={film: file_digest(inputs.workspace.film)})
    ).write(inputs.workspace.kept_path)
    return inputs


def test_every_artifact_the_pipeline_declares_says_when_it_is_built() -> None:
    """A stage added to the pipeline reaches the build and the report, so a new artifact may not be left out."""
    assert set(kept.BUILT) == set(Artifact)


def test_the_film_the_last_build_assembled_still_stands(tmp_path: Path) -> None:
    inputs = a_film(tmp_path)
    record = kept.read_kept(inputs)
    assert kept.assembled(inputs, record) == kept.assemble_digest(inputs, OPTIONS)


def test_a_film_rewritten_outside_the_build_no_longer_stands(tmp_path: Path) -> None:
    """A stage run on its own rewrites the film without the record, which a keeping build must not trust."""
    inputs = a_film(tmp_path)
    inputs.workspace.film.write_bytes(b"another film")
    assert kept.assembled(inputs, kept.read_kept(inputs)) is None


def test_a_record_this_version_cannot_read_keeps_nothing(tmp_path: Path) -> None:
    """Keeping nothing costs one assemble and one verify and is never wrong."""
    inputs = a_film(tmp_path)
    inputs.workspace.kept_path.write_text("{not json", encoding="utf-8")
    assert kept.read_kept(inputs) == kept.Kept()
