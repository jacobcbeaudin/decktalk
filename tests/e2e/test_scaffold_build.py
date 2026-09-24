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

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.findings import Certainty
from decktalk.template import EXAMPLES, STARTER
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

BUILD_BUDGET_SECONDS = budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS)
"""How long one packaged project may take to build, which the lesson example sets and nothing else.

The lesson draws every attribute in the table, so it records more slides than any other project
here. The budget is a ceiling that catches a hung page rather than a measurement of anything.
"""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(BUILD_BUDGET_SECONDS)]

HOSTILE_DIRECTORY = "jacob's fïlms 2"
"""The name every temporary root of this suite sits under, because a path is an input like any other.

An apostrophe and a diacritic reach every shell quote, every ffmpeg concat list and every served URL
a build writes, and the founder's own films live under a name like this one.
"""

NO_EXAMPLE = None
"""What `--example` is given for the starter, because the starter is what `init` writes unnamed.

It is not the name `init` reports. The starter is a packaged project like any other and `init`
reports which one it wrote, so `STARTER` is the answer and this is the flag that was never typed.
"""

PACKAGED = [NO_EXAMPLE, *(example.name for example in EXAMPLES if example.shipped)]
"""Every project `decktalk init` can write today, which is the starter and each shipped example."""

IDS = [STARTER, *(name for name in PACKAGED[1:] if name)]

RESERVED_KEYS = frozenset({"schema", "ok", "findings", "error"})
"""The four keys every result carries, which is the founder's decided JSON contract."""

FOUND_NOTHING = 0
"""What the CLI exits when it judged nothing, from the CLI design's exit code table."""


def flat(stdout: str) -> dict[str, Any]:
    """The one flat object a command printed with `--json`, checked against the reserved keys."""
    doc = json.loads(stdout)
    assert isinstance(doc, dict), "--json prints one object on stdout and nothing else"
    assert RESERVED_KEYS <= set(doc), sorted(RESERVED_KEYS - set(doc))
    assert doc["schema"] == 2, doc["schema"]
    return doc


def certain(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finding the run is sure about, which is what a packaged project may never produce."""
    return [row for row in doc["findings"] if row["certainty"] == Certainty.CERTAIN.value]


def decktalk(*args: str, cwd: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    """One `decktalk` command, run as the subprocess an author or an agent would run.

    The environment is stripped of the credential and of the machine settings file, because a
    packaged project has to build on a machine that has never seen either.
    """
    env = dict(os.environ)
    for name in ("DECKTALK_PROJECT", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        env.pop(name, None)
    env["DECKTALK_CONFIG"] = str(cache / "no-machine-config.toml")
    return subprocess.run(
        [sys.executable, "-m", "decktalk", *args], capture_output=True, text=True, check=False, env=env, cwd=cwd
    )


@pytest.mark.parametrize("example", PACKAGED, ids=IDS)
def test_a_packaged_project_builds_and_verifies_without_a_voice(tmp_path: Path, example: str | None) -> None:
    """`init`, then `build --no-voice`, then `verify`, on a project straight out of the wheel."""
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
    assert certain(report) == [], built.stderr
    assert built.returncode == FOUND_NOTHING, built.stderr
    film = root / report["film"]
    assert film.is_file() and film.stat().st_size > 0

    # Nothing the runtime could not honour, on any page of any section.
    for log_path in sorted((root / "build" / "recordings").glob("*.json")):
        log = RecordingLog.read(log_path)
        assert log is not None, log_path
        assert list(log.findings) == [], (log_path.name, log.findings)
        assert list(log.external) == [], (log_path.name, log.external)

    # Read the finished film back. No packaged project may raise a certain finding, because that is
    # a cue that did not land. An example is a project that was really made and its art is its own,
    # so a reveal of its that sits at the measurement floor may be uncertain. What it may never be
    # is a missed cue.
    checked = decktalk("--project", str(root), "verify", "--json", "--fail-on", "never", cwd=home, cache=home)
    measured = flat(checked.stdout)
    assert certain(measured) == [], measured["findings"]
    if example is NO_EXAMPLE:
        assert measured["findings"] == [], "the starter is the page every author copies, so it is clean"
