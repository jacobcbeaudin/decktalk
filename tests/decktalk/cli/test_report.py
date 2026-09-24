"""The five commands that read a project, driven through the real parser against a faked project."""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.cli import report as commands
from decktalk.results import ApplyResult, CheckResult

from .conftest import Fake, finding, spend


def test_status_reports_the_project_and_judges_nothing(run, project, answers) -> None:
    made = project(status=answers["status"])
    ran = run("status")
    assert ran.exit_code == 0
    assert made.called("status")
    assert "Section" in ran.out


def test_status_never_fails_on_a_finding_because_it_takes_no_threshold(run) -> None:
    assert "--fail-on" not in run("status", "--help").out


def test_check_judges_the_written_files_and_prices_a_voiced_run(run, project, answers) -> None:
    made = project(check=answers["check"])
    ran = run("check", "--json")
    assert ran.exit_code == 0
    written = json.loads(ran.out)
    assert written["spend"]["ceiling_dollars"] == spend().ceiling_dollars
    assert made.called("check")["pages"] is True


def test_check_without_pages_says_so_to_the_library(run, project, answers) -> None:
    made = project(check=answers["check"])
    run("check", "--no-pages", "--no-frames")
    asked = made.called("check")
    assert asked["pages"] is False
    assert asked["frames"] is False


def test_check_reports_every_fix_and_applies_none_without_a_terminal(run, project) -> None:
    judged = CheckResult(
        ok=False,
        findings=(finding(fix=True),),
        run="r",
        judged=(Path("cues.json"),),
        pages=True,
        frames=True,
        spend=spend(),
    )
    made = project(check=judged)
    ran = run("check")
    assert ran.exit_code == 1
    assert "fix (safe)" in ran.out
    assert [name for name, _, _ in made.calls] == ["check"]


def test_check_fix_applies_the_safe_fixes_and_judges_again(run, project) -> None:
    judged = CheckResult(
        ok=False,
        findings=(finding(fix=True),),
        run="r",
        judged=(Path("cues.json"),),
        pages=True,
        frames=True,
        spend=spend(),
    )
    made = Fake(check=judged, apply=ApplyResult(ok=True, run="r", fixes=()))
    made.answers["reload"] = made
    project_calls = made.calls
    _install(project, made)
    run("check", "--fix")
    assert [name for name, _, _ in project_calls] == ["check", "apply", "reload", "check"]


def _install(project, fake: Fake) -> None:
    """Put one prepared fake behind the seam, rather than the fixture's own fresh one."""
    made = project()
    made.answers.update(fake.answers)
    made.calls = fake.calls
    made.answers["reload"] = made


def test_words_prints_the_clock_a_cue_phrase_is_written_against(run, project, answers) -> None:
    made = project(words=answers["words"])
    assert run("words", "--section", "2").exit_code == 0
    assert made.called("words")["only"] == (2,)


def test_storyboard_writes_the_checkpoint_page(run, project, answers) -> None:
    made = project(storyboard=answers["storyboard"])
    ran = run("storyboard")
    assert "build/storyboard.html" in ran.out
    assert made.called("storyboard")


def test_serve_prints_its_object_and_stops_when_the_origin_closes(run, project, answers) -> None:
    class Origin:
        """One origin that is already closed, which is what a test of the printed object needs."""

        result = answers["serve"]

        def wait(self) -> None:
            """A caller that waits on a closed origin waits for nothing."""

        def close(self) -> None:
            """Closing a closed origin is what the command does on its way out."""

    project(serve=Origin())
    ran = run("serve")
    assert ran.exit_code == 0
    assert "http://127.0.0.1:8000" in ran.out


def test_every_reading_command_is_registered() -> None:
    assert all(hasattr(commands, name) for name in ("status", "check", "words", "storyboard", "serve"))
