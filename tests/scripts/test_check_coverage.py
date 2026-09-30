"""The coverage gate's roll call, which fails a leg that measured lines but ran no test.

A suite whose every test skipped still imports the package, so its data file is never empty. The gate
reads the JUnit report each suite writes as well, and these are the reports it has to tell apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import check
import check_coverage as gate


def report(path: Path, *, tests: int, skipped: int) -> None:
    """A JUnit report as pytest writes one, with a single suite element and its totals."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="0" failures="0" skipped="{skipped}" tests="{tests}" time="1.0"/>'
        "</testsuites>",
        encoding="utf-8",
    )


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A healthy report of every suite, each having run ten tests and measured some lines.

    A test unlinks or overwrites the one report it is about, so every other leg stays healthy.
    """
    monkeypatch.setattr(check, "REPORTS", tmp_path)
    monkeypatch.setattr(gate, "lines_measured", lambda _leg: 100)
    for leg in gate.reporting_legs():
        report(tmp_path / f"{leg}.xml", tests=10, skipped=0)
    return tmp_path


@pytest.mark.parametrize(
    ("leg", "tests", "skips", "silent"),
    [
        pytest.param(None, 0, 0, [], id="a healthy run of every suite"),
        pytest.param("e2e", 32, 32, ["e2e (ran no test)"], id="every test skipped"),
        pytest.param(
            "browser",
            85,
            80,
            ["browser (skipped 80 tests that -m browser selected)"],
            id="a skip under the marker the run named",
        ),
        pytest.param("unit", 4537, 1, [], id="the suite that names no marker skips what this platform cannot run"),
    ],
)
def test_a_suite_is_silent_when_it_ran_nothing_its_marker_selected(
    reports: Path, leg: str | None, tests: int, skips: int, silent: list[str]
) -> None:
    if leg is not None:
        report(reports / f"{leg}.xml", tests=tests, skipped=skips)
    assert gate.silent_legs() == silent


def test_a_suite_that_wrote_no_report_is_silent(reports: Path) -> None:
    (reports / "media.xml").unlink()
    assert gate.silent_legs() == ["media (wrote no report of the tests it ran)"]


def test_one_python_of_a_leg_that_ran_nothing_is_enough_to_silence_it(reports: Path) -> None:
    """The coverage job renames each report after its leg, and the unit group runs on three Pythons."""
    (reports / "unit.xml").unlink()
    report(reports / f"unit-{check.LINUX}-3.12.xml", tests=4537, skipped=1)
    report(reports / f"unit-{check.LINUX}-3.13.xml", tests=0, skipped=0)
    assert gate.silent_legs() == ["unit (ran no test)"]


def test_every_measuring_suite_writes_the_report_the_gate_reads() -> None:
    for leg, group in gate.suites().items():
        (command,) = [command for command in group.commands if "pytest" in command]
        assert f"--junitxml={check.REPORTS / f'{leg}.xml'}" in command, group.name


@pytest.mark.parametrize("argv", [[], ["--check", "--write"]])
def test_a_run_must_name_exactly_one_mode(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """A bare run used to gate as though --check were named, and both flags together used to write."""
    monkeypatch.setattr("sys.argv", ["check_coverage.py", *argv])
    with pytest.raises(SystemExit) as refused:
        gate.main()
    assert refused.value.code == 2
