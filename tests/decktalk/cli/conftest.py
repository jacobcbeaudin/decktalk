"""What every command-line test drives: the real parser, with the library faked at one seam.

A test here writes a command line and reads what came back on the two streams and in the exit code,
which is the whole contract the command line publishes. The parser, the help, the derivation of the
shared flags, the renderer and the exit code are all real. What is faked is the one seam a command
reaches the library through, which is `Session.project` and `Session.machine`, so a test exercises
the client without a browser, an encoder or a voice.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from decktalk.cli import catalog, main
from decktalk.cli import session as sessions
from decktalk.events import Event, Events
from decktalk.findings import ERRORS_FAIL, Applicability, Code, EditFix, Finding, Location, Threshold
from decktalk.project import Project
from decktalk.results import (
    ApiKeyState,
    BuildResult,
    CheckResult,
    DoctorResult,
    InitResult,
    InstallResult,
    Result,
    ServeResult,
    StatusResult,
    StoryboardResult,
    WordsResult,
)
from support.costs import a_cost

TTY = "TTY_COMPATIBLE"
"""The variable Rich reads to be told there is a terminal here, which is how both paths are run."""


@dataclass(frozen=True)
class Ran:
    """One command line, as its caller sees it: the exit code and the two streams."""

    exit_code: int
    out: str
    err: str


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """Run one command line through the real entry point and give back what a caller would see."""

    def go(*argv: str, stdin: str = "", tty: bool = False) -> Ran:
        monkeypatch.setenv(TTY, "1" if tty else "0")
        monkeypatch.delenv("NO_COLOR", raising=False)
        runner = CliRunner()
        with runner.isolation(input=stdin) as (out, err, _):
            code = main(list(argv))
            sys.stdout.flush()
            sys.stderr.flush()
            return Ran(code, out.getvalue().decode(), err.getvalue().decode())

    return go


class Fake:
    """A stand-in for `Project` or `Machine` that records every call and answers as it was told to.

    Every command is a thin client of one of those two objects, so a fake at that seam is the whole
    of what a command-line test needs to say what the client did. A result it answers with has its
    `ok` judged the way the library judges it: by the threshold the project was opened with, or by
    the one a machine call was handed.
    """

    def __init__(self, **answers: object) -> None:
        self.answers = dict(answers)
        self.threshold: Threshold = ERRORS_FAIL
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.events = Events()
        self.emits: dict[str, tuple[type[Event], dict[str, object]]] = {}
        self.root = Path.cwd()

    def project(self) -> Project:
        """This fake as the project it stands in for, for a test that hands it to a function directly."""
        return cast("Project", self)

    def opened(self, threshold: Threshold) -> Fake:
        """This fake as the project a session opened, with the threshold it was opened with."""
        self.threshold = threshold
        return self

    def called(self, name: str) -> dict[str, object]:
        """The keywords one call was made with, which is what a client test asserts on."""
        for made, _, keywords in self.calls:
            if made == name:
                return keywords
        raise AssertionError(f"{name} was never called, and these were: {[made for made, _, _ in self.calls]}")

    def __getattr__(self, name: str) -> Callable[..., object]:
        def call(*args: object, **keywords: object) -> object:
            self.calls.append((name, args, keywords))
            line = self.emits.get(name)
            if line is not None:
                self.events.emit("r", line[0], **line[1])
            answer = self.answers.get(name)
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, Result) and answer.error is None:
                threshold = cast("Threshold", keywords.get("threshold", self.threshold))
                ok = not threshold.fails(answer.findings)
                return answer if answer.ok is ok else answer.model_copy(update={"ok": ok})
            return answer

        return call


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch):
    """Put a fake project behind `Session.project`, which is the seam every project command uses."""

    def install(**answers: object) -> Fake:
        fake = Fake(**answers)
        monkeypatch.setattr(sessions.Session, "project", lambda self: fake.opened(self.threshold))
        return fake

    return install


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch):
    """Put a fake machine behind `Session.machine`, which is the seam every machine command uses."""

    def install(**answers: object) -> Fake:
        fake = Fake(**answers)
        monkeypatch.setattr(sessions.Session, "machine", property(lambda self: fake))
        return fake

    return install


def commands() -> dict[str, dict[str, Any]]:
    """Every command the parser really has, by the words a caller types to reach it."""
    return {str(row["command"]): row for row in catalog.walk()}


def finding(code: Code = Code.CUE_UNRESOLVED, *, fix: bool = False) -> Finding:
    """One judgement, with a safe fix under it when the test is about fixing."""
    repair = (
        EditFix(
            title='Change the phrase to "the same thing in code".',
            applicability=Applicability.SAFE,
            edits=({"file": "cues.json", "line": 14, "new": '"phrase": "the same thing in code"'},),
        )
        if fix
        else None
    )
    return Finding.model_validate(
        {
            "code": code,
            "message": 'The phrase "same thing in the code" is not spoken in section 2.',
            "location": Location(where="cues.json", file=Path("cues.json"), line=14, section=2),
            "fix": repair,
        }
    )


ANSWERS: dict[str, Result] = {
    "init": InitResult(ok=True, run="r", root=Path("demo"), name="demo", example="starter", skills_written=True),
    "install": InstallResult(ok=True, run="r", tools=(), cache=Path("cache")),
    "doctor": DoctorResult(
        ok=True,
        run="r",
        tools=(),
        cache=Path("cache"),
        python="3.12",
        platform="test",
        api_key_state=ApiKeyState.MISSING,
    ),
    "status": StatusResult(
        ok=True, run="r", name="demo", script=Path("script.md"), cues_file=Path("cues.json"), sections=()
    ),
    "check": CheckResult(
        ok=True, run="r", judged=(Path("script.md"),), pages_opened=True, frames_compared=True, cost=a_cost()
    ),
    "words": WordsResult(ok=True, run="r", sections=()),
    "storyboard": StoryboardResult(ok=True, run="r", storyboard=Path("build/storyboard.html"), panels=()),
    "serve": ServeResult(ok=True, run="r", url="http://127.0.0.1:8000", port=8000),
    "build": BuildResult(ok=True, run="r", stages=(), spend=False, cost=a_cost(), elapsed_seconds=1.0),
}
"""One prepared answer per command, so a client test says what it asked for rather than what it got."""


@pytest.fixture
def answers() -> dict[str, Result]:
    """One prepared result per command, which a client test hands to its fake."""
    return dict(ANSWERS)
