"""Every project `decktalk init` writes records and verifies with no voice.

    uv run pytest -m scaffold

This is the promise the packaged projects make: a fresh project builds on the machine it was written
on, with no API key, no spend and no file of the author's. The starter takes about a minute and the
lesson example a few, so the suite carries the `scaffold` marker and is the one suite a pull request
leaves out. `tests/decktalk/template/test_template.py` judges the same projects as data on every
pull request without building them, and this file is what proves they build.

The command line is driven as a real subprocess of `python -m decktalk`, so nothing about the CLI's
internal module layout is assumed and nothing is faked. The commands and flags are spelled from
`~/Documents/decktalk-plan/gen5/synthesis/design.md` section 3, the final vocabulary, with the flag
families of `~/Documents/decktalk-plan/gen5/panels/cli/design.md` section 2 applied. T8 had not
landed when this was written, so a failure that names a missing command or an unknown flag is T8's
spelling and not a broken project.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.findings import Code
from decktalk.template import EXAMPLES, STARTER
from support.commands import FOUND_NOTHING, HOSTILE_DIRECTORY, clean_environ, codes, flat
from support.timing_policy import (
    EVERY_PACKAGED_PROJECT_SECONDS,
    FIRST_FETCH_SECONDS,
    LATE_FRAME,
    assert_build_finished,
    budget,
    faults,
    gates_timing,
    judged,
    note_late_reveals,
)

BUILD_BUDGET_SECONDS = budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS)
"""How long one packaged project may take to build, which the lesson example sets and nothing else.

The lesson draws every attribute in the table, so it records more slides than any other project
here. The budget is a ceiling that catches a hung page rather than a measurement of anything.
"""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(BUILD_BUDGET_SECONDS)]

NO_EXAMPLE = None
"""What `--example` is given for the starter, because the starter is what `init` writes unnamed.

It is not the name `init` reports. The starter is a packaged project like any other and `init`
reports which one it wrote, so `STARTER` is the answer and this is the flag that was never typed.
"""

PACKAGED = [NO_EXAMPLE, *(example.name for example in EXAMPLES if example.shipped)]
"""Every project `decktalk init` can write today, which is the starter and each shipped example."""

IDS = [STARTER, *(name for name in PACKAGED[1:] if name)]


def sentences(doc: dict[str, Any], wanted: tuple[Code, ...]) -> list[str]:
    """What the run said about the codes named, which is the news a runner that reports prints."""
    return [str(row["message"]) for row in doc["findings"] if Code(row["code"]) in wanted]


def decktalk(*args: str, cwd: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    """One `decktalk` command, run as the subprocess an author or an agent would run.

    The environment is stripped of the credential and of the machine settings file, because a
    packaged project has to build on a machine that has never seen either.
    """
    return subprocess.run(
        [sys.executable, "-m", "decktalk", *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cache),
        cwd=cwd,
    )


@pytest.mark.parametrize("example", PACKAGED, ids=IDS)
def test_a_packaged_project_builds_and_verifies_without_a_voice(
    tmp_path: Path, example: str | None, pytestconfig: pytest.Config
) -> None:
    """`init`, then `build --no-voice`, then `verify`, on a project straight out of the wheel.

    This suite runs whole builds one after another on a runner that renders in software, so its leg
    reports cue timing rather than gating it and `tests/support/timing_policy.py` says what that
    means. Nothing else is weakened: a cue that never changed the picture, a phrase the page never
    found and a page that threw fail this test on every runner it is ever run on.
    """
    gate = gates_timing(pytestconfig)
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    name = example or STARTER
    root = home / name

    chosen = ("--example", example) if example is not NO_EXAMPLE else ()
    made = decktalk("init", str(root), "--name", name, "--json", *chosen, cwd=home, cache=home)
    assert made.returncode == FOUND_NOTHING, made.stderr
    doc = flat(made.stdout)
    assert doc["example"] == (example or STARTER), "init reports the packaged project it wrote"
    assert Path(doc["root"]).name == name

    built = decktalk("--project", str(root), "build", "--no-voice", "--json", cwd=home, cache=home)
    report = flat(built.stdout)
    assert_build_finished(built.returncode, codes(report), built.stderr, pytestconfig)
    film = root / report["film"]
    assert film.is_file() and film.stat().st_size > 0

    # Nothing the runtime could not honour, on any page of any section.
    for log_path in sorted((root / "build" / "recordings").glob("*.json")):
        log = RecordingLog.read(log_path)
        assert log is not None, log_path
        assert list(log.findings) == [], (log_path.name, log.findings)
        assert list(log.external) == [], (log_path.name, log.external)

    # Read the finished film back. No packaged project may raise a certain finding this runner
    # judges, because that is a cue that did not land. An example is a project that was really made
    # and its art is its own, so a reveal of its that sits at the measurement floor may be
    # uncertain. What it may never be is a missed cue.
    checked = decktalk("--project", str(root), "verify", "--json", "--fail-on", "never", cwd=home, cache=home)
    measured = flat(checked.stdout)
    assert faults(codes(measured), gate) == [], measured["findings"]
    if example is NO_EXAMPLE:
        clean = "the starter is the page every author copies, so it is clean"
        assert judged(codes(measured), gate) == [], f"{clean}: {measured['findings']}"
    if news := sentences(measured, LATE_FRAME):
        note_late_reveals(pytestconfig, name, news)
