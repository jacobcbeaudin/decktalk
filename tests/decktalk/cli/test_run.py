"""The eight commands that move a project forward, each asserted on what it asked the library for."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.cli import run as commands
from decktalk.cli.options import FailOn
from decktalk.cli.session import Globals, Session
from decktalk.errors import ErrorCode
from decktalk.events import RunStart
from decktalk.findings import Certainty, Code
from decktalk.pipeline import Stage
from decktalk.results import (
    ApplyResult,
    AssembleResult,
    Billing,
    BuildResult,
    CheckResult,
    ClipResult,
    CueResult,
    FixOutcome,
    NarrateResult,
    RecordResult,
    SoundscapeResult,
    Spend,
    VerifyResult,
)
from support.spends import a_spend

from .conftest import ANSWERS, Fake, finding

NARRATE = NarrateResult(ok=True, run="r", spending=False, sections=(), spend=a_spend(), seconds=1.0)
CUE = CueResult(ok=True, run="r", sections=(), seconds=1.0)
RECORD = RecordResult(ok=True, run="r", sections=(), seconds=1.0)
SOUNDSCAPE = SoundscapeResult(ok=True, run="r", items=(), spend=a_spend(), seconds=1.0)
ASSEMBLE = AssembleResult(ok=True, run="r", film="build/final/demo.mp4", film_seconds=64.0, sections=(), seconds=1.0)
VERIFY = VerifyResult(ok=True, run="r", film="build/final/demo.mp4", film_seconds=64.0, seconds=1.0)
MOVING = {
    "narrate": NARRATE,
    "cue": CUE,
    "record": RECORD,
    "soundscape": SOUNDSCAPE,
    "assemble": ASSEMBLE,
    "build": ANSWERS["build"],
}
"""The answer each moving command's fake gives."""


@pytest.mark.parametrize(
    ("argv", "keyword", "expected"),
    [
        (("narrate", "--no-spend"), "spend", False),  # never buys
        (("cue", "--section", "2"), "only", (2,)),
        (("record", "--section", "1,3-4"), "only", (1, 3, 4)),
        (("soundscape", "--no-spend"), "spend", False),  # the spending flags are shared
        (("assemble", "--skip", "soundscape"), "soundscape", False),  # the one stage it can leave out
        (
            ("build", "--no-spend", "--from", "record", "--to", "assemble"),
            "stages",
            (Stage.RECORD, Stage.SOUNDSCAPE, Stage.ASSEMBLE),
        ),
        (("build", "--no-spend"), "stages", None),  # neither end runs the whole pipeline
    ],
    ids=["narrate", "cue", "record", "soundscape", "assemble", "build-span", "build-whole"],
)
def test_a_stage_flag_reaches_the_library_as_its_keyword(run, project, argv, keyword, expected) -> None:
    made = project(**{argv[0]: MOVING[argv[0]]})
    assert run(*argv).exit_code == 0
    assert made.called(argv[0])[keyword] == expected


def test_narrate_with_spend_buys_without_asking(run, project) -> None:
    made = project(narrate=NARRATE)
    run("narrate", "--spend", "--max-cost", "5")
    asked = made.called("narrate")
    assert asked["spend"] is True
    assert asked["max_cost"] == 5


@pytest.mark.parametrize("tty", [True, False], ids=["terminal", "no-terminal"])
def test_no_spend_never_prices_never_prompts_and_never_buys(run, project, tty: bool) -> None:
    made = project(narrate=NARRATE)
    ran = run("narrate", "--no-spend", tty=tty)
    assert ran.exit_code == 0, ran.err
    assert "Spend that now?" not in ran.err
    assert made.called("narrate")["spend"] is False
    assert [name for name, _, _ in made.calls if name == "check"] == []


def test_an_unset_spend_with_no_terminal_refuses_and_names_both_flags(run, project) -> None:
    made = project(narrate=NARRATE, check=_priced(a_spend()))
    ran = run("narrate")
    assert ran.exit_code == ErrorCode.APPROVAL.exit_code
    assert "decktalk narrate --spend" in ran.err
    assert "decktalk narrate --no-spend" in ran.err
    assert [name for name, _, _ in made.calls if name == "narrate"] == []


@pytest.mark.parametrize("tty", [True, False], ids=["terminal", "no-terminal"])
def test_an_unset_spend_with_nothing_to_buy_runs_without_asking(run, project, tty: bool) -> None:
    made = project(narrate=NARRATE, check=_priced(a_spend(0.0, 0.0, sections=())))
    ran = run("narrate", tty=tty)
    assert ran.exit_code == 0, ran.err
    assert "Spend that now?" not in ran.err
    assert made.called("narrate")["spend"] is False


@pytest.mark.parametrize("tty", [True, False], ids=["terminal", "no-terminal"])
def test_an_unset_spend_on_a_voice_that_bills_nothing_runs_without_asking(run, project, tty: bool) -> None:
    free = a_spend(0.0, 0.0, sections=(1, 2), billing=Billing.FREE)
    made = project(narrate=NARRATE, check=_priced(free))
    ran = run("narrate", tty=tty)
    assert ran.exit_code == 0, ran.err
    assert "Spend that now?" not in ran.err
    assert made.called("narrate")["spend"] is True


def test_an_unset_soundscape_is_priced_by_what_it_would_buy(run, project, monkeypatch) -> None:
    """A soundscape whose items are all bought asks nothing, whatever the narration would cost."""
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    made = project(soundscape=SOUNDSCAPE, check=_priced(a_spend()))
    ran = run("soundscape")
    assert ran.exit_code == 0, ran.err
    assert [name for name, _, _ in made.calls] == ["soundscape"]
    assert made.called("soundscape")["spend"] is False


def test_an_unset_soundscape_with_sounds_to_buy_and_no_terminal_refuses(run, project, monkeypatch) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend())
    made = project(soundscape=SOUNDSCAPE)
    ran = run("soundscape")
    assert ran.exit_code == ErrorCode.APPROVAL.exit_code
    assert "decktalk soundscape --no-spend" in ran.err
    assert made.calls == []


@pytest.mark.parametrize("command", ["narrate", "build"])
def test_an_unset_run_told_to_rebuild_with_nothing_to_buy_asks_nothing_and_buys_nothing(
    run, project, monkeypatch, command: str
) -> None:
    """`--force` rebuilds what is free, so with every take and sound held there is nothing to approve."""
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    made = project(**{command: MOVING[command]}, check=_priced(a_spend(0.0, 0.0, sections=())))
    ran = run(command, "--force")
    assert ran.exit_code == 0, ran.err
    assert made.called(command)["spend"] is False
    assert made.called(command)["force"] is True


@pytest.mark.parametrize("command", ["narrate", "build"])
def test_an_unset_run_told_to_replace_voiced_takes_asks_even_with_every_take_on_disk(
    run, project, monkeypatch, command: str
) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    made = project(**{command: MOVING[command]}, check=_priced(a_spend(0.0, 0.0, sections=())))
    assert run(command, "--replace-voiced").exit_code == ErrorCode.APPROVAL.exit_code
    assert [name for name, _, _ in made.calls if name == command] == []


@pytest.mark.parametrize("command", ["soundscape", "build"])
def test_an_unset_run_told_to_replace_the_score_asks_even_with_every_sound_on_disk(
    run, project, monkeypatch, command: str
) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    made = project(**{command: MOVING[command]}, check=_priced(a_spend(0.0, 0.0, sections=())))
    refused = run(command, "--replace-score")
    assert refused.exit_code == ErrorCode.APPROVAL.exit_code
    assert f"decktalk {command} --spend" in refused.err, refused.err
    assert [name for name, _, _ in made.calls if name == command] == []


@pytest.mark.parametrize("command", ["soundscape", "build"])
def test_replace_score_reaches_the_library_with_spend_and_is_confirmed_on_a_terminal(
    run, project, command: str
) -> None:
    made = project(**{command: MOVING[command]})
    run(command, "--spend")
    assert made.calls[-1][2]["replace_score"] is False
    run(command, "--spend", "--replace-score")
    assert made.calls[-1][2]["replace_score"] is True, "without a terminal the flag is the authorisation"
    declined = run(command, "--spend", "--replace-score", tty=True, stdin="n\n")
    assert "every bought sound" in declined.err
    assert made.calls[-1][2]["replace_score"] is False
    quiet = run(command, "--no-spend", "--replace-score", tty=True)
    assert "every bought sound" not in quiet.err, "a run that may not spend has nothing to confirm"
    assert made.calls[-1][2]["replace_score"] is False


def test_each_replace_flag_names_what_it_replaces(run) -> None:
    """A free voice's take is not paid, so the help says voiced take, and a bought sound is its own flag."""
    for command in ("narrate", "build"):
        said = " ".join(run(command, "--help").out.split())
        assert "--replace-voiced Set aside each voiced take" in said
        assert "each paid take" not in said
    for command in ("soundscape", "build"):
        said = " ".join(run(command, "--help").out.split())
        assert "--replace-score Buy each bought sound again" in said


def test_soundscape_takes_no_force_because_every_sound_it_makes_is_bought(run, project) -> None:
    project(soundscape=SOUNDSCAPE)
    assert run("soundscape", "--no-spend", "--force").exit_code == ErrorCode.USAGE.exit_code


def test_an_unset_narrate_is_priced_for_the_sections_it_runs(run, project) -> None:
    made = project(narrate=NARRATE, check=_priced(a_spend(0.0, 0.0, sections=())))
    run("narrate", "--section", "2")
    assert made.called("check")["only"] == (2,)


def test_an_unset_build_whose_takes_are_bought_asks_about_the_sounds_it_would_buy(run, project, monkeypatch) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend())
    made = project(build=ANSWERS["build"], check=_priced(a_spend(0.0, 0.0, sections=())))
    assert run("build").exit_code == ErrorCode.APPROVAL.exit_code
    assert [name for name, _, _ in made.calls if name == "build"] == []


def test_an_unset_build_on_a_free_voice_still_asks_about_the_sounds_it_would_buy(run, project, monkeypatch) -> None:
    """A free voice buys its takes without asking, and the paid soundscape beside it is still asked about."""
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(billing=Billing.PER_SECOND))
    free = a_spend(0.0, 0.0, sections=(1, 2), billing=Billing.FREE)
    made = project(build=ANSWERS["build"], check=_priced(free))
    ran = run("build")
    assert ran.exit_code == ErrorCode.APPROVAL.exit_code, ran.err
    assert [name for name, _, _ in made.calls if name == "build"] == []


@pytest.mark.parametrize("sections", [(1, 2), ()], ids=["takes-to-buy", "every-take-on-disk"])
def test_an_unset_build_on_a_free_voice_whose_sound_cannot_be_priced_is_asked_about(
    run, project, monkeypatch, sections: tuple[int, ...]
) -> None:
    """A soundscape nobody could price is never bought on the strength of a free voice beside it."""
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: None)
    free = a_spend(0.0, 0.0, sections=sections, billing=Billing.FREE)
    made = project(build=ANSWERS["build"], check=_priced(free))
    assert run("build").exit_code == ErrorCode.APPROVAL.exit_code
    assert [name for name, _, _ in made.calls if name == "build"] == []


@pytest.mark.parametrize("tty", [True, False], ids=["terminal", "no-terminal"])
def test_an_unset_build_on_a_free_voice_with_no_sound_to_buy_runs_without_asking(
    run, project, monkeypatch, tty: bool
) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    free = a_spend(0.0, 0.0, sections=(1, 2), billing=Billing.FREE)
    made = project(build=ANSWERS["build"], check=_priced(free))
    ran = run("build", tty=tty)
    assert ran.exit_code == 0, ran.err
    assert "Spend that now?" not in ran.err
    assert made.called("build")["spend"] is True


@pytest.mark.parametrize("span", [("--from", "record"), ("--skip", "narrate", "--skip", "soundscape")])
def test_an_unset_build_that_runs_no_stage_that_buys_is_never_priced(run, project, monkeypatch, span) -> None:
    monkeypatch.setattr(Session, "sound_price", lambda *_a, **_k: a_spend(0.0, 0.0, sections=()))
    made = project(build=ANSWERS["build"], check=_priced(a_spend()))
    assert run("build", *span).exit_code == 0
    assert [name for name, _, _ in made.calls if name == "check"] == []


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        pytest.param((), 0, id="unset"),
        pytest.param(("--fail-on", FailOn.ANY.value), 1, id="fail-on-any"),
        pytest.param(("--fail-on", FailOn.ANY.value, "--allow", Code.TAKE_MISSING.value), 0, id="fail-on-any-allowed"),
    ],
)
def test_a_missing_take_fails_a_build_only_at_the_threshold_that_names_it(
    run, project, flags: tuple[str, ...], code: int
) -> None:
    missing = ANSWERS["build"].model_copy(update={"findings": (finding(Code.TAKE_MISSING),)})
    project(build=missing)
    ran = run("build", "--no-spend", "--json", *flags)
    assert ran.exit_code == code, ran.err
    assert json.loads(ran.out)["ok"] is (code == 0)


def test_an_allowed_code_is_still_reported_and_only_stops_failing_the_run(run, project) -> None:
    """`--allow` decides whether a finding fails the run, never whether the run reports it."""
    unknown = CUE.model_copy(update={"ok": False, "findings": (finding(Code.CUE_UNKNOWN),)})
    made = project(cue=unknown)
    ran = run("cue", "--json", "--allow", Code.CUE_UNKNOWN.value)
    assert ran.exit_code == 0, ran.err
    written = json.loads(ran.out)
    assert written["ok"] is True
    assert [one["code"] for one in written["findings"]] == [Code.CUE_UNKNOWN.value]
    assert set(made.called("cue")) == {"only", "cancel"}, "the stage was told what the caller allows"


@pytest.mark.parametrize("command", ["narrate", "soundscape", "build"])
def test_no_voice_is_an_unknown_option(run, command: str) -> None:
    ran = run(command, "--no-voice")
    assert ran.exit_code == 2
    assert "--no-voice" in ran.err


def _priced(spend: Spend) -> CheckResult:
    """What `check` answers with when a command prices a run before asking about it."""
    return CheckResult(ok=True, run="r", judged=(), pages=False, frames=False, spend=spend)


def test_narrate_keeps_every_paid_take_unless_the_flag_says_otherwise(run, project) -> None:
    made = project(narrate=NARRATE)
    run("narrate", "--no-spend")
    assert made.called("narrate")["replace_voiced"] is False
    run("narrate", "--no-spend", "--replace-voiced")
    assert made.calls[-1][2]["replace_voiced"] is True


def test_assemble_refuses_a_skip_that_names_a_stage_it_does_not_run(run, project) -> None:
    project(assemble=ASSEMBLE)
    ran = run("assemble", "--skip", "record")
    assert ran.exit_code == 2
    assert "soundscape alone" in ran.err


def test_soundscape_says_what_its_spending_flags_buy(run) -> None:
    """The spending family is shared, and on soundscape the thing bought is sound rather than a voice."""
    said = " ".join(run("soundscape", "--help").out.split())
    assert "play a placeholder" not in said
    assert "buy nothing: report the plan" in said
    assert "play a placeholder" in " ".join(run("narrate", "--help").out.split())


def test_assemble_and_clip_describe_the_narrower_flags_they_take(run) -> None:
    assert "Run every stage but this one" not in run("assemble", "--help").out
    clip = " ".join(run("clip", "--help").out.split())
    assert "--section N The one section to cut the clip from, such as 3." in clip
    assert "clip-N.mp4" in clip


def test_verify_measures_the_film(run, project) -> None:
    made = project(verify=VERIFY)
    ran = run("verify")
    assert made.called("verify")
    assert "build/final/demo.mp4" in ran.out


def test_build_stops_where_the_exit_code_would_fail_and_carries_on_past_what_is_allowed(run, project, answers) -> None:
    """The threshold a build stops on is the one its exit code fails on, so the two cannot disagree."""
    made = project(build=answers["build"])
    run("build", "--no-spend", "--allow", Code.CUE_UNKNOWN.value)
    asked = made.called("build")
    assert asked["allow"] == frozenset({Code.CUE_UNKNOWN})
    assert asked["stop_on"] is Certainty.CERTAIN
    assert "soundscape" not in asked


@pytest.mark.parametrize(("flag", "stops"), [(FailOn.ANY, Certainty.UNCERTAIN), (FailOn.NEVER, None)])
def test_fail_on_moves_where_a_build_stops(run, project, answers, flag: FailOn, stops: Certainty | None) -> None:
    made = project(build=answers["build"])
    run("build", "--no-spend", "--fail-on", flag.value)
    assert made.called("build")["stop_on"] is stops


def test_a_build_that_stopped_on_a_finding_exits_1_and_says_where(run, project, answers) -> None:
    stopped = answers["build"].model_copy(update={"ok": False, "stopped_at": Stage.CUE, "findings": (finding(),)})
    project(build=stopped)
    ran = run("build", "--no-spend")
    assert ran.exit_code == 1
    assert f"Stopped at {Stage.CUE.value}" in ran.out


def test_build_names_its_run_and_its_events_file_on_the_first_line_of_stderr(run, project, answers) -> None:
    made = project(build=answers["build"])
    made.emits["build"] = (RunStart, {"events_path": Path("build/events/r.jsonl")})
    ran = run("build", "--no-spend")
    assert ran.err.startswith("run r, events build/events/r.jsonl")


def test_events_writes_one_json_line_per_moment_on_stderr(run, project, answers) -> None:
    made = project(build=answers["build"])
    made.emits["build"] = (RunStart, {"events_path": Path("build/events/r.jsonl")})
    ran = run("build", "--no-spend", "--events")
    assert '"event":"run.start"' in ran.err
    assert ran.out.strip().startswith("Built")


def test_every_stderr_line_under_events_is_one_json_object(run, project, answers) -> None:
    """A reader of `--events` parses every line of stderr, so a plain sentence among them breaks it."""
    made = project(build=answers["build"])
    made.emits["build"] = (RunStart, {"events_path": Path("build/events/r.jsonl")})
    ran = run("build", "--no-spend", "--events", "-v")
    lines = ran.err.splitlines()
    assert lines
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert not any(line.startswith("run r, events") for line in lines)


def test_a_refusal_under_events_is_one_json_object_on_stderr(run, project, answers) -> None:
    project(build=answers["build"], check=answers["check"])
    ran = run("build", "--events")
    assert ran.exit_code == 2
    [line] = ran.err.splitlines()
    assert json.loads(line)["error"]["code"] == ErrorCode.APPROVAL.value


def test_build_without_a_terminal_and_without_a_flag_refuses_the_spend(run, project, answers) -> None:
    project(build=answers["build"], check=answers["check"])
    ran = run("build")
    assert ran.exit_code == 2
    assert "error[APPROVAL]" in ran.err
    assert "--spend" in ran.err


def test_the_approval_refusal_is_one_object_under_json(run, project, answers) -> None:
    project(build=answers["build"], check=answers["check"])
    ran = run("build", "--json")
    assert ran.exit_code == 2
    written = json.loads(ran.out)
    assert written["error"]["code"] == ErrorCode.APPROVAL.value
    assert written["ok"] is False


def test_clip_needs_exactly_one_section(run, project) -> None:
    project(
        clip=ClipResult(
            ok=True,
            run="r",
            section=1,
            film="clip-1.mp4",
            words="clip-1.words.json",
            start=0.0,
            end=2.0,
            seconds=2.0,
            hold_seconds=0.0,
            gain_db=0.0,
            estimated=True,
        )
    )
    assert run("clip", "--section", "1", "--start", "0", "--end", "2").exit_code == 0
    assert run("clip", "--section", "1,2").exit_code == 2


def test_applying_one_fix_says_so_in_the_singular(monkeypatch: pytest.MonkeyPatch) -> None:
    made = Session(Globals(), command="build")
    said: list[str] = []
    monkeypatch.setattr(made, "say", said.append)
    applied = FixOutcome(code=Code.CUE_THIN_CHANGE, title="Move the cue.", applied=True)
    fake = Fake(apply=ApplyResult(ok=True, run="r", fixes=(applied,)))
    answer = ANSWERS["build"]
    assert isinstance(answer, BuildResult)
    built = answer.model_copy(update={"ok": False, "findings": (finding(fix=True),)})
    commands._offered(made, fake.project(), built, True)
    assert said == ["Applied 1 fix. Run decktalk build again to make the film from them."]
