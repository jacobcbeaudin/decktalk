"""The pipeline is one declaration, so what a run needs is read from it and never worked out twice."""

from __future__ import annotations

import pytest

from decktalk.pipeline import PIPELINE, Artifact, Outcome, Stage, required


def test_the_six_stages_are_declared_in_run_order() -> None:
    assert [stage.value for stage in Stage] == ["narrate", "cue", "record", "soundscape", "assemble", "verify"]


def test_the_pipeline_has_one_row_per_stage_in_the_same_order() -> None:
    assert [spec.stage for spec in PIPELINE] == list(Stage)


def test_every_row_says_why_it_runs_where_it_does() -> None:
    for spec in PIPELINE:
        assert spec.why.endswith("."), spec.stage


def test_every_artifact_is_written_by_at_most_one_stage() -> None:
    for artifact in Artifact:
        writers = [spec.stage for spec in PIPELINE if artifact in spec.writes]
        assert len(writers) <= 1, artifact
        assert artifact.written_by == (writers[0] if writers else None)


def test_every_artifact_is_read_or_written_by_some_stage() -> None:
    touched = {artifact for spec in PIPELINE for artifact in (*spec.reads, *spec.writes)}
    assert touched == set(Artifact)


def test_every_artifact_path_is_project_relative_and_posix() -> None:
    for artifact in Artifact:
        assert artifact.value.startswith("build/"), artifact
        assert "\\" not in artifact.value, artifact
        assert artifact.path.parts[0] == "build"


def test_an_artifact_resolves_under_a_root(tmp_path) -> None:
    assert Artifact.TAKES.under(tmp_path) == tmp_path / "build" / "narrate" / "takes.json"


def test_no_stage_reads_an_artifact_a_later_stage_writes() -> None:
    order = list(Stage)
    for spec in PIPELINE:
        for artifact in spec.reads:
            writer = artifact.written_by
            assert writer is not None, artifact
            assert order.index(writer) < order.index(spec.stage), (spec.stage, artifact)


@pytest.mark.parametrize(
    ("plan", "wanted"),
    [
        (tuple(Stage), ()),
        ((Stage.ASSEMBLE,), (Artifact.TAKES, Artifact.RECORDINGS, Artifact.SOUNDSCAPE)),
        ((Stage.VERIFY,), (Artifact.CUE_TIMES, Artifact.FINAL)),
        ((Stage.NARRATE, Stage.CUE), ()),
    ],
)
def test_a_partial_run_needs_what_its_skipped_stages_would_have_written(
    plan: tuple[Stage, ...], wanted: tuple[Artifact, ...]
) -> None:
    assert required(plan) == wanted


def test_a_span_is_inclusive_at_both_ends_and_open_at_either() -> None:
    assert Stage.span(Stage.CUE, Stage.RECORD) == (Stage.CUE, Stage.RECORD)
    assert Stage.span(None, Stage.NARRATE) == (Stage.NARRATE,)
    assert Stage.span(Stage.VERIFY, None) == (Stage.VERIFY,)
    assert Stage.span(None, None) == tuple(Stage)


def test_a_span_whose_ends_are_the_wrong_way_round_is_empty() -> None:
    assert Stage.span(Stage.VERIFY, Stage.NARRATE) == ()


def test_one_outcome_field_replaces_three_event_names() -> None:
    assert [outcome.value for outcome in Outcome] == ["ok", "skipped", "failed"]


def test_every_stage_reaches_its_own_row() -> None:
    for stage in Stage:
        assert stage.spec.stage is stage
