"""One run of the whole pipeline: its plan, its preconditions, its stream and what stops it.

Every stage is replaced at the one attribute the facade reaches, which is the module-level function
named after the stage, so a build test opens no browser, no encoder and no voice and still runs the
real `build`. That is what the one-function convention buys: the seam a test replaces is the seam
the product uses.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from decktalk.cli import main
from decktalk.cli.session import Session
from decktalk.errors import ApprovalRequired, ErrorCode, InputError, NotBuiltError
from decktalk.events import CostPriced, RunLog, StageDone, StageStart
from decktalk.findings import Code, Finding, Location, Severity, Threshold
from decktalk.inputs import Inputs
from decktalk.machine.run import Run
from decktalk.media import audio
from decktalk.pipeline import Outcome, Stage
from decktalk.project import Project
from decktalk.results import (
    AssembleResult,
    BillingBasis,
    Cost,
    CostState,
    CueResult,
    Layer,
    NarrateResult,
    RecordResult,
    Result,
    ScoreResult,
    StageCost,
    StoryboardResult,
    VerifyResult,
    money,
)
from decktalk.settings import MACHINE_FILE_VARIABLE
from decktalk.stages import assemble, cue, narrate, record, storyboard, verify
from decktalk.stages import score as score_stage
from decktalk.stages.build import build
from decktalk.stages.kept import read_kept
from decktalk.stages.table import CALLS
from support.fakes import FakeVoice
from support.logs import decisions
from support.projects import load_project
from support.runs import RUN_ID, Watched

RATE = 0.30
"""What the project under test states a thousand characters of speech costs."""

TOML = """
[project]
name = "t"

[elevenlabs]
dollars_per_1000_characters = 0.30

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


VOICED = {"DECKTALK_VOICE_ID": "voice-under-test"}
"""What a machine that names the voice hands a project, so a take on disk can be matched to it."""


def priced(stage: Stage, **fields: Any) -> Cost:
    """One stage's price from its fields, with the one row that stage reports."""
    return Cost(**fields, stages=(StageCost(stage=stage, **fields),))


def price(dollars: float = 0.0, *, state: CostState = CostState.ESTIMATE, stage: Stage = Stage.NARRATE) -> Cost:
    """One stage's price, stated the way every stage of the product states one."""
    return priced(
        stage,
        state=state,
        sections=(1,),
        characters=int(dollars * 1000),
        seconds=0.0,
        dollars=dollars,
        ceiling_dollars=dollars,
        billing=BillingBasis.PER_CHARACTER,
        dollars_per_1000_characters=RATE,
        dollars_per_minute=0.0,
        price_key=None,
        averaged=False,
        price_layer=Layer.PROJECT,
    )


SOUND_RATE = 0.12
"""Dollars per minute of sound audio, which a score's own price is quoted at."""


def sound_price(seconds: float) -> Cost:
    """The score's price, which is billed per second of audio rather than per character."""
    dollars = round(seconds * SOUND_RATE / 60, 2)
    return priced(
        Stage.SCORE,
        state=CostState.ESTIMATE,
        sections=(1,),
        characters=0,
        seconds=seconds,
        dollars=dollars,
        ceiling_dollars=dollars,
        billing=BillingBasis.PER_SECOND,
        dollars_per_1000_characters=0.0,
        dollars_per_minute=SOUND_RATE,
        price_key="score.music.dollars_per_minute",
        averaged=False,
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
    """A two-section project with no score, which is the shape most of these tests want."""
    return load_project(tmp_path / "proj", TOML, script=SCRIPT)


@dataclass
class Answers:
    """What each faked stage hands back, so one test can change one stage and leave the rest alone."""

    narrate: list[Finding] = field(default_factory=list)
    cue: list[Finding] = field(default_factory=list)
    record: list[Finding] = field(default_factory=list)
    score: list[Finding] = field(default_factory=list)
    assemble: list[Finding] = field(default_factory=list)
    verify: list[Finding] = field(default_factory=list)
    narrate_dollars: float = 0.0
    score_dollars: float = 0.0
    narrate_cost: Cost | None = None
    """The narration's whole price, when a test needs one other than `narrate_dollars` at the speech rate."""
    score_cost: Cost | None = None
    """The score's whole price, when a test needs one other than `score_dollars` at the speech rate."""
    storyboard_page: str | None = "build/storyboard.html"
    film: bytes | None = None
    """What the faked assemble writes as the film, or None when it writes nothing, as most tests want."""


def _replace(monkeypatch: pytest.MonkeyPatch, module: object, name: str, fake: Callable[..., Result]) -> None:
    """`fake` in place of a stage's row in the table, or of a module's function for a call that is no stage."""
    if name in {stage.value for stage in Stage}:
        monkeypatch.setitem(CALLS, Stage(name), replace(CALLS[Stage(name)], call=fake))
    else:
        monkeypatch.setattr(module, name, fake)


def _results(answers: Answers) -> dict[str, Callable[[], Result]]:
    """One result per stage, each the model that stage's own command answers with."""
    return {
        "narrate": lambda: NarrateResult(
            ok=not answers.narrate,
            findings=tuple(answers.narrate),
            run=RUN_ID,
            written=(),
            spend=False,
            sections=(),
            cost=answers.narrate_cost or price(answers.narrate_dollars),
            takes=Path("build/narrate/takes.json"),
            elapsed_seconds=0.0,
        ),
        "cue": lambda: CueResult(
            ok=not answers.cue,
            findings=tuple(answers.cue),
            run=RUN_ID,
            written=(),
            sections=(),
            file=Path("build/cue-times.json"),
            elapsed_seconds=0.0,
        ),
        "record": lambda: RecordResult(
            ok=not answers.record,
            findings=tuple(answers.record),
            run=RUN_ID,
            written=(),
            sections=(),
            elapsed_seconds=0.0,
        ),
        "score": lambda: ScoreResult(
            ok=not answers.score,
            findings=tuple(answers.score),
            run=RUN_ID,
            written=(),
            spend=False,
            items=(),
            cost=answers.score_cost or price(answers.score_dollars, stage=Stage.SCORE),
            elapsed_seconds=0.0,
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
            elapsed_seconds=0.0,
        ),
        "verify": lambda: VerifyResult(
            ok=not answers.verify,
            findings=tuple(answers.verify),
            run=RUN_ID,
            film=Path("build/final/t.mp4"),
            film_seconds=12.0,
            elapsed_seconds=0.0,
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
        "score": score_stage,
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

        _replace(monkeypatch, module, name, fake)
    yield seen


def test_the_plan_is_the_pipelines_own_order(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """One table declares the order, so a build never writes a second list of its own."""
    result = build(inputs, watched.run)
    assert calls.names == [stage.value for stage in Stage]
    assert [row.stage for row in result.stages] == list(Stage)
    assert {row.outcome for row in result.stages} == {Outcome.RAN}


def test_stages_narrows_the_plan_to_the_span_it_names(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    result = build(inputs, watched.run, stages=[Stage.NARRATE, Stage.CUE])
    assert calls.names == ["narrate", "cue"]
    assert [row.outcome for row in result.stages] == [Outcome.RAN, Outcome.RAN, *[Outcome.SKIPPED] * 4]


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


def test_a_run_of_the_score_alone_needs_no_take_index(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """The sound is planned from the project file alone, so no narration has to exist before it is bought."""
    build(inputs, watched.run, stages=[Stage.SCORE])
    assert calls.names == ["score"]


def test_a_project_with_no_score_may_still_assemble_without_one(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """The score is the one artifact a project may honestly have none of."""
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


def _with_a_score(inputs: Inputs) -> Inputs:
    """The same project with one generated ambience bed declared and nothing generated yet."""
    toml = inputs.root / "decktalk.toml"
    toml.write_text(TOML + '\n[score.ambience]\nprompt = "a quiet room"\n', encoding="utf-8")
    inputs.workspace.narrate_dir.mkdir(parents=True)
    inputs.workspace.takes_path.write_text("{}", encoding="utf-8")
    inputs.workspace.recordings_dir.mkdir(parents=True)
    for section in inputs.document.page_sections:
        inputs.workspace.recording(section.key).write_bytes(b"")
    return Inputs.load(inputs.root, environ={})


def test_a_run_that_skips_the_score_assembles_without_it(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """One switch decides the sound: a run told to skip the stage neither needs its files nor mixes them."""
    declared = _with_a_score(inputs)
    build(declared, watched.run, stages=[Stage.ASSEMBLE], skip=[Stage.SCORE])
    assert calls.options("assemble")["score"] is False


def test_a_run_that_does_not_skip_the_score_needs_it(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    declared = _with_a_score(inputs)
    with pytest.raises(NotBuiltError) as refused:
        build(declared, watched.run, stages=[Stage.ASSEMBLE])
    assert "decktalk score" in (refused.value.hint or "")
    assert calls.names == []


@pytest.mark.parametrize("spend", [True, False], ids=["may spend", "may not spend"])
def test_the_library_build_never_draws_the_storyboard(
    inputs: Inputs, make_run: Callable[..., Watched], calls: Calls, spend: bool
) -> None:
    """The storyboard is the command line's checkpoint before it spends, so a build runs the six stages alone."""
    build(inputs, make_run(inputs, spend=spend).run)
    assert calls.names == [stage.value for stage in Stage]


@pytest.mark.parametrize(
    ("stages", "refused"),
    [(None, True), ((Stage.NARRATE, Stage.CUE), False)],
    ids=["a plan that opens pages", "a plan that opens none"],
)
def test_a_run_that_may_spend_is_refused_an_untrusted_page_before_its_first_stage(
    tmp_path: Path,
    make_run: Callable[..., Watched],
    calls: Calls,
    stages: tuple[Stage, ...] | None,
    refused: bool,
) -> None:
    """The launch's own rule is asked before narrate buys, of a plan that reaches a stage that opens a page."""
    sealed = load_project(tmp_path / "sealed", TOML, script=SCRIPT, machine={"record": {"page_policy": "untrusted"}})
    run = make_run(sealed, spend=True).run
    if refused:
        with pytest.raises(ApprovalRequired, match="untrusted page"):
            build(sealed, run, stages=stages)
        assert calls.names == []
    else:
        build(sealed, run, stages=stages)
        assert calls.names == ["narrate", "cue"]


def test_each_stage_is_handed_the_options_it_declares(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """A stage handed a flag it does not read would accept a switch that changes nothing."""
    build(inputs, watched.run, only=[1], force=True, strict=True, loudness=False)
    assert calls.options("narrate") == {"only": [1], "force": True, "replace_voiced": False}
    assert calls.options("cue") == {"only": [1]}
    assert calls.options("record") == {"only": [1], "force": True}
    assert calls.options("score") == {"only": [1], "replace_score": False}, "force reached the stage that buys sound"
    assert calls.options("assemble") == {"only": [1], "score": True, "loudness": False, "strict": True}
    assert calls.options("verify") == {"only": [1]}


PAYING_TOML = (
    TOML.replace('scene = "1"', 'scene = "1"\nwith_ambience = true', 1)
    + """
[score.ambience]
prompt = "a quiet room"
"""
)
"""The two-section project with one bought sound beside its two takes, so a build buys from both stages."""


@dataclass
class Purchases:
    """Every take and every sound a build bought, counted at the two seams that are paid."""

    voice: FakeVoice
    sounds: list[dict[str, object]] = field(default_factory=list)

    def effect(self, body: Mapping[str, object], *, output_format: str) -> bytes:  # noqa: ARG002
        self.sounds.append(dict(body))
        return b"sound"

    def music(self, body: Mapping[str, object], *, output_format: str) -> bytes:  # noqa: ARG002
        self.sounds.append(dict(body))
        return b"music"

    @property
    def counts(self) -> tuple[int, int]:
        """(takes bought, sounds bought), which is what a run cost read as two numbers."""
        return len(self.voice.requests), len(self.sounds)


@pytest.fixture
def purchases(monkeypatch: pytest.MonkeyPatch, fake_voice: FakeVoice, answers: Answers) -> Purchases:
    """A build whose two paying stages are real and buy from fakes, with every other stage faked.

    The voice and the sound service are the paid seams, so they are what is counted, and the stages
    between them are replaced because no test of what is bought needs a browser or an encoder.
    """
    bought = Purchases(voice=fake_voice)
    monkeypatch.setattr(score_stage, "client_for", lambda _run, _inputs: bought)
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)
    made = _results(answers)
    for name, module in {"cue": cue, "record": record, "assemble": assemble, "verify": verify}.items():
        _replace(monkeypatch, module, name, lambda _inputs, _run, _name=name, **_options: made[_name]())
    monkeypatch.setattr(storyboard, "storyboard", lambda _inputs, _run, **_options: made["storyboard"]())
    return bought


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_forced_build_that_may_spend_buys_no_take_and_no_sound_again(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """`force` rebuilds what is free, so it never buys again what a take or the sound ledger holds."""
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    build(paying, make_run(paying, spend=True).run)
    assert purchases.counts == (2, 1)
    build(paying, make_run(paying, spend=True).run, force=True)
    assert purchases.counts == (2, 1), "a forced build bought again what the project already held"


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_build_after_the_build_directory_is_deleted_buys_no_take_and_no_sound_again(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """Every paid record lives in a folder the project keeps, so deleting the build directory costs nothing."""
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    build(paying, make_run(paying, spend=True).run)
    assert purchases.counts == (2, 1)
    shutil.rmtree(paying.workspace.build)
    build(paying, make_run(paying, spend=True).run)
    assert purchases.counts == (2, 1), "a build bought again what the project already held"


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_build_told_to_replace_voiced_takes_still_buys_them_and_no_sound(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """`replace_voiced` is the one way a take is bought again, and it reaches no sound."""
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    build(paying, make_run(paying, spend=True).run)
    build(paying, make_run(paying, spend=True).run, force=True, replace_voiced=True)
    assert purchases.counts == (4, 1)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_build_that_may_not_spend_makes_the_film_with_silence_where_an_unbought_sound_would_be(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """An unbought sound warns and never fails the film, and a caller that fails on any finding stops on it."""
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    result = build(paying, make_run(paying, spend=False).run)
    assert purchases.counts == (0, 0)
    assert result.stopped_at is None
    assert result.ok
    assert Code.SOUND_MISSING in {found.code for found in result.findings}
    any_finding = Threshold(stop_on=Severity.WARNING, allow=frozenset({Code.TAKE_MISSING}))
    strict = build(paying, make_run(paying, spend=False, threshold=any_finding).run)
    assert strict.stopped_at is Stage.SCORE
    assert not strict.ok
    assert purchases.counts == (0, 0)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_build_told_to_replace_the_score_buys_each_sound_again_and_no_take(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """`replace_score` is the one way a bought sound is bought again, and it reaches no take."""
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    build(paying, make_run(paying, spend=True).run)
    build(paying, make_run(paying, spend=True).run, force=True, replace_score=True)
    assert purchases.counts == (2, 2)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_neither_replace_flag_buys_anything_without_spend(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    paying = load_project(tmp_path / "paying", PAYING_TOML, script=SCRIPT, environ=VOICED)
    build(paying, make_run(paying, spend=True).run)
    build(paying, make_run(paying, spend=False).run, replace_voiced=True, replace_score=True)
    assert purchases.counts == (2, 1)


PRICED_TOML = PAYING_TOML.replace("dollars_per_1000_characters = 0.30", "dollars_per_1000_characters = 60.0").replace(
    'prompt = "a quiet room"', 'prompt = "a quiet room"\ndollars_per_minute = 2.16'
)
"""The paying project at rates where its takes and its sound each cost under a dollar and together more."""

CAP = 1.0
"""A ceiling each of the priced project's stages fits under and the two of them together do not."""


TAKES, SOUND = 0.84, 0.90
"""The most the priced project's two takes and its one sound can each cost, both under `CAP` and together over it."""


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_ceiling_refuses_the_whole_build_before_it_buys_anything(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases
) -> None:
    """Takes under the cap and a sound under the cap still make a run over it, so nothing at all is bought."""
    paying = load_project(tmp_path / "paying", PRICED_TOML, script=SCRIPT, environ=VOICED)
    with pytest.raises(ApprovalRequired) as refused:
        build(paying, make_run(paying, spend=True, max_cost=CAP).run)
    assert purchases.counts == (0, 0)
    said = str(refused.value)
    assert money(TAKES + SOUND) in said and money(CAP) in said, said
    assert "kept" not in said, "a run refused before it bought anything has nothing to keep"
    assert paying.takes() is None


@pytest.mark.parametrize("terminal", [True, False], ids=["approved on a terminal", "given --spend"])
def test_the_command_line_draws_the_storyboard_once_before_a_build_that_spends(
    tmp_path: Path, inputs: Inputs, calls: Calls, monkeypatch: pytest.MonkeyPatch, terminal: bool
) -> None:
    """The checkpoint draws the sheet a person looks at before money goes, and the build draws none of its own."""
    for name, value in {**VOICED, MACHINE_FILE_VARIABLE: str(tmp_path / "machine.toml")}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(Session, "asks", property(lambda _self: terminal))
    monkeypatch.setattr(Session, "confirm", lambda _self, _question, default=False: True)
    monkeypatch.setattr(Project, "price", lambda _self, **_options: price(1.0))
    code = main(["-p", str(inputs.root), "build", *([] if terminal else ["--spend"])])
    assert code == 0
    assert calls.names == ["storyboard", *(stage.value for stage in Stage)]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_the_command_line_refuses_the_whole_build_with_its_json_refusal(
    tmp_path: Path, purchases: Purchases, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`build --spend --max-cost` and `Project.build(spend=True, max_cost=...)` open one run, so one cap holds."""
    paying = load_project(tmp_path / "paying", PRICED_TOML, script=SCRIPT, environ=VOICED)
    for name, value in {**VOICED, MACHINE_FILE_VARIABLE: str(tmp_path / "machine.toml")}.items():
        monkeypatch.setenv(name, value)
    code = main(["-p", str(paying.root), "--json", "build", "--spend", "--max-cost", str(CAP)])
    assert purchases.counts == (0, 0)
    refused = json.loads(capsys.readouterr().out)
    assert code == ErrorCode.APPROVAL.exit_code
    assert refused["error"]["code"] == ErrorCode.APPROVAL.value
    assert money(TAKES + SOUND) in refused["error"]["message"] and money(CAP) in refused["error"]["message"]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_stage_that_asks_for_more_than_the_build_was_priced_at_is_refused_and_keeps_what_was_bought(
    tmp_path: Path, make_run: Callable[..., Watched], purchases: Purchases, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate keeps the run's own total, so a sound priced low up front cannot carry the run over the cap."""
    paying = load_project(tmp_path / "paying", PRICED_TOML, script=SCRIPT, environ=VOICED)
    real = score_stage.price
    monkeypatch.setattr(
        score_stage,
        "price",
        lambda *_a, **_k: real(paying).model_copy(
            update={"seconds": 0.0, "sections": (), "dollars": 0.0, "ceiling_dollars": 0.0, "stages": ()}
        ),
    )
    watched = make_run(paying, spend=True, max_cost=CAP)
    with pytest.raises(ApprovalRequired) as refused:
        build(paying, watched.run)
    assert purchases.counts == (2, 0)
    assert [line.cost.ceiling_dollars for line in watched.of(CostPriced)] == [TAKES, SOUND]
    said = str(refused.value)
    assert money(TAKES + SOUND) in said and money(CAP) in said and "kept" in said, said
    kept = paying.takes()
    assert kept is not None and len(kept.voiced_sections) == 2, "the takes it bought are kept"


def test_an_error_stops_the_run_where_it_was_found(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """A cue whose phrase is never spoken leaves a slide that never appears, so the film is not made."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    result = build(inputs, watched.run)
    assert calls.names == ["narrate", "cue"]
    assert result.ok is False
    assert result.stopped_at is Stage.CUE
    assert [found.code for found in result.findings] == [Code.CUE_UNRESOLVED]
    assert [row.outcome for row in result.stages] == [Outcome.RAN, Outcome.RAN, *[Outcome.SKIPPED] * 4]
    assert result.film is None


def test_a_run_that_stops_says_so_in_a_sentence(
    inputs: Inputs, watched: Watched, answers: Answers, calls: Calls
) -> None:
    """The stream says which stage stopped the run and how many findings did it, counted in words."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    build(inputs, watched.run)
    said = [line.message for line in watched.of(RunLog)]
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
    watched = make_run(inputs, spend=True)
    result = build(inputs, watched.run)
    assert result.stopped_at is Stage.CUE
    assert result.cost.dollars == pytest.approx(1.0)


def test_a_warning_lets_the_run_carry_on(inputs: Inputs, watched: Watched, answers: Answers, calls: Calls) -> None:
    answers.record.append(judged(Code.PAGE_SWAP_APART, Stage.RECORD))
    assert judged(Code.PAGE_SWAP_APART, Stage.RECORD).severity is Severity.WARNING
    result = build(inputs, watched.run)
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None


def test_a_threshold_of_any_finding_stops_on_a_warning(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """`--fail-on warning` means the run stops where the command would fail, which is at any finding."""
    answers.record.append(judged(Code.PAGE_SWAP_APART, Stage.RECORD))
    result = build(inputs, make_run(inputs, threshold=Threshold(stop_on=Severity.WARNING)).run)
    assert calls.names == ["narrate", "cue", "record"]
    assert result.stopped_at is Stage.RECORD
    assert result.ok is False


@pytest.mark.parametrize(
    ("stop_on", "allow", "stops_at"),
    [
        pytest.param(Severity.ERROR, (), None, id="default carries on"),
        pytest.param(Severity.WARNING, (), Stage.NARRATE, id="any stops at narrate"),
        pytest.param(Severity.WARNING, (Code.TAKE_MISSING,), None, id="any with it allowed carries on"),
    ],
)
def test_a_missing_take_builds_the_film_unless_the_threshold_says_any(
    inputs: Inputs,
    make_run: Callable[..., Watched],
    answers: Answers,
    calls: Calls,
    stop_on: Severity,
    allow: tuple[Code, ...],
    stops_at: Stage | None,
) -> None:
    """A placeholder where a take is missing is a draft, so the default threshold builds the film past it."""
    answers.narrate.append(judged(Code.TAKE_MISSING, Stage.NARRATE))
    result = build(inputs, make_run(inputs, threshold=Threshold(stop_on=stop_on, allow=frozenset(allow))).run)
    assert result.stopped_at is stops_at
    assert (calls.names[-1] == Stage.VERIFY.value) is (stops_at is None)
    assert result.ok is (stops_at is None)
    assert [found.code for found in result.findings] == [Code.TAKE_MISSING]


def test_no_threshold_runs_every_stage_whatever_it_finds(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """`--fail-on never` lets a build run through to verify, which then measures what was made."""
    answers.cue.append(judged(Code.CUE_UNRESOLVED, Stage.CUE))
    result = build(inputs, make_run(inputs, threshold=Threshold(stop_on=None)).run)
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None
    assert result.ok is True


def test_an_allowed_code_lets_a_cue_no_page_declares_through(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """A deck under construction lists the cues of slides it has not drawn yet."""
    answers.cue.append(judged(Code.CUE_UNKNOWN, Stage.CUE))
    build(inputs, make_run(inputs, threshold=Threshold(allow=frozenset({Code.CUE_UNKNOWN}))).run)
    assert calls.names[-1] == "verify"
    assert "allow_unknown" not in calls.options("cue")


def test_any_allowed_code_is_forgiven_the_same_way(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls
) -> None:
    """`--allow` means what it means on every other command, not only for one code."""
    answers.record.append(judged(Code.RECORD_BLACK, Stage.RECORD))
    assert judged(Code.RECORD_BLACK, Stage.RECORD).severity is Severity.ERROR
    result = build(inputs, make_run(inputs, threshold=Threshold(allow=frozenset({Code.RECORD_BLACK}))).run)
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
    assert result.stages[-1].outcome is Outcome.RAN


@pytest.mark.parametrize(
    ("stop_on", "code"),
    [
        pytest.param(Severity.ERROR, Code.CUE_OFF, id="an error at the error threshold"),
        pytest.param(Severity.WARNING, Code.CUE_THIN_CHANGE, id="a warning at the any threshold"),
    ],
)
def test_verify_judging_on_the_threshold_fails_the_run_and_never_stops_it(
    inputs: Inputs, make_run: Callable[..., Watched], answers: Answers, calls: Calls, stop_on: Severity, code: Code
) -> None:
    """Verify is the last stage, so a finding there that would stop any other stage fails the run instead."""
    found = judged(code, Stage.VERIFY)
    assert Threshold(stop_on=stop_on).reaches(found)
    answers.verify.append(found)
    watched = make_run(inputs, threshold=Threshold(stop_on=stop_on))
    result = build(inputs, watched.run)
    assert calls.names[-1] == "verify"
    assert result.stopped_at is None
    assert result.ok is False
    assert [line.message for line in watched.of(RunLog)] == []


@pytest.mark.usefixtures("calls")
def test_the_spend_is_every_stage_that_priced_something_added_up(
    inputs: Inputs, watched: Watched, answers: Answers
) -> None:
    answers.narrate_dollars = 1.0
    answers.score_dollars = 0.5
    result = build(inputs, watched.run)
    assert result.cost.dollars == pytest.approx(1.5)
    assert result.cost.ceiling_dollars == pytest.approx(1.5)
    assert result.cost.dollars_per_1000_characters == RATE


FREE_AND_VOICED = priced(
    Stage.NARRATE,
    **price(0.0, state=CostState.CHARGED).model_dump(exclude={"stages"})
    | {"billing": BillingBasis.FREE, "dollars_per_1000_characters": 0.0, "characters": 39},
)
"""A free voice's narration that voiced 39 characters, which a run that may not spend still voices."""


@pytest.mark.usefixtures("calls")
@pytest.mark.parametrize(
    ("narrated", "dollars", "said"),
    [
        (FREE_AND_VOICED, 0.0, "This run voiced 39 characters for nothing, because the voice is free."),
        (price(0.30, state=CostState.CHARGED), 0.30, "This run spent $0.30"),
    ],
    ids=["free-voice", "paid-voice"],
)
def test_a_charged_total_counts_only_the_stages_that_had_leave_to_buy(
    inputs: Inputs, watched: Watched, answers: Answers, narrated: Cost, dollars: float, said: str
) -> None:
    """A score that only priced what it would buy is never reported as spent beside a narration that bought."""
    answers.narrate_cost = narrated
    answers.score_cost = sound_price(60.0)
    result = build(inputs, watched.run)
    assert result.cost.state is CostState.CHARGED
    assert result.cost.dollars == result.cost.ceiling_dollars == dollars
    assert result.cost.sentence.startswith(said)


def test_a_run_that_priced_nothing_still_reports_a_spend(inputs: Inputs, watched: Watched, calls: Calls) -> None:
    """A reader that met a null there would need to know which stages price before reading zero."""
    inputs.workspace.cue_times_path.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.cue_times_path.write_text("{}", encoding="utf-8")
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"")
    result = build(inputs, watched.run, stages=[Stage.VERIFY])
    assert calls.names == ["verify"]
    assert result.cost.dollars == 0.0
    assert result.cost.state is CostState.ESTIMATE
    assert result.cost.dollars_per_1000_characters == RATE


@pytest.mark.usefixtures("calls")
def test_the_film_and_whether_it_could_spend_come_back_on_the_result(inputs: Inputs, watched: Watched) -> None:
    result = build(inputs, watched.run)
    assert result.film == Path("build/final/t.mp4")
    assert result.spend is False
    assert result.run == RUN_ID


@pytest.mark.usefixtures("calls")
def test_a_run_that_makes_no_film_reports_none(inputs: Inputs, watched: Watched) -> None:
    result = build(inputs, watched.run, stages=[Stage.NARRATE])
    assert result.film is None


def test_every_stage_of_the_pipeline_declares_the_options_it_takes() -> None:
    """A stage added to the pipeline with no row in the table would be called with no options at all."""
    assert set(CALLS) == set(Stage)


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
