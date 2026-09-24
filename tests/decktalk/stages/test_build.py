"""One run of the whole pipeline: its plan, its preconditions, its stream and what stops it.

Every stage is replaced at the one attribute the facade reaches, which is the module-level function
named after the stage, so a build test opens no browser, no encoder and no voice and still runs the
real `build`. That is what the one-function convention buys: the seam a test replaces is the seam
the product uses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.errors import Cancel, InputError, NotBuiltError
from decktalk.events import Event
from decktalk.findings import Certainty, Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    AssembleResult,
    CueResult,
    Layer,
    NarrateResult,
    RecordResult,
    Result,
    SoundscapeResult,
    Spend,
    SpendState,
    StoryboardResult,
    VerifyResult,
    Voicing,
)
from decktalk.stages import assemble, cue, narrate, record, storyboard, verify
from decktalk.stages import build as build_module
from decktalk.stages import soundscape as soundscape_stage
from decktalk.stages.build import build

RUN_ID = "run-under-test"
"""The one run every test here opens, which every result it fakes carries."""

RATE = 0.30
"""What the project under test states a thousand characters of speech costs."""

TOML = """
[project]
name = "t"

[voice]
price_per_1000_characters = 0.30

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

SCRIPT = """## 1. Open

A bowl.

## 2. Close

A ball.
"""


def price(dollars: float = 0.0, *, state: SpendState = SpendState.ESTIMATE) -> Spend:
    """One stage's price, stated the way every stage of the product states one."""
    return Spend(
        state=state,
        sections=(1,),
        characters=int(dollars * 1000),
        dollars=dollars,
        ceiling_dollars=dollars,
        price_per_1000_characters=RATE,
        price_layer=Layer.PROJECT,
    )


def judged(code: Code, stage: Stage) -> Finding:
    """One judgement a faked stage hands back, stamped with the stage that made it."""
    return Finding.model_validate(
        {
            "code": code,
            "message": f"{code.name} was found while the build was under test.",
            "location": Location(where="1:a", section=1),
            "stage": stage,
        }
    )


@dataclass
class Calls:
    """Every stage the build reached, in order, with the keywords it was handed."""

    made: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [name for name, _options in self.made]

    def options(self, name: str) -> dict[str, object]:
        return next(options for called, options in self.made if called == name)


@dataclass
class Watched:
    """One run and every line it put on the stream, which is how a test reads what a build reported."""

    run: Run
    lines: list[Event] = field(default_factory=list)

    def of(self, event: str) -> list[Event]:
        return [line for line in self.lines if line.event == event]


@pytest.fixture
def inputs(tmp_path: Path) -> Inputs:
    """A two-section project with no soundscape, which is the shape most of these tests want."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    return Inputs.load(root, environ={})


@pytest.fixture
def make_run(tmp_path: Path) -> Callable[..., Watched]:
    """A run on a machine that holds nothing but a stream, with every line it emits kept."""

    def make(project: Inputs, *, voice: Voicing = Voicing.PLACEHOLDER) -> Watched:
        machine = Machine(
            environ={},
            tables={},
            config_path=tmp_path / "decktalk-machine.toml",
            cwd=project.root,
            toolchain=Toolchain(),
        )
        watched = Watched(run=Run(machine, id=RUN_ID, cancel=Cancel(), voice=voice, root=project.root))
        machine.events.subscribe(watched.lines.append)
        return watched

    return make


@pytest.fixture
def watched(inputs: Inputs, make_run: Callable[..., Watched]) -> Watched:
    return make_run(inputs)


@dataclass
class Answers:
    """What each faked stage hands back, so one test can change one stage and leave the rest alone."""

    narrate: list[Finding] = field(default_factory=list)
    cue: list[Finding] = field(default_factory=list)
    record: list[Finding] = field(default_factory=list)
    soundscape: list[Finding] = field(default_factory=list)
    assemble: list[Finding] = field(default_factory=list)
    verify: list[Finding] = field(default_factory=list)
    narrate_dollars: float = 0.0
    soundscape_dollars: float = 0.0
    storyboard_page: str | None = "build/storyboard.html"


def _results(answers: Answers) -> dict[str, Callable[[], Result]]:
    """One result per stage, each the model that stage's own command answers with."""
    return {
        "narrate": lambda: NarrateResult(
            ok=not answers.narrate,
            findings=tuple(answers.narrate),
            run=RUN_ID,
            written=(),
            voice=Voicing.PLACEHOLDER,
            sections=(),
            spend=price(answers.narrate_dollars),
            takes=Path("build/narrate/takes.json"),
            seconds=0.0,
        ),
        "cue": lambda: CueResult(
            ok=not answers.cue,
            findings=tuple(answers.cue),
            run=RUN_ID,
            written=(),
            sections=(),
            file=Path("build/cue-times.json"),
            seconds=0.0,
        ),
        "record": lambda: RecordResult(
            ok=not answers.record, findings=tuple(answers.record), run=RUN_ID, written=(), sections=(), seconds=0.0
        ),
        "soundscape": lambda: SoundscapeResult(
            ok=not answers.soundscape,
            findings=tuple(answers.soundscape),
            run=RUN_ID,
            written=(),
            items=(),
            spend=price(answers.soundscape_dollars),
            seconds=0.0,
        ),
        "assemble": lambda: AssembleResult(
            ok=not answers.assemble,
            findings=tuple(answers.assemble),
            run=RUN_ID,
            written=(),
            film=Path("build/final/t.mp4"),
            film_seconds=12.0,
            sections=(),
            loudness=None,
            seconds=0.0,
        ),
        "verify": lambda: VerifyResult(
            ok=not answers.verify,
            findings=tuple(answers.verify),
            run=RUN_ID,
            film=Path("build/final/t.mp4"),
            film_seconds=12.0,
            seconds=0.0,
        ),
        "storyboard": lambda: StoryboardResult(
            ok=True,
            run=RUN_ID,
            written=(),
            storyboard=None if answers.storyboard_page is None else Path(answers.storyboard_page),
            panels=(),
        ),
    }


@pytest.fixture
def answers() -> Answers:
    return Answers()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, answers: Answers) -> Iterator[Calls]:
    """Every stage replaced at its own module attribute, which is the seam the facade reaches."""
    seen = Calls()
    made = _results(answers)
    modules = {
        "narrate": narrate,
        "cue": cue,
        "record": record,
        "soundscape": soundscape_stage,
        "assemble": assemble,
        "verify": verify,
        "storyboard": storyboard,
    }
    for name, module in modules.items():

        def fake(_inputs: Inputs, _run: Run, _name: str = name, **options: object) -> Result:
            seen.made.append((_name, options))
            return made[_name]()

        monkeypatch.setattr(module, name, fake)
    yield seen


def test_the_plan_is_the_pipelines_own_order(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """One table declares the order, so a build never writes a second list of its own."""
    result = build(inputs, watched.run)
    assert calls.names == [stage.value for stage in Stage]
    assert [row.stage for row in result.stages] == list(Stage)
    assert {row.outcome for row in result.stages} == {Outcome.OK}


def test_stages_narrows_the_plan_to_the_span_it_names(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    result = build(inputs, watched.run, stages=[Stage.NARRATE, Stage.CUE])
    assert calls.names == ["narrate", "cue"]
    assert [row.outcome for row in result.stages] == [Outcome.OK, Outcome.OK, *[Outcome.SKIPPED] * 4]


def test_skip_removes_a_stage_the_span_would_have_run(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    build(inputs, watched.run, skip=[Stage.VERIFY])
    assert calls.names == [stage.value for stage in Stage if stage is not Stage.VERIFY]


@pytest.mark.usefixtures("calls")
def test_a_skipped_stage_still_reports_that_it_ended(inputs: Inputs, watched: Watched) -> None:
    """A renderer meets every stage of the pipeline exactly once, whether or not the run performed it."""
    build(inputs, watched.run, stages=[Stage.NARRATE])
    ended = watched.of("stage.done")
    assert [line.stage for line in ended] == list(Stage)  # type: ignore[attr-defined]
    assert [line.stage for line in watched.of("stage.start")] == [Stage.NARRATE]  # type: ignore[attr-defined]
    skipped = [line for line in ended if line.outcome is Outcome.SKIPPED]  # type: ignore[attr-defined]
    assert len(skipped) == len(Stage) - 1


def test_a_run_that_plans_no_stage_at_all_is_refused(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    with pytest.raises(InputError, match="plans no stage at all"):
        build(inputs, watched.run, skip=list(Stage))
    assert calls.names == []


def test_a_run_that_starts_past_a_missing_artifact_names_the_file_and_the_stage(
    inputs: Inputs, watched: Watched, calls: Calls
) -> None:
    """The refusal is read from the pipeline, so no stage carries a run-this-first sentence."""
    with pytest.raises(NotBuiltError) as refused:
        build(inputs, watched.run, stages=[Stage.ASSEMBLE])
    assert "build/narrate/takes.json" in str(refused.value)
    assert "decktalk narrate" in (refused.value.hint or "")
    assert calls.names == []


def test_a_project_with_no_soundscape_may_still_assemble_without_one(
    inputs: Inputs, watched: Watched, calls: Calls
) -> None:
    """The soundscape is the one artifact a project may honestly have none of."""
    (inputs.workspace.narrate_dir).mkdir(parents=True)
    inputs.workspace.takes_path.write_text("{}", encoding="utf-8")
    inputs.workspace.recordings_dir.mkdir(parents=True)
    (inputs.workspace.recordings_dir / "01.webm").write_bytes(b"")
    build(inputs, watched.run, stages=[Stage.ASSEMBLE])
    assert calls.names == ["assemble"]


def test_a_paid_run_draws_the_storyboard_before_it_narrates(
    inputs: Inputs, make_run: Callable[..., Watched], calls: Calls
) -> None:
    """The contact sheet is the checkpoint a person reads before a credit is bought."""
    watched = make_run(inputs, voice=Voicing.PAID)
    result = build(inputs, watched.run)
    assert calls.names[0] == "storyboard"
    assert calls.names[1] == "narrate"
    assert result.storyboard == Path("build/storyboard.html")


def test_a_placeholder_run_draws_no_storyboard(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    result = build(inputs, watched.run)
    assert "storyboard" not in calls.names
    assert result.storyboard is None


def test_each_stage_is_handed_the_options_it_declares(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """A stage handed a flag it does not read would accept a knob that changes nothing."""
    build(inputs, watched.run, only=[1], force=True, strict=True, loudness=False, allow_unknown=True)
    assert calls.options("narrate") == {"only": [1], "force": True, "replace_voiced": False}
    assert calls.options("cue") == {"only": [1], "allow_unknown": True}
    assert calls.options("record") == {"only": [1], "force": True}
    assert calls.options("assemble") == {"only": [1], "soundscape": True, "loudness": False, "strict": True}
    assert calls.options("verify") == {"only": [1]}


def test_a_certain_finding_stops_the_run_where_it_was_found(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """A cue whose phrase is never spoken leaves a slide that never appears, so the film is not made."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    with pytest.raises(InputError) as stopped:
        build(inputs, watched.run)
    assert "CUE_UNRESOLVED" in (stopped.value.hint or "")
    assert calls.names == ["narrate", "cue"]


def test_an_uncertain_finding_lets_the_run_carry_on(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    answers.record.append(judged(Code.PAGE_SWAP_APART, Stage.RECORD))
    assert judged(Code.PAGE_SWAP_APART, Stage.RECORD).certainty is Certainty.UNCERTAIN
    build(inputs, watched.run)
    assert calls.names[-1] == "verify"


def test_allow_unknown_lets_a_cue_no_page_declares_through(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """A deck under construction lists the cues of slides it has not drawn yet."""
    answers.cue.append(judged(Code.CUE_UNKNOWN, Stage.CUE))
    build(inputs, watched.run, allow_unknown=True)
    assert calls.names[-1] == "verify"


@pytest.mark.usefixtures("calls")
def test_the_same_cue_stops_the_run_without_that_flag(inputs: Inputs, watched: Watched, answers: Answers) -> None:
    answers.cue.append(judged(Code.CUE_UNKNOWN, Stage.CUE))
    with pytest.raises(InputError):
        build(inputs, watched.run)


def test_a_finding_an_earlier_stage_made_does_not_stop_a_later_one(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """One run carries every judgement made in it, so only the stage's own findings are weighed."""
    answers.record.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    build(inputs, watched.run)
    assert calls.names[-1] == "verify"


def test_what_verify_found_ends_the_run_rather_than_stopping_it(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    answers.verify.append(judged(Code.CUE_OFF, Stage.VERIFY))
    result = build(inputs, watched.run)
    assert calls.names[-1] == "verify"
    assert result.stages[-1].outcome is Outcome.OK


@pytest.mark.usefixtures("calls")
def test_the_spend_is_every_stage_that_priced_something_added_up(
    inputs: Inputs, watched: Watched, answers: Answers
) -> None:
    answers.narrate_dollars = 1.0
    answers.soundscape_dollars = 0.5
    result = build(inputs, watched.run)
    assert result.spend.dollars == pytest.approx(1.5)
    assert result.spend.ceiling_dollars == pytest.approx(1.5)
    assert result.spend.price_per_1000_characters == RATE


def test_a_run_that_priced_nothing_still_reports_a_spend(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """A reader that met a null there would need to know which stages price before reading zero."""
    inputs.workspace.cue_times_path.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.cue_times_path.write_text("{}", encoding="utf-8")
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"")
    result = build(inputs, watched.run, stages=[Stage.VERIFY])
    assert calls.names == ["verify"]
    assert result.spend.dollars == 0.0
    assert result.spend.state is SpendState.ESTIMATE
    assert result.spend.price_per_1000_characters == RATE


@pytest.mark.usefixtures("calls")
def test_the_film_and_the_voicing_come_back_on_the_result(inputs: Inputs, watched: Watched) -> None:
    result = build(inputs, watched.run)
    assert result.film == Path("build/final/t.mp4")
    assert result.voice is Voicing.PLACEHOLDER
    assert result.run == RUN_ID


@pytest.mark.usefixtures("calls")
def test_a_run_that_makes_no_film_reports_none(inputs: Inputs, watched: Watched) -> None:
    result = build(inputs, watched.run, stages=[Stage.NARRATE])
    assert result.film is None


def test_every_stage_of_the_pipeline_declares_the_options_it_takes() -> None:
    """A stage added to the pipeline with no row here would be called with no options at all."""
    assert set(build_module.OPTIONS) == set(Stage)
    assert set(build_module.MODULES) == set(Stage)


def test_every_artifact_of_the_pipeline_knows_where_this_project_keeps_it(inputs: Inputs) -> None:
    """`Artifact` says what a file is for and the workspace says where it is, which is one home each."""
    for artifact, name in build_module.ARTIFACTS.items():
        assert isinstance(getattr(inputs.workspace, name), Path), artifact
