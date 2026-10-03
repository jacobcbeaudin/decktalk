"""The watch loop: what it rebuilds when a file is saved, and what it never does."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from decktalk.cli import watch
from decktalk.cli.session import Globals, Session
from decktalk.errors import InputError
from decktalk.inputs.workspace import Workspace
from decktalk.results import BuildResult, SectionKind, SectionStatus, ServeResult, StatusResult
from support.spends import a_spend
from support.takes import TAKE_SUFFIX

from .conftest import Fake

BUILT = BuildResult(ok=True, run="r", stages=(), spending=False, spend=a_spend(), seconds=1.0)


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


def test_the_loop_serves_builds_once_and_never_voices(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    origin = Origin()
    project = Fake(serve=origin, build=BUILT, status=_status())
    _place(project, tmp_path)
    built = watch.loop(session(), project.project())
    assert built is BUILT
    assert project.called("build")["spend"] is False
    assert origin.closed


def test_a_refused_rebuild_is_reported_and_the_loop_keeps_watching(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(watch.time, "sleep", _stop)
    project = Fake(serve=Origin(), build=InputError("script.md is not there."), status=_status())
    _place(project, tmp_path)
    built = watch.loop(session(), project.project())
    assert built.ok is False
    assert "error[INPUT]" in capsys.readouterr().err


def test_a_saved_file_names_the_sections_it_touches(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = (2,)
    assert watch._touched(project.project(), [tmp_path / "script.md"]) == (2,)


def test_a_saved_file_that_touches_nothing_named_rebuilds_everything(tmp_path) -> None:
    project = Fake()
    project.answers["sections_touching"] = ()
    assert watch._touched(project.project(), [tmp_path / "decktalk.toml"]) is None


def test_what_a_run_writes_is_never_watched(tmp_path) -> None:
    (tmp_path / "script.md").write_text("one", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "takes.json").write_text("{}", encoding="utf-8")
    watched = watch._stamps(_place(Fake(), tmp_path).project())
    assert set(watched) == {tmp_path / "script.md"}


def test_a_build_folder_the_project_names_is_never_watched(tmp_path) -> None:
    """A run writes into this folder, so watching it would start the next build without end."""
    (tmp_path / "script.md").write_text("one", encoding="utf-8")
    for written in ("out/film", "takes"):
        (tmp_path / written).mkdir(parents=True)
        (tmp_path / written / "takes.json").write_text("{}", encoding="utf-8")
    project = _place(Fake(), tmp_path, build="out/film", takes="takes")
    watched = watch._stamps(project.project())
    assert set(watched) == {tmp_path / "script.md"}


def _place(project: Fake, root: Path, *, build: str = "build", takes: str = "takes") -> Fake:
    """Put a fake project at a root, with its build and take folders where a test's settings put them."""
    project.root = root
    project._inputs = SimpleNamespace(
        workspace=Workspace(
            root=root,
            build=root / build,
            name="demo",
            suffix=TAKE_SUFFIX,
            takes=root / takes,
            score_dir=root / "score",
        )
    )
    return project


def test_a_voiced_take_that_goes_stale_is_named_with_the_command_that_voices_it(capsys, tmp_path) -> None:
    project = Fake(status=_status(stale=True))
    project.root = tmp_path
    made = Session(Globals(), command="build")
    watch._stale(made, project.project())
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
