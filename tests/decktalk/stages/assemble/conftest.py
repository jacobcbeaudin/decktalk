"""The project, the run and the rendered rows every assemble test measures, built once here.

A stage is handed parsed inputs and an open run, so these fixtures build both without a project and
without a machine that read anything. The toolchain is faked at the seam each module imports, which
is what keeps a test here about the cut, the mix and the publish rather than about ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, Takes
from decktalk.errors import Cancel
from decktalk.events import FindingRaised, RunLog, StageProgress
from decktalk.inputs import Inputs
from decktalk.machine.run import Run
from decktalk.media import ffmpeg
from decktalk.results import CueTime, SectionCues, Word
from decktalk.stages.assemble.cut import Rendered
from support.fakes import FakeFfmpeg
from support.projects import load_project
from support.runs import RUN_ID, Watched, a_machine
from support.takes import a_take, narrated

PAGES_TOML = """
[project]
name = "t"

[narration]
lead_seconds = 0

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"

[[section]]
number = 3
page = "deck/index.html"
scene = "3"
"""
"""Three page sections and nothing else, for a test that wants no clip in the way."""

MID_CLIP_TOML = """
[project]
name = "t"

[narration]
lead_seconds = 0

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
clip = "media/broll.mp4"

[[section]]
number = 3
page = "deck/index.html"
scene = "3"

[[section]]
number = 4
page = "deck/index.html"
scene = "4"
"""
"""A clip between two page sections, which is what splits the narration into runs."""

TITLED_TOML = """
[project]
name = "t"

[narration]
lead_seconds = 0

[[section]]
number = 1
chapter = "Open"
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
chapter = "The edit"
clip = "media/before.mov"
words = "media/before.words.json"

[[section]]
number = 3
chapter = "The edit"
page = "deck/index.html"
scene = "3"

[[section]]
number = 4
chapter = "Close"
page = "deck/index.html"
scene = "4"
"""
"""Four sections where two share one chapter, which is what a shared chapter marker is read from."""


def draw_slate(out: Path, **_named: object) -> Path:
    """A slate drawn with no browser, which writes an empty picture where the real one would."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"")
    return out


def write_project(root: Path, toml: str = PAGES_TOML) -> Inputs:
    """One project on disk, parsed as a stage is handed it."""
    return load_project(root, toml, page="<html></html>")


class Opened(Watched):
    """One open run and every line it put on the stream, which is what a test reads it back from."""

    def notes(self) -> list[str]:
        """Every sentence the run said, which is what a stage says instead of printing."""
        return [line.message for line in self.of(RunLog)]

    def codes(self) -> list[str]:
        """The code of every judgement the run made, in the order it made them."""
        return [line.finding.code.name for line in self.of(FindingRaised)]

    def progress(self) -> list[str]:
        """The label of every progress line, which is how far through its own work the stage said it was."""
        return [line.label for line in self.of(StageProgress)]


def open_run(root: Path) -> Opened:
    """A run on a machine that read nothing, with every line it emits collected for the test."""
    machine = a_machine(root)
    opened = Opened(run=Run(machine, id=RUN_ID, cancel=Cancel(), root=root))
    machine.events.subscribe(opened.lines.append)
    return opened


def spoken(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    """Evenly spaced words, as a placeholder narration gives them."""
    words: list[Word] = []
    at = start
    for word in text.split():
        words.append(Word(word=word, start=round(at, 3), end=round(at + 0.3, 3)))
        at += step
    return words


def take_index(inputs: Inputs, rows: dict[int, tuple[str, float, float | None, list[Word]]], *, voiced: bool = True
               ) -> Takes:  # fmt: skip
    """A take index built from (chapter, span, speech end, words) per section, with its words on disk.

    Every span is the take alone, because these projects set `[narration] lead_seconds = 0`, so a
    section starts where the one before it ended and the words sit where the take names them.
    """
    takes = [
        a_take(
            number,
            seconds=span,
            chapter=chapter,
            voiced=voiced,
            speech_end_seconds=speech_end,
            spoken=" ".join(word.word for word in words),
        )
        for number, (chapter, span, speech_end, words) in rows.items()
    ]
    return narrated(inputs, *takes, words={number: row[3] for number, row in rows.items()})


def cue_times(inputs: Inputs, rows: dict[int, dict[str, float]]) -> CueTimes:
    """Resolved cue times on disk, which is what an effect and a sound caption are placed by."""
    times = CueTimes(
        sections=tuple(
            SectionCues(
                section=number,
                key=f"{number:02d}",
                estimated=False,
                cues=tuple(CueTime(id=cue, phrase=cue, seconds=at) for cue, at in cues.items()),
            )
            for number, cues in rows.items()
        )
    )
    times.write(inputs.workspace.cue_times_path)
    return times


def rendered(inputs: Inputs, seconds: dict[int, float], *, audio: dict[int, Path] | None = None) -> list[Rendered]:
    """The rows the cut would hand the mix, without running one ffmpeg call to make them."""
    audio = audio or {}
    return [
        Rendered(
            section=section,
            path=inputs.workspace.section_video(section.key),
            seconds=seconds[section.number],
            source=Path(f"build/recordings/{section.key}.webm"),
            audio=audio.get(section.number),
        )
        for section in inputs.document.sections
        if section.number in seconds
    ]


def durations(monkeypatch: pytest.MonkeyPatch, by_name: dict[str, float], default: float = 1.0) -> None:
    """Make every probe answer by file name, so several sections can be different lengths at once."""
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: by_name.get(Path(path).name, default))


@pytest.fixture
def rendering(fake_ffmpeg: FakeFfmpeg, monkeypatch: pytest.MonkeyPatch) -> FakeFfmpeg:
    """`fake_ffmpeg` whose outputs are not empty, because a published film is checked for content.

    The shared fixture writes a zero-byte file at each output, and `publish` refuses to rename an
    empty render into place, which is the one check that would otherwise never be reached here.
    """

    def run(*args: str) -> None:
        fake_ffmpeg.calls.append(list(args))
        out = Path(args[-1])
        if out.parent.is_dir():
            out.write_bytes(b"one frame")

    monkeypatch.setattr(ffmpeg, "run", run)
    return fake_ffmpeg
