"""The run every call opens: its stream, its stages, its result, and the gate a spend passes."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from decktalk.errors import ApprovalRequired, Cancel, Cancelled, ErrorCode, InputError
from decktalk.events import Event, RunDone, RunStart, StageDone, StageStart
from decktalk.findings import (
    Code,
    Finding,
    Location,
    Severity,
    Threshold,
)
from decktalk.machine.run import Run
from decktalk.pipeline import Outcome, Stage
from decktalk.results import BillingBasis, Layer, StatusResult
from decktalk.stages.cost import total
from support.costs import a_cost
from support.runs import a_machine

# ---- the run ------------------------------------------------------------------------------


def test_every_line_of_a_run_carries_its_run_and_counts_from_zero(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run:
        run.note("one")
        run.note("two")
    assert [line.event for line in seen] == ["run.start", "run.log", "run.log", "run.done"]
    assert {line.run for line in seen} == {run.id}
    assert [line.seq for line in seen] == [0, 1, 2, 3]


def test_a_run_that_fails_closes_as_failed_and_lets_the_failure_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), pytest.raises(ZeroDivisionError), here._run():
        raise ZeroDivisionError
    assert isinstance(seen[-1], RunDone) and seen[-1].outcome is Outcome.FAILED
    assert seen[-1].error is not None and seen[-1].error.code is ErrorCode.INTERNAL
    assert seen[-1].error.message == "ZeroDivisionError: "


def test_a_refused_run_names_its_refusal_on_its_last_line(tmp_path: Path) -> None:
    """The events file is read after the process is gone, so its last line has to say why the run failed."""
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with pytest.raises(InputError), here._run(root=tmp_path, events_dir=events) as run:
        raise InputError("cues.json is not JSON.", hint="Fix cues.json.")
    last = RunDone.model_validate_json((events / f"{run.id}.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last.outcome is Outcome.FAILED
    assert last.error is not None
    assert (last.error.code, last.error.message, last.error.hint) == (
        ErrorCode.INPUT,
        "cues.json is not JSON.",
        "Fix cues.json.",
    )


def test_a_run_says_on_its_last_line_how_many_lines_its_bounded_file_left_out(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with here._run(root=tmp_path, events_dir=events, max_bytes=1) as run:
        for number in range(5):
            logging.getLogger("decktalk.media.ffmpeg").debug("call %d", number)
    lines = (events / f"{run.id}.jsonl").read_text(encoding="utf-8").splitlines()
    last = RunDone.model_validate_json(lines[-1])
    assert last.dropped == 5 and [line for line in lines if '"run.log"' in line] == []


def test_a_finished_run_carries_no_error(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run():
        pass
    assert isinstance(seen[-1], RunDone) and seen[-1].error is None


@pytest.mark.parametrize("stop", [Cancelled("The caller stopped this run."), KeyboardInterrupt()])
def test_a_cancelled_or_interrupted_run_ends_as_stopped_rather_than_failed(tmp_path: Path, stop: BaseException) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with (
        here.events.subscribe(seen.append),
        pytest.raises(type(stop)),
        here._run() as run,
        run.section(Stage.RECORD, 1),
    ):
        raise stop
    section = next(line for line in seen if line.event == "section.done")
    assert getattr(section, "outcome", None) is Outcome.STOPPED
    assert isinstance(seen[-1], RunDone) and seen[-1].outcome is Outcome.STOPPED
    assert seen[-1].error is not None and seen[-1].error.code is ErrorCode.CANCELLED


@pytest.mark.parametrize("outcome", [Outcome.RAN, Outcome.KEPT])
def test_a_block_that_says_how_it_ended_is_reported_with_that_outcome(tmp_path: Path, outcome: Outcome) -> None:
    """A block that found its work done already says so, and one that says nothing ran."""
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run, run.section(Stage.NARRATE, 1) as ending:
        if outcome is not Outcome.RAN:
            ending.outcome = outcome
    section = next(line for line in seen if line.event == "section.done")
    assert getattr(section, "outcome", None) is outcome


def test_a_run_with_a_project_writes_its_own_file_and_says_where(tmp_path: Path) -> None:
    """One file per run, so a watch loop beside a build by hand cannot overwrite the other's lines."""
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with here._run(root=tmp_path, events_dir=events) as run:
        run.note("hello")
    written = events / f"{run.id}.jsonl"
    assert written.exists()
    assert "hello" in written.read_text(encoding="utf-8")


def test_a_run_says_the_path_its_own_lines_are_going_to(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run(root=tmp_path, events_dir=tmp_path / "build" / "events") as run:
        pass
    assert isinstance(seen[0], RunStart)
    assert seen[0].events_file == Path(f"build/events/{run.id}.jsonl")


def test_a_machine_run_holds_no_project_so_it_writes_no_file(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run():
        pass
    assert isinstance(seen[0], RunStart) and seen[0].events_file is None


def test_the_oldest_event_files_are_pruned_before_a_new_run_opens(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    events.mkdir(parents=True)
    for name in ("a", "b", "c"):
        (events / f"{name}.jsonl").write_text("{}\n", encoding="utf-8")
    with here._run(root=tmp_path, events_dir=events, keep_runs=1):
        pass
    assert len(list(events.glob("*.jsonl"))) == 2  # the one kept, and this run's own


def test_a_stage_opens_and_closes_on_the_stream(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run, run.stage(Stage.NARRATE, index=1, count=2):
        pass
    started = next(line for line in seen if isinstance(line, StageStart))
    done = next(line for line in seen if isinstance(line, StageDone))
    assert (started.stage, started.index, started.count) == (Stage.NARRATE, 1, 2)
    assert done.outcome is Outcome.RAN


def test_a_stage_that_raises_closes_as_failed(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run:
        with pytest.raises(ValueError, match="no"), run.stage(Stage.RECORD):
            raise ValueError("no")
    assert next(line for line in seen if isinstance(line, StageDone)).outcome is Outcome.FAILED


def test_a_cancelled_run_stops_at_the_next_section_boundary(tmp_path: Path) -> None:
    """A stage checks between sections, so a cancelled run leaves whole artifacts rather than half of one."""
    here = a_machine(tmp_path)
    cancel = Cancel()
    with here._run(cancel=cancel) as run:
        with run.section(Stage.RECORD, 1):
            cancel.cancel()
        with pytest.raises(Cancelled), run.section(Stage.RECORD, 2):
            pass


def test_a_result_carries_the_run_the_judgements_and_the_files(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(root=tmp_path) as run:
        run.wrote(tmp_path / "build" / "final" / "a.mp4")
        run.wrote(tmp_path / "build" / "final" / "a.mp4")
        result = run.result(StatusResult, name="t", script=Path("script.md"), cues_file=Path("cues.json"), sections=())
    assert result.run == run.id
    assert result.ok


def test_an_error_is_what_makes_a_result_not_ok(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    error = Finding(code=Code.CUE_UNRESOLVED, message="x", location=Location(where="cues.json"))
    warning = Finding(code=Code.PAGE_SWAP_APART, message="y", location=Location(where="deck/index.html"))
    assert error.severity is Severity.ERROR and warning.severity is Severity.WARNING
    with here._run() as run:
        run.found(warning)
        assert run.result(StatusResult, name="t", script=Path("s"), cues_file=Path("c"), sections=()).ok
        run.found(error)
        assert not run.result(StatusResult, name="t", script=Path("s"), cues_file=Path("c"), sections=()).ok


ERROR = Finding(code=Code.CUE_UNRESOLVED, message="x", location=Location(where="cues.json"))
WARNING = Finding(code=Code.PAGE_SWAP_APART, message="y", location=Location(where="deck/index.html"))


@pytest.mark.parametrize(
    ("threshold", "found", "passes"),
    [
        (Threshold(), (WARNING,), True),
        (Threshold(), (ERROR,), False),
        (Threshold(stop_on=Severity.WARNING), (WARNING,), False),
        (Threshold(stop_on=None), (ERROR, WARNING), True),
        (Threshold(allow=frozenset({Code.CUE_UNRESOLVED})), (ERROR,), True),
        (Threshold(stop_on=Severity.WARNING, allow=frozenset({Code.CUE_UNRESOLVED})), (ERROR, WARNING), False),
    ],
    ids=["warning", "error", "fail-on-warning", "off", "allowed", "allowed-but-warning"],
)
def test_a_result_is_ok_exactly_when_nothing_reaches_the_threshold_its_run_carries(
    tmp_path: Path, threshold: Threshold, found: tuple[Finding, ...], passes: bool
) -> None:
    assert ERROR.severity is Severity.ERROR and WARNING.severity is Severity.WARNING
    run = Run(a_machine(tmp_path), id="r", cancel=Cancel(), threshold=threshold)
    result = run.result(
        StatusResult,
        findings=found,
        name="t",
        script=Path("s"),
        cues_file=Path("c"),
        sections=(),
    )
    assert result.ok is passes
    assert threshold.fails(found) is not passes


# ---- the spend gate -------------------------------------------------------------------------


def test_nothing_is_bought_unless_the_run_may_spend(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run() as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_cost(0.42, 0.42))
    assert refused.value.code is ErrorCode.APPROVAL
    assert "--spend" in (refused.value.hint or "")
    assert "--no-spend" in (refused.value.hint or "")


@pytest.mark.parametrize("max_cost", [None, 0.0])
def test_a_free_voice_is_never_asked_for_approval_even_by_a_run_that_may_not_spend(
    tmp_path: Path, max_cost: float | None
) -> None:
    """Spend gates money, so a voice that declares it bills nothing passes the gate whatever the run may spend."""
    here = a_machine(tmp_path)
    free = a_cost(0.0, 0.0, billing=BillingBasis.FREE)
    assert free.free
    with here._run(spend=False, max_cost=max_cost) as run:
        assert run.approve(free) == free


def test_a_run_that_may_not_spend_refuses_a_price_of_zero_from_a_voice_that_bills(tmp_path: Path) -> None:
    """A zero price on a voice that bills is an estimate, and money is the gate's question."""
    here = a_machine(tmp_path)
    with here._run() as run, pytest.raises(ApprovalRequired):
        run.approve(a_cost(0.0, 0.0))


def test_a_cap_lets_a_free_voice_through_with_no_rate_stated(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    free = a_cost(0.0, 0.0, billing=BillingBasis.FREE, layer=Layer.DEFAULT)
    with here._run(spend=True, max_cost=0.0) as run:
        assert run.approve(free) == free


def test_a_cap_is_refused_for_a_voice_that_declares_no_bill_with_what_to_declare(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    undeclared = a_cost(0.0, 0.0, billing=BillingBasis.UNDECLARED, layer=Layer.DEFAULT)
    with here._run(spend=True, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(undeclared)
    assert "declares no bill" in str(refused.value)
    assert "per character, per second or free" in (refused.value.hint or "")


def test_a_cap_over_an_undeclared_sound_beside_a_paid_voice_names_the_scores_provider(tmp_path: Path) -> None:
    """The refusal names the stage whose provider declares no bill, never the voice that declares one."""
    here = a_machine(tmp_path)
    undeclared = a_cost(0.0, 0.0, billing=BillingBasis.UNDECLARED, stage=Stage.SCORE)
    whole = total([a_cost(0.12, 0.12), undeclared])
    with here._run(spend=True, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve_whole(whole)
    assert "the score's provider declares no bill" in str(refused.value)


def test_a_refusal_states_the_price_in_the_one_sentence_every_surface_uses(tmp_path: Path) -> None:
    """A run whose error part is zero must not be said to spend $0.00, which its ceiling contradicts."""
    here = a_machine(tmp_path)
    unmatched = a_cost(0.0, 0.3)
    with here._run() as run, pytest.raises(ApprovalRequired) as unvoiced:
        run.approve(unmatched)
    with here._run(spend=True, max_cost=0.1) as run, pytest.raises(ApprovalRequired) as capped:
        run.approve(unmatched)
    for refused in (unvoiced, capped):
        assert str(refused.value).startswith(unmatched.sentence)
        assert "$0.00" not in str(refused.value)


def test_a_paid_run_inside_its_ceiling_goes_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=1.0) as run:
        assert run.approve(a_cost(0.42, 0.9)).dollars == 0.42


def test_the_ceiling_is_compared_against_the_most_a_run_can_cost(tmp_path: Path) -> None:
    """A provider charges one request at a time, so a cap that stopped a run halfway would be a lie."""
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=0.5) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_cost(0.42, 0.9))
    assert "0.90" in str(refused.value)


@pytest.mark.parametrize(
    ("ceilings", "cap", "passes"),
    [
        ((0.9,), 0.9, True),
        ((0.9,), 0.89, False),
        ((0.5, 0.5), 1.0, True),
        ((0.5, 0.51), 1.0, False),
        ((0.1, 0.2), 0.3, True),
    ],
    ids=["at-the-cap", "a-cent-over", "summed-to-the-cap", "summed-a-cent-over", "summed-in-floats"],
)
def test_the_ceiling_lets_a_run_cost_exactly_the_cap_and_not_a_cent_more(
    tmp_path: Path, ceilings: tuple[float, ...], cap: float, passes: bool
) -> None:
    """`--max-cost` is the most a run may cost, so the cap itself passes and a cent over it is refused.

    The approvals are summed in dollars to the cent, so 0.1 and 0.2 make the 0.3 they say.
    """
    with a_machine(tmp_path)._run(spend=True, max_cost=cap) as run:
        *before, last = (a_cost(ceiling, ceiling) for ceiling in ceilings)
        for cost in before:
            run.approve(cost)
        if passes:
            assert run.approve(last) == last
        else:
            with pytest.raises(ApprovalRequired):
                run.approve(last)


def test_the_ceiling_caps_everything_one_run_approves_and_not_each_approval(tmp_path: Path) -> None:
    """A build approves its takes and then its sounds, and `--max-cost` is the most the whole run may cost."""
    here = a_machine(tmp_path)
    sounds = a_cost(0.9, 0.9, billing=BillingBasis.PER_SECOND)
    with here._run(spend=True, max_cost=1.0) as run:
        run.approve(a_cost(0.9, 0.9))
        with pytest.raises(ApprovalRequired) as refused:
            run.approve(sounds)
    said = str(refused.value)
    assert said.startswith(sounds.sentence)
    assert "$1.80" in said and "$1.00" in said, said
    assert "kept" in said, "a run refused partway says what it already bought stays"
    with here._run(spend=True, max_cost=1.0) as again:
        assert again.approve(sounds) == sounds, "the cap is per run, so the next run starts from nothing"


def test_a_cap_is_refused_while_nobody_has_stated_the_price(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_cost(0.42, 0.9, layer=Layer.DEFAULT))
    assert "elevenlabs.dollars_per_1000_characters" in (refused.value.hint or "")


def test_every_priced_request_reaches_the_stream_before_it_is_judged(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run(spend=True) as run:
        run.approve(a_cost(0.42, 0.42))
    assert [line.event for line in seen if line.event == "cost.priced"] == ["cost.priced"]
