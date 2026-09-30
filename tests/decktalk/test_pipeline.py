"""The pipeline is one declaration, so what a run needs is read from it and never worked out twice."""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

import pytest

from decktalk import pipeline
from decktalk.pipeline import NEEDS, PIPELINE, Artifact, Outcome, Stage, downstream, required


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


def test_the_next_step_names_the_stage_that_writes_the_artifact() -> None:
    """A refusal about a missing file reads its advice from the one table that says who writes it."""
    for artifact in Artifact:
        writer = artifact.written_by
        assert writer is not None, artifact
        assert artifact.next_step.startswith(f"Run `decktalk {writer.value}` first"), artifact


def test_the_one_stage_that_spends_names_the_way_to_spend_nothing() -> None:
    """A reader stopped by a missing take index should not have to look up the free way to make one."""
    assert (
        Artifact.TAKES.next_step == "Run `decktalk narrate` first, or `decktalk narrate --no-voice` to spend nothing."
    )
    assert Artifact.RECORDINGS.next_step == "Run `decktalk record` first."


def test_the_declared_order_is_one_the_graph_admits() -> None:
    """Each stage is ready, in the graph's own sense, by the time the declared order reaches it."""
    graph = TopologicalSorter(NEEDS)
    graph.prepare()
    ready: set[Stage] = set()
    for stage in Stage:
        ready |= set(graph.get_ready())
        assert stage in ready, f"{stage.value} runs before a stage whose artifact it reads"
        graph.done(stage)


def test_the_graph_is_the_table_read_as_edges() -> None:
    assert NEEDS == {
        Stage.NARRATE: frozenset(),
        Stage.CUE: {Stage.NARRATE},
        Stage.RECORD: {Stage.CUE},
        Stage.SOUNDSCAPE: {Stage.NARRATE},
        Stage.ASSEMBLE: {Stage.NARRATE, Stage.RECORD, Stage.SOUNDSCAPE},
        Stage.VERIFY: {Stage.CUE, Stage.ASSEMBLE},
    }


@pytest.mark.parametrize(
    ("changed", "stale"),
    [
        ((Stage.NARRATE,), (Stage.CUE, Stage.RECORD, Stage.SOUNDSCAPE, Stage.ASSEMBLE, Stage.VERIFY)),
        ((Stage.CUE,), (Stage.RECORD, Stage.ASSEMBLE, Stage.VERIFY)),
        ((Stage.SOUNDSCAPE,), (Stage.ASSEMBLE, Stage.VERIFY)),
        ((Stage.ASSEMBLE,), (Stage.VERIFY,)),
        ((Stage.VERIFY,), ()),
        ((Stage.RECORD, Stage.ASSEMBLE), (Stage.VERIFY,)),
    ],
)
def test_a_change_reaches_every_stage_that_reads_from_it_however_far(
    changed: tuple[Stage, ...], stale: tuple[Stage, ...]
) -> None:
    assert downstream(changed) == stale


def test_a_table_that_reads_in_a_circle_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(pipeline.NEEDS, Stage.NARRATE, frozenset({Stage.VERIFY}))
    with pytest.raises(CycleError):
        downstream((Stage.CUE,))


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


def test_one_outcome_field_replaces_four_event_names() -> None:
    assert [outcome.value for outcome in Outcome] == ["ok", "kept", "skipped", "stopped", "failed"]


def test_every_stage_reaches_its_own_row() -> None:
    for stage in Stage:
        assert stage.spec.stage is stage
