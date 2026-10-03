"""The watch loop: what it rebuilds when a file is saved, and what it never does."""

from __future__ import annotations

from pathlib import Path

from decktalk.cli import watch
from decktalk.cli.session import Globals, Session
from decktalk.errors import InputError
from decktalk.results import UNPRICED, BuildResult, SectionKind, SectionStatus, ServeResult, StatusResult, TakeState
from decktalk.stages.narrate.state import CHANGED, HELD
from support.costs import a_cost

from .conftest import Fake

BUILT = BuildResult(ok=True, run="r", stages=(), spend=False, cost=a_cost(), elapsed_seconds=1.0)


class Origin:
    """A local origin that is already there, which is what the loop starts before it builds."""

    result = ServeResult(ok=True, run="r", url="http://127.0.0.1:8000", port=8000)

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """Stop serving, which the loop does however it ends."""
        self.closed = True


def session() -> Session:
    """One session with no terminal, which is what a watch loop under test writes through."""
    return Session(Globals(quiet=True), command="build")


def test_the_loop_serves_builds_once_and_never_voices(monkeypatch) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    origin = Origin()
    project = Fake(serve=origin, build=BUILT, status=_status(), authored_files=())
    built = watch.loop(session(), project.project())
    assert built is BUILT
    assert project.called("build")["spend"] is False
    assert origin.closed


def test_a_refused_rebuild_is_reported_and_the_loop_keeps_watching(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    project = Fake(serve=Origin(), build=InputError("script.md is not there."), status=_status(), authored_files=())
    built = watch.loop(session(), project.project())
    assert built.ok is False
    assert "error[INPUT]" in capsys.readouterr().err
    assert built.cost == UNPRICED
    assert all(name != "price" for name, _, _ in project.calls), "a refused rebuild was priced"


def test_a_saved_file_names_the_sections_it_touches(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = (2,)
    assert watch._touched(project.project(), [tmp_path / "script.md"]) == (2,)


def test_a_saved_file_that_touches_nothing_named_rebuilds_everything(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = ()
    assert watch._touched(project.project(), [tmp_path / "decktalk.toml"]) is None


def test_the_loop_stamps_the_files_the_project_says_an_author_edits(tmp_path) -> None:
    """A file removed between the listing and its stat is left out rather than stopping the loop."""
    (tmp_path / "script.md").write_text("one", encoding="utf-8")
    project = Fake(authored_files=(tmp_path / "script.md", tmp_path / "gone.md"))
    assert set(watch._stamps(project.project())) == {tmp_path / "script.md"}


def test_a_stale_take_is_named_with_the_command_that_voices_it(capsys, tmp_path) -> None:
    project = Fake(status=_status(stale=True))
    project.root = tmp_path
    made = Session(Globals(), command="build")
    watch._stale(made, project.project())
    said = capsys.readouterr().err
    assert "has a stale voiced take" in said
    assert "decktalk narrate --section 2 --spend" in said


def test_a_voice_change_is_announced_with_the_row_s_reason(capsys, tmp_path) -> None:
    """The row says why its take is stale, so the line names the input that moved rather than guessing."""
    project = Fake(status=_status(stale=True))
    project.root = tmp_path
    watch._stale(Session(Globals(), command="build"), project.project())
    said = capsys.readouterr().err
    assert f"because {CHANGED}." in said


def test_a_current_take_is_not_announced(capsys, tmp_path) -> None:
    project = Fake(status=_status(stale=False))
    project.root = tmp_path
    watch._stale(Session(Globals(), command="build"), project.project())
    assert "stale" not in capsys.readouterr().err


def _stop(_seconds: float) -> None:
    """Stand in for the pause between two readings, and stop the loop on the first one."""
    raise KeyboardInterrupt


def _status(*, stale: bool = False) -> StatusResult:
    """What `status` answers with, with one section whose take is stale when a test asks for one."""
    return StatusResult(
        ok=True,
        run="r",
        name="demo",
        script=Path("script.md"),
        cues_file=Path("cues.json"),
        sections=(
            SectionStatus(
                section=2,
                key="02",
                kind=SectionKind.PAGE,
                source="deck/index.html",
                take_state=TakeState.STALE if stale else TakeState.VOICED,
                take_reason=CHANGED if stale else HELD,
                recorded=True,
                assembled=True,
                recording_stale=False,
            ),
        ),
    )
