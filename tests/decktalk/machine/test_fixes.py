"""The fix applier: what it applies, what it refuses, and that a refused fix changes no file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from decktalk.events import Event, Level, RunLog
from decktalk.findings import (
    Applicability,
    Code,
    CommandFix,
    Edit,
    EditFix,
    Finding,
    Location,
)
from decktalk.machine import Machine
from decktalk.machine.fixes import FIX_TIMEOUT_SECONDS, apply_fix, fixes_of
from decktalk.results import FixOutcome, Scope
from decktalk.toolchain import command_line
from support.links import link
from support.logs import data_of
from support.runs import a_machine

# ---- applying a fix ---------------------------------------------------------------------------


INSTALL = ("decktalk", "install")
"""The one command a fix may run, which these tests stand in for with a fake subprocess."""
INSTALL_FIX = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)


def a_finding(fix: object) -> Finding:
    return Finding(code=Code.FILE_MISSING, message="x", location=Location(where="a"), fix=fix)


def ran(here: Machine, fix: CommandFix, *, unsafe: bool = False) -> FixOutcome:
    """What applying a machine's fix in a run of its own came to."""
    with here._run() as run:
        return apply_fix(run, Code.FILE_MISSING, fix, root=here.cwd, scope=Scope.MACHINE, unsafe=unsafe)


def exits(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Make every command a fix runs exit with `code`, so no test installs anything."""
    monkeypatch.setattr(
        "decktalk.machine.fixes.subprocess.run",
        lambda argv, **_: subprocess.CompletedProcess(argv, code, b"", b"Traceback\nOSError: the cache is read-only\n"),
    )


def test_a_fix_is_taken_from_the_finding_whose_code_it_resolves() -> None:
    assert fixes_of(a_finding(INSTALL_FIX)) == ((Code.FILE_MISSING, INSTALL_FIX),)
    assert fixes_of([a_finding(None), a_finding(INSTALL_FIX)]) == ((Code.FILE_MISSING, INSTALL_FIX),)


def test_a_fix_only_a_person_can_make_is_reported_and_never_applied(tmp_path: Path) -> None:
    outcome = ran(a_machine(tmp_path), INSTALL_FIX.model_copy(update={"applicability": Applicability.DISPLAY}))
    assert not outcome.applied and "only a person" in (outcome.why or "")


def test_a_fix_left_alone_is_a_warning_that_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --fix` and `check --fix` print the stream, not the result, so the reason has to be a line."""
    here = a_machine(tmp_path)
    exits(monkeypatch, 1)
    seen: list[Event] = []
    with here.events.subscribe(seen.append):
        here.apply(a_finding(INSTALL_FIX))
    warned = [line.message for line in seen if isinstance(line, RunLog) and line.level is Level.WARNING]
    assert any(INSTALL_FIX.title in message and "exited 1" in message for message in warned)


def test_a_fix_that_can_lose_work_is_applied_only_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 0)
    fix = INSTALL_FIX.model_copy(update={"applicability": Applicability.UNSAFE})
    assert not ran(here, fix).applied and ran(here, fix, unsafe=True).applied


def test_a_command_that_fails_is_reported_rather_than_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits(monkeypatch, 1)
    outcome = ran(a_machine(tmp_path), INSTALL_FIX)
    assert not outcome.applied and "exited 1" in (outcome.why or "")
    assert "OSError: the cache is read-only" in (outcome.why or "")


def test_a_fix_command_leaves_its_command_exit_time_and_output_on_the_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 2)
    seen: list[Event] = []
    with here.events.subscribe(seen.append):
        ran(here, INSTALL_FIX)
    [line] = [line for line in seen if isinstance(line, RunLog) and line.source == "machine.fixes"]
    assert line.level is Level.WARNING and line.data is not None
    assert line.data["exit"] == 2 and str(line.data["argv"]).endswith("-m decktalk install")
    assert line.data["output_tail"] == "Traceback | OSError: the cache is read-only"


def test_a_command_runs_as_this_interpreters_decktalk_under_a_timeout_and_the_machines_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`decktalk` on `PATH` may be another install, and an unbounded command holds `apply` for ever."""
    here = a_machine(tmp_path, ONLY_THIS="1")
    asked: dict[str, object] = {}

    def record(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        asked.update(options, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("decktalk.machine.fixes.subprocess.run", record)
    assert ran(here, INSTALL_FIX).applied
    assert asked["argv"] == [sys.executable, "-m", "decktalk", "install"]
    assert asked["timeout"] == FIX_TIMEOUT_SECONDS
    assert asked["env"] == {
        "ONLY_THIS": "1",
        "DECKTALK_MACHINE_FILE": str(here.config_path),
        "DECKTALK_TOOLS_CACHE_DIR": str(here.cache_dir),
    }


def test_a_command_that_runs_past_its_timeout_is_stopped_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def hang(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, FIX_TIMEOUT_SECONDS)

    monkeypatch.setattr("decktalk.machine.fixes.subprocess.run", hang)
    outcome = ran(a_machine(tmp_path), INSTALL_FIX)
    assert not outcome.applied and "was stopped" in (outcome.why or "")
    [said] = [record for record in caplog.records if record.name == "decktalk.machine.fixes"]
    assert said.levelname == "WARNING" and "(timeout)" in said.getMessage()
    assert data_of(said) == {
        "argv": command_line([sys.executable, "-m", *INSTALL]),
        "reason": "timeout",
        "limit": FIX_TIMEOUT_SECONDS,
    }


def test_a_command_built_without_validation_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model refuses a foreign argv, and a model built past its validator meets the same refusal here."""
    marker = tmp_path / "invoked"

    def run_it(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        marker.write_text(" ".join(argv), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("decktalk.machine.fixes.subprocess.run", run_it)
    hostile = CommandFix.model_construct(
        kind="command", title="t", applicability=Applicability.SAFE, command=("sh", "-c", f"touch {marker}")
    )
    outcome = ran(a_machine(tmp_path), hostile)
    assert not outcome.applied and "not one of DeckTalk's own commands" in (outcome.why or "")
    assert not marker.exists()


def edit_fix(*edits: Edit) -> EditFix:
    """A safe fix of these edits, made in order."""
    return EditFix(title="t", applicability=Applicability.SAFE, edits=edits)


def an_edit(file: str | Path, **locator: object) -> EditFix:
    """A safe fix of one edit, which is the shape a finding read from JSON hands `apply`."""
    return edit_fix(Edit.model_validate({"file": file, "new": "written by a fix", **locator}))


def a_key_fix(key: str, value: str) -> EditFix:
    """A safe fix that sets one settings key, which lands in the file the scope it is applied in names."""
    return edit_fix(Edit(file=Path("decktalk.toml"), key=key, new=value))


def applied(here: Machine, fix: EditFix, root: Path) -> tuple[bool, str]:
    with here._run() as run:
        outcome = apply_fix(run, Code.CUE_MISSING, fix, root=root, scope=Scope.PROJECT, unsafe=False)
    return outcome.applied, outcome.why or ""


@pytest.mark.parametrize("escape", ["../outside.txt", "deeper/../../outside.txt"])
def test_an_edit_that_climbs_out_of_the_project_is_refused(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    done, why = applied(a_machine(tmp_path), an_edit(escape, line=1), root)
    assert not done and "outside the project" in why
    assert not (tmp_path / "outside.txt").exists()


def test_an_edit_that_names_an_absolute_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "elsewhere.txt"
    done, why = applied(a_machine(tmp_path), an_edit(target, line=1), root)
    assert not done and "outside the project" in why
    assert not target.exists()


def test_an_edit_through_a_link_that_leaves_the_project_is_refused(tmp_path: Path) -> None:
    """A link spells a path inside the project and writes outside it, so the check follows the link."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("mine\n", encoding="utf-8")
    link(root / "shared", outside)
    done, why = applied(a_machine(tmp_path), an_edit("shared/notes.txt", line=1, old="mine"), root)
    assert not done and "outside the project" in why
    assert (outside / "notes.txt").read_text(encoding="utf-8") == "mine\n"


def test_a_fix_with_one_refused_edit_writes_none_of_its_edits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    fix = edit_fix(Edit(file=Path("inside.txt"), line=1, new="x"), Edit(file=Path("../outside.txt"), line=1, new="x"))
    done, _why = applied(a_machine(tmp_path), fix, root)
    assert not done
    assert not (root / "inside.txt").exists()


def test_a_fix_whose_second_edit_is_stale_leaves_its_first_file_whole(tmp_path: Path) -> None:
    """Every edit is checked before any file is written, so a fix changes all of its files or none."""
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("moved\n", encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("first.txt"), line=1, old="one", new="changed"),
        Edit(file=Path("second.txt"), line=1, old="two", new="changed"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert not done and "no longer reads" in why
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".fixing")) == []


def test_two_edits_to_one_file_are_made_in_order_and_written_once(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("notes.txt"), line=1, old="one", new="first"),
        Edit(file=Path("notes.txt"), line=2, old="two", new="second"),
    )
    done, _why = applied(a_machine(tmp_path), fix, tmp_path)
    assert done
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\nsecond\n"


def test_a_link_planted_where_a_fix_writes_its_draft_is_a_refusal_and_is_never_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A draft's name is random, and a name a project did plant is refused in a sentence, never followed."""
    monkeypatch.setattr("decktalk.files.secrets.token_hex", lambda _size: "planted")
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("mine\n", encoding="utf-8")
    link(root / ".notes.txt.planted.draft", elsewhere)
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=1, old="one"), root)
    assert not done and "could not be read or written" in why
    assert elsewhere.read_text(encoding="utf-8") == "mine\n"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "one\n"
    assert (root / ".notes.txt.planted.draft").is_symlink()


def test_a_fix_that_breaks_the_project_file_and_sets_a_key_in_it_changes_nothing(tmp_path: Path) -> None:
    """A key edit is staged with the line edits, so the file both change is judged whole before any lands."""
    (tmp_path / "script.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text('[project]\nname = "t"\n', encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("script.md"), line=1, old="one", new="changed"),
        Edit(file=Path("decktalk.toml"), line=1, old="[project]", new="[project"),
        Edit(file=Path("decktalk.toml"), key="video.width", new="1280"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert not done and "not valid TOML" in why
    assert (tmp_path / "script.md").read_text(encoding="utf-8") == "one\n"
    assert (tmp_path / "decktalk.toml").read_text(encoding="utf-8") == '[project]\nname = "t"\n'


def test_a_line_edit_and_a_key_edit_of_the_project_file_both_land_in_one_write(tmp_path: Path) -> None:
    (tmp_path / "decktalk.toml").write_text('[project]\nname = "t"\n', encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("decktalk.toml"), line=2, old='name = "t"', new='name = "renamed"'),
        Edit(file=Path("decktalk.toml"), key="video.width", new="1280"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert done, why
    written = (tmp_path / "decktalk.toml").read_text(encoding="utf-8")
    assert 'name = "renamed"' in written and "width = 1280" in written


def test_a_file_the_system_refuses_to_replace_puts_back_every_file_already_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second move that fails, as a full disk or an editor holding a file open makes it, is a refusal."""
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("two\n", encoding="utf-8")
    moves: list[Path] = []
    real = Path.replace

    def full(self: Path, target: Path) -> Path:
        moves.append(Path(target))
        if len(moves) == 2:
            raise OSError(28, "No space left on device", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", full)
    fix = edit_fix(
        Edit(file=Path("first.txt"), line=1, old="one", new="changed"),
        Edit(file=Path("second.txt"), line=1, old="two", new="changed"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert (
        not done
        and why
        == "second.txt could not be read or written (No space left on device), so every file was left as it was."
    )
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "two\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".draft")) == []


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_settings_fix_never_writes_through_a_project_file_linked_out_of_the_project(
    tmp_path: Path, kind: str
) -> None:
    """A project that arrives with its `decktalk.toml` linked elsewhere chose where a settings fix writes."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "victim.toml"
    outside.write_text('[project]\nname = "victim"\n', encoding="utf-8")
    link(root / "decktalk.toml", outside, hard=kind == "hard")
    fix = a_key_fix("video.width", "1280")
    with a_machine(tmp_path)._run() as run:
        outcome = apply_fix(run, Code.CUE_MISSING, fix, root=root, scope=Scope.PROJECT, unsafe=False)
    assert outside.read_text(encoding="utf-8") == '[project]\nname = "victim"\n'
    if kind == "symbolic":
        assert not outcome.applied and "outside the project" in (outcome.why or "")
    else:
        assert outcome.applied and "width = 1280" in (root / "decktalk.toml").read_text(encoding="utf-8")


def test_a_line_that_no_longer_reads_what_the_fix_expected_is_left_alone(tmp_path: Path) -> None:
    """A file edited after its finding was raised has moved its lines, and line n is now another line."""
    (tmp_path / "notes.txt").write_text("one\ninserted\ntwo\n", encoding="utf-8")
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=2, old="two"), tmp_path)
    assert not done and "no longer reads" in why
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\ninserted\ntwo\n"


def test_a_line_past_the_end_of_the_file_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one\n", encoding="utf-8")
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=5, old="five"), tmp_path)
    assert not done and "no longer reads" in why


def test_a_setting_a_fix_names_is_written_into_the_file_the_machine_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process may name another file, and the machine a host built by hand is the one being fixed."""
    here = a_machine(tmp_path)
    monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(tmp_path / "the-process-file.toml"))
    fix = a_key_fix("tools.ffmpeg", "/usr/bin/ffmpeg")
    result = here.apply(a_finding(fix))
    assert result.fixes[0].applied, result.fixes[0].why
    assert "/usr/bin/ffmpeg" in here.config_path.read_text(encoding="utf-8")
    assert not (tmp_path / "the-process-file.toml").exists()
