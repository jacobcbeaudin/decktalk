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

from decktalk.errors import InputError, NotBuiltError
from decktalk.events import Log, StageDone, StageStart
from decktalk.findings import Certainty, Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Run
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
from decktalk.stages.status import read_kept
from support.logs import decisions
from support.projects import load_project
from support.runs import RUN_ID, Watched

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


@pytest.fixture
def inputs(tmp_path: Path) -> Inputs:
    """A two-section project with no soundscape, which is the shape most of these tests want."""
    return load_project(tmp_path / "proj", TOML, script=SCRIPT)


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
    film: bytes | None = None
    """What the faked assemble writes as the film, or None when it writes nothing, as most tests want."""


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
            answer = made[_name]()
            if _name == "assemble" and answers.film is not None:
                _inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
                _inputs.workspace.film.write_bytes(answers.film)
            # A real stage reports each judgement through its run as it makes it, which is what
            # fills the build's own result, so the fake does the same.
            for found in answer.findings:
                _run.found(found)
            return answer

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
    ended = watched.of(StageDone)
    assert [line.stage for line in ended] == list(Stage)
    assert [line.stage for line in watched.of(StageStart)] == [Stage.NARRATE]
    skipped = [line for line in ended if line.outcome is Outcome.SKIPPED]
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
    for section in inputs.document.page_sections:
        inputs.workspace.recording(section.key).write_bytes(b"")
    build(inputs, watched.run, stages=[Stage.ASSEMBLE])
    assert calls.names == ["assemble"]


def test_a_run_that_starts_past_a_partial_recording_is_refused(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """A build and the status report read one rule, so neither calls half a record stage finished."""
    inputs.workspace.narrate_dir.mkdir(parents=True)
    inputs.workspace.takes_path.write_text("{}", encoding="utf-8")
    inputs.workspace.recordings_dir.mkdir(parents=True)
    first = inputs.document.page_sections[0]
    inputs.workspace.recording(first.key).write_bytes(b"")
    with pytest.raises(NotBuiltError) as refused:
        build(inputs, watched.run, stages=[Stage.ASSEMBLE])
    assert "decktalk record" in (refused.value.hint or "")
    assert calls.names == []


def _with_a_soundscape(inputs: Inputs) -> Inputs:
    """The same project with one generated ambience bed declared and nothing generated yet."""
    toml = inputs.root / "decktalk.toml"
    toml.write_text(TOML + '\n[soundscape.ambience]\ntext = "a quiet room"\n', encoding="utf-8")
    inputs.workspace.narrate_dir.mkdir(parents=True)
    inputs.workspace.takes_path.write_text("{}", encoding="utf-8")
    inputs.workspace.recordings_dir.mkdir(parents=True)
    for section in inputs.document.page_sections:
        inputs.workspace.recording(section.key).write_bytes(b"")
    return Inputs.load(inputs.root, environ={})


def test_a_run_that_skips_the_soundscape_assembles_without_it(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """One knob decides the sound: a run told to skip the stage neither needs its files nor mixes them."""
    declared = _with_a_soundscape(inputs)
    build(declared, watched.run, stages=[Stage.ASSEMBLE], skip=[Stage.SOUNDSCAPE])
    assert calls.options("assemble")["soundscape"] is False


def test_a_run_that_does_not_skip_the_soundscape_needs_it(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    declared = _with_a_soundscape(inputs)
    with pytest.raises(NotBuiltError) as refused:
        build(declared, watched.run, stages=[Stage.ASSEMBLE])
    assert "decktalk soundscape" in (refused.value.hint or "")
    assert calls.names == []


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
    build(inputs, watched.run, only=[1], force=True, strict=True, loudness=False, allow=[Code.CUE_UNKNOWN])
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
    result = build(inputs, watched.run)
    assert calls.names == ["narrate", "cue"]
    assert result.ok is False
    assert result.stopped_at is Stage.CUE
    assert [found.code for found in result.findings] == [Code.CUE_UNRESOLVED]
    assert [row.outcome for row in result.stages] == [Outcome.OK, Outcome.OK, *[Outcome.SKIPPED] * 4]
    assert result.film is None


def test_a_run_that_stops_says_so_in_a_sentence(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """The stream says which stage stopped the run and how many findings did it, counted in words."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    build(inputs, watched.run)
    said = [line.message for line in watched.of(Log)]
    assert said == ["Cue made 1 finding that the build stops on, so the build stopped before record rather "
                    "than carry it into the film."]  # fmt: skip
    assert "(s)" not in said[0]
    assert calls.names == ["narrate", "cue"]


@pytest.mark.usefixtures("calls")
def test_a_stopped_run_keeps_what_narrate_already_charged(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers
) -> None:
    """A paid narrate followed by a cue finding still reports the money it spent."""
    answers.narrate_dollars = 1.0
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    watched = make_run(inputs, voice=Voicing.PAID)
    result = build(inputs, watched.run)
    assert result.stopped_at is Stage.CUE
    assert result.spend.dollars == pytest.approx(1.0)


def test_an_uncertain_finding_lets_the_run_carry_on(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    answers.record.append(judged(Code.PAGE_SWAP_APART, Stage.RECORD))
    assert judged(Code.PAGE_SWAP_APART, Stage.RECORD).certainty is Certainty.UNCERTAIN
    result = build(inputs, watched.run)
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None


def test_a_threshold_of_any_finding_stops_on_an_uncertain_one(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """`--fail-on any` means the run stops where the command would fail, which is at any finding."""
    answers.record.append(judged(Code.PAGE_SWAP_APART, Stage.RECORD))
    result = build(inputs, watched.run, stop_on=Certainty.UNCERTAIN)
    assert calls.names == ["narrate", "cue", "record"]
    assert result.stopped_at is Stage.RECORD
    assert result.ok is False


def test_no_threshold_runs_every_stage_whatever_it_finds(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """`--fail-on never` lets a build run through to verify, which then measures what was made."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    result = build(inputs, watched.run, stop_on=None)
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None
    assert result.ok is True


def test_an_allowed_code_lets_a_cue_no_page_declares_through(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """A deck under construction lists the cues of slides it has not drawn yet."""
    answers.cue.append(judged(Code.CUE_UNKNOWN, Stage.CUE))
    build(inputs, watched.run, allow=[Code.CUE_UNKNOWN])
    assert calls.names[-1] == "verify"
    assert calls.options("cue")["allow_unknown"] is True


def test_any_allowed_code_is_forgiven_the_same_way(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """`--allow` means what it means on every other command, not only for one code."""
    answers.record.append(judged(Code.PAGE_BLACK, Stage.RECORD))
    assert judged(Code.PAGE_BLACK, Stage.RECORD).certainty is Certainty.CERTAIN
    result = build(inputs, watched.run, allow=[Code.PAGE_BLACK])
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None
    assert result.ok is True


@pytest.mark.usefixtures("calls")
def test_the_same_cue_stops_the_run_without_that_flag(inputs: Inputs, watched: Watched, answers: Answers) -> None:
    answers.cue.append(judged(Code.CUE_UNKNOWN, Stage.CUE))
    assert build(inputs, watched.run).stopped_at is Stage.CUE


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


# ---- keeping what has not changed --------------------------------------------------------------


def _built_once(inputs: Inputs, answers: Answers, make_run: Callable[..., Watched]) -> None:
    """A first build whose assemble writes a film and whose verify finds a late cue."""
    answers.film = b"film"
    answers.verify.append(judged(Code.CUE_OFF, Stage.VERIFY))
    build(inputs, make_run(inputs).run)


def test_an_unchanged_build_keeps_assemble_and_verify(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """Nothing either stage reads has moved, so the film and its measurement already stand."""
    _built_once(inputs, answers, make_run)
    calls.made.clear()
    again = make_run(inputs)
    result = build(inputs, again.run)
    assert "assemble" not in calls.names
    assert "verify" not in calls.names
    outcomes = {row.stage: row.outcome for row in result.stages}
    assert outcomes[Stage.ASSEMBLE] is Outcome.KEPT
    assert outcomes[Stage.VERIFY] is Outcome.KEPT
    assert result.film == Path("build/final/t.mp4")
    # What verify found is still true of the film, so the kept run reports it again.
    assert [found.code for found in result.findings] == [Code.CUE_OFF]
    kept = [line for line in again.of(StageDone) if line.outcome is Outcome.KEPT]
    assert [line.stage for line in kept] == [Stage.ASSEMBLE, Stage.VERIFY]


def test_every_kept_or_remade_stage_says_why(
    inputs: Inputs,
    make_run: Callable[..., Watched],
    answers: Answers,
    calls: Calls,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An author who expected a kept assemble learns which input moved from one token rather than a guess."""
    with caplog.at_level("DEBUG", logger="decktalk"):
        _built_once(inputs, answers, make_run)
        assert decisions(caplog, "assemble") == [(False, "no-record")]
        caplog.clear()
        build(inputs, make_run(inputs).run)
        assert decisions(caplog, "assemble") == [(True, "unchanged")]
        assert decisions(caplog, "verify") == [(True, "unchanged")]
        caplog.clear()
        build(inputs, make_run(inputs).run, force=True)
        assert decisions(caplog, "assemble") == [(False, "forced")]
        caplog.clear()
        inputs.script_path.write_text(SCRIPT.replace("A ball.", "A ball rolls."), encoding="utf-8")
        build(inputs, make_run(inputs).run)
        assert decisions(caplog, "assemble") == [(False, "key-changed")]
    del calls


def test_force_measures_again(inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls) -> None:
    _built_once(inputs, answers, make_run)
    calls.made.clear()
    build(inputs, make_run(inputs).run, force=True)
    assert calls.names[-2:] == ["assemble", "verify"]


def test_a_changed_input_assembles_and_measures_again(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    _built_once(inputs, answers, make_run)
    calls.made.clear()
    inputs.script_path.write_text(SCRIPT.replace("A ball.", "A ball rolls."), encoding="utf-8")
    build(inputs, make_run(inputs).run)
    assert calls.names[-2:] == ["assemble", "verify"]


def test_a_film_rewritten_outside_the_build_is_made_again(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls, caplog: pytest.LogCaptureFixture
) -> None:
    """A stage run on its own rewrites the film without the record, so the record no longer vouches for it."""
    _built_once(inputs, answers, make_run)
    calls.made.clear()
    inputs.workspace.film.write_bytes(b"another film")
    caplog.clear()
    with caplog.at_level("DEBUG", logger="decktalk"):
        build(inputs, make_run(inputs).run)
    assert "assemble" in calls.names
    assert decisions(caplog, "assemble") == [(False, "outputs-changed")]


def test_a_new_film_is_measured_again_even_when_its_inputs_are_old(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """Verify is kept on the film's own bytes, so a film assembled with other options is measured."""
    _built_once(inputs, answers, make_run)
    calls.made.clear()
    answers.film = b"a louder film"
    build(inputs, make_run(inputs).run, loudness=False)
    assert calls.names[-2:] == ["assemble", "verify"]


def test_a_run_that_makes_no_film_keeps_nothing(
    inputs: Inputs, watched: Watched, calls: Calls, caplog: pytest.LogCaptureFixture
) -> None:
    """An assemble that left no film behind has nothing a later build could keep."""
    build(inputs, watched.run)
    calls.made.clear()
    caplog.clear()
    with caplog.at_level("DEBUG", logger="decktalk"):
        build(inputs, watched.run)
    assert calls.names[-2:] == ["assemble", "verify"]
    assert decisions(caplog, "verify") == [(False, "film-missing")]


def test_a_run_that_assembles_and_stops_before_verify_keeps_no_measurement(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """Verify reads what assemble writes, so a new film leaves the last measurement describing another one."""
    _built_once(inputs, answers, make_run)
    assert read_kept(inputs).verify is not None
    inputs.script_path.write_text(SCRIPT.replace("A ball.", "A ball rolls."), encoding="utf-8")
    build(inputs, make_run(inputs).run, stages=Stage.span(None, Stage.ASSEMBLE))
    assert calls.names[-1] == "assemble"
    kept = read_kept(inputs)
    assert kept.assemble is not None
    assert kept.verify is None
