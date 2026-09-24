"""The watch loop: what it rebuilds when a file is saved, and what it never does."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.cli import watch
from decktalk.cli.session import Globals, Session
from decktalk.errors import InputError
from decktalk.results import BuildResult, SectionKind, SectionStatus, StatusResult, Voicing

from .conftest import Fake, spend

BUILT = BuildResult(ok=True, run="r", stages=(), voice=Voicing.PLACEHOLDER, spend=spend(), seconds=1.0)


class Origin:
    """A local origin that is already there, which is what the loop starts before it builds."""

    url = "http://127.0.0.1:8000"

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """Stop serving, which the loop does however it ends."""
        self.closed = True


def session() -> Session:
    """One session with no terminal, which is what a watch loop under test writes through."""
    return Session(Globals(quiet=True), command="build")


def test_the_loop_serves_builds_once_and_never_voices(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    origin = Origin()
    project = Fake(serve=origin, build=BUILT, status=_status())
    project.root = tmp_path
    built = watch.loop(session(), project)  # ty: ignore[invalid-argument-type]
    assert built is BUILT
    assert project.called("build")["voice"] is Voicing.PLACEHOLDER
    assert origin.closed


def test_a_refused_rebuild_is_reported_and_the_loop_keeps_watching(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    project = Fake(serve=Origin(), build=InputError("script.md is not there."), status=_status())
    project.root = tmp_path
    built = watch.loop(session(), project)  # ty: ignore[invalid-argument-type]
    assert built.ok is False
    assert "error[INPUT]" in capsys.readouterr().err


def test_a_saved_file_names_the_sections_it_touches(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = (2,)
    assert watch._touched(project, [tmp_path / "script.md"]) == (2,)  # ty: ignore[invalid-argument-type]


def test_a_saved_file_that_touches_nothing_named_rebuilds_everything(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = ()
    assert watch._touched(project, [tmp_path / "decktalk.toml"]) is None  # ty: ignore[invalid-argument-type]


def test_what_a_run_writes_is_never_watched(tmp_path) -> None:
    (tmp_path / "script.md").write_text("one", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "takes.json").write_text("{}", encoding="utf-8")
    watched = watch._stamps(tmp_path)
    assert set(watched) == {tmp_path / "script.md"}


def test_a_voiced_take_that_goes_stale_is_named_with_the_command_that_voices_it(capsys, tmp_path) -> None:
    project = Fake(status=_status(stale=True))
    project.root = tmp_path
    made = Session(Globals(), command="build")
    watch._stale(made, project)  # ty: ignore[invalid-argument-type]
    said = capsys.readouterr().err
    assert "no longer matches the script" in said
    assert "decktalk narrate --section 2 --spend" in said


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
        cues=Path("cues.json"),
        sections=(
            SectionStatus(
                section=2,
                key="02",
                kind=SectionKind.PAGE,
                source="deck/index.html",
                voiced=True,
                recorded=True,
                cut=True,
                stale=stale,
            ),
        ),
    )


@pytest.mark.parametrize("directory", sorted(watch.IGNORED))
def test_every_ignored_directory_is_one_a_run_writes(directory: str) -> None:
    assert directory in {"build", ".git", ".venv", "node_modules", "__pycache__"}
