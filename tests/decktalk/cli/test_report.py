"""The five commands that read a project, driven through the real parser against a faked project."""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.cli import report as commands
from decktalk.results import ApplyResult
from support.spends import a_spend

from .conftest import ANSWERS, Fake, finding

FIXABLE = ANSWERS["check"].model_copy(
    update={"ok": False, "findings": (finding(fix=True),), "judged": (Path("cues.json"),)}
)
"""A check that found one thing it can fix, which every test of `--fix` hands its fake."""


def test_status_reports_the_project_and_judges_nothing(run, project, answers) -> None:
    made = project(status=answers["status"])
    ran = run("status")
    assert ran.exit_code == 0
    assert made.called("status")
    assert "Section" in ran.out


def test_status_never_fails_on_a_finding_because_it_takes_no_threshold(run) -> None:
    assert "--fail-on" not in run("status", "--help").out


def test_status_help_says_the_one_thing_it_exits_1_for(run) -> None:
    """status raises a certain FILE_MISSING, so its help cannot promise that it never exits 1."""
    said = " ".join(run("status", "--help").out.split())
    assert "never exits 1" not in said
    assert "exits 1 only then" in said


def test_check_judges_the_written_files_and_prices_a_voiced_run(run, project, answers) -> None:
    made = project(check=answers["check"])
    ran = run("check", "--json")
    assert ran.exit_code == 0
    written = json.loads(ran.out)
    assert written["spend"]["ceiling_dollars"] == a_spend().ceiling_dollars
    assert made.called("check")["pages"] is True


def test_check_without_pages_says_so_to_the_library(run, project, answers) -> None:
    made = project(check=answers["check"])
    run("check", "--no-pages", "--no-frames")
    asked = made.called("check")
    assert asked["pages"] is False
    assert asked["frames"] is False


def test_check_reports_every_fix_and_applies_none_without_a_terminal(run, project) -> None:
    made = project(check=FIXABLE)
    ran = run("check")
    assert ran.exit_code == 1
    assert "fix (safe)" in ran.out
    assert [name for name, _, _ in made.calls] == ["check"]


def test_check_fix_applies_the_safe_fixes_and_judges_again(run, project) -> None:
    made = Fake(check=FIXABLE, apply=ApplyResult(ok=True, run="r", fixes=()))
    made.answers["reload"] = made
    project_calls = made.calls
    _install(project, made)
    run("check", "--fix")
    assert [name for name, _, _ in project_calls] == ["check", "apply", "reload", "check"]


def test_the_second_judgement_after_a_fix_keeps_the_sections_it_was_asked_about(run, project) -> None:
    """A re-check that widened to the whole project priced sections the caller never named."""
    judged = FIXABLE.model_copy(update={"pages": False, "frames": False})
    made = Fake(check=judged, apply=ApplyResult(ok=True, run="r", fixes=()))
    _install(project, made)
    run("check", "--section", "4", "--no-pages", "--fix")
    first, again = [keywords for name, _, keywords in made.calls if name == "check"]
    assert again["only"] == first["only"] == (4,)
    assert again["pages"] is first["pages"] is False


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
