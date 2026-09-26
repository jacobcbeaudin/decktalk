"""The coverage gate's roll call, which fails a leg that measured lines but ran no test.

A suite whose every test skipped still imports the package, so its data file is never empty. The gate
reads the JUnit report each suite writes as well, and these are the reports it has to tell apart.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from support.paths import REPO


def _load(name: str) -> ModuleType:
    """One script under `scripts/` as a module, registered so the scripts can import each other."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = sys.modules.get("check") or _load("check")
gate = _load("check_coverage")


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
    """An empty report directory, with every suite having measured some lines."""
    monkeypatch.setattr(check, "REPORTS", tmp_path)
    monkeypatch.setattr(gate, "lines_measured", lambda _leg: 100)
    return tmp_path


def test_a_healthy_run_of_every_suite_is_not_silent(reports: Path) -> None:
    for leg in gate.reporting_legs():
        report(reports / f"{leg}.xml", tests=10, skipped=0)
    assert gate.silent_legs() == []


def test_a_suite_whose_every_test_skipped_is_silent(reports: Path) -> None:
    for leg in gate.reporting_legs():
        report(reports / f"{leg}.xml", tests=10, skipped=0)
    report(reports / "e2e.xml", tests=32, skipped=32)
    assert gate.silent_legs() == ["e2e (ran no test)"]


def test_a_skip_under_the_marker_the_run_named_is_silent(reports: Path) -> None:
    for leg in gate.reporting_legs():
        report(reports / f"{leg}.xml", tests=10, skipped=0)
    report(reports / "browser.xml", tests=85, skipped=80)
    assert gate.silent_legs() == ["browser (skipped 80 tests that -m browser selected)"]


def test_the_suite_that_names_no_marker_may_skip_what_this_platform_cannot_run(reports: Path) -> None:
    for leg in gate.reporting_legs():
        report(reports / f"{leg}.xml", tests=10, skipped=0)
    report(reports / "unit.xml", tests=4537, skipped=1)
    assert gate.silent_legs() == []


def test_a_suite_that_wrote_no_report_is_silent(reports: Path) -> None:
    for leg in gate.reporting_legs():
        if leg != "media":
            report(reports / f"{leg}.xml", tests=10, skipped=0)
    assert gate.silent_legs() == ["media (wrote no report of the tests it ran)"]


def test_one_python_of_a_leg_that_ran_nothing_is_enough_to_silence_it(reports: Path) -> None:
    """The coverage job renames each report after its leg, and the unit group runs on three Pythons."""
    for leg in gate.reporting_legs():
        if leg != "unit":
            report(reports / f"{leg}.xml", tests=10, skipped=0)
    report(reports / f"unit-{check.LINUX}-3.12.xml", tests=4537, skipped=1)
    report(reports / f"unit-{check.LINUX}-3.13.xml", tests=0, skipped=0)
    assert gate.silent_legs() == ["unit (ran no test)"]


def test_every_measuring_suite_writes_the_report_the_gate_reads() -> None:
    for leg, group in gate.suites().items():
        (command,) = [command for command in group.commands if "pytest" in command]
        assert f"--junitxml={check.REPORTS / f'{leg}.xml'}" in command, group.name
