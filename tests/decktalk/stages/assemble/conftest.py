"""The project, the run and the rendered rows every assemble test measures, built once here.

A stage is handed parsed inputs and an open run, so these fixtures build both without a project and
without a machine that read anything. The toolchain is faked at the seam each module imports, which
is what keeps a test here about the cut, the mix and the publish rather than about ffmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, Take, Takes, Words
from decktalk.errors import Cancel
from decktalk.events import Event
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media import ffmpeg
from decktalk.results import CueTime, SectionCues, Word
from decktalk.settings import ToolsConfig
from decktalk.stages.assemble.cut import Rendered

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


def write_project(root: Path, toml: str = PAGES_TOML) -> Inputs:
    """One project on disk, parsed as a stage is handed it."""
    (root / "decktalk.toml").write_text(toml, encoding="utf-8")
    (root / "deck").mkdir(exist_ok=True)
    (root / "deck" / "index.html").write_text("<html></html>", encoding="utf-8")
    return Inputs.load(root, environ={})


@dataclass
class Opened:
    """One open run and every line it put on the stream, which is what a test reads it back from."""

    run: Run
    lines: list[Event] = field(default_factory=list)

    def notes(self) -> list[str]:
        """Every sentence the run said, which is what a stage says instead of printing."""
        return [line.message for line in self.lines if getattr(line, "message", None) is not None]

    def codes(self) -> list[str]:
        """The code of every judgement the run made, in the order it made them."""
        return [line.finding.code.name for line in self.lines if getattr(line, "finding", None) is not None]

    def progress(self) -> list[str]:
        """The label of every progress line, which is how far through its own work the stage said it was."""
        return [line.label for line in self.lines if getattr(line, "label", None) is not None]


def open_run(root: Path) -> Opened:
    """A run on a machine that read nothing, with every line it emits collected for the test."""
    machine = Machine(
        environ={},
        tables={},
        config_path=root / "config.toml",
        cwd=root,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(root / "cache"))),
    )
    opened = Opened(run=Run(machine, id="r1", cancel=Cancel(), root=root))
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
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    takes: list[Take] = []
    for number, (chapter, span, speech_end, words) in rows.items():
        digest = f"h{number:02d}"
        Words(words=tuple(words)).write(inputs.workspace.takes_dir / f"{digest}.words.json")
        takes.append(
            Take(
                section=number,
                key=f"{number:02d}",
                chapter=chapter,
                hash=digest,
                voiced=voiced,
                word_count=len(words),
                characters=sum(len(word.word) for word in words),
                estimated_seconds=span,
                duration_seconds=span,
                speech_end_seconds=speech_end,
                sound_end_seconds=span,
                spoken=" ".join(word.word for word in words),
            )
        )
    index = Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=tuple(takes))
    index.write(inputs.workspace.takes_path)
    return index


def cue_times(inputs: Inputs, rows: dict[int, dict[str, float]]) -> CueTimes:
    """Resolved cue times on disk, which is what an effect and a sound caption are placed by."""
    times = CueTimes(
        sections=tuple(
            SectionCues(
                section=number,
                key=f"{number:02d}",
                estimated=False,
                cues=tuple(CueTime(cue=cue, phrase=cue, seconds=at) for cue, at in cues.items()),
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
            note=f"{section.key}.webm",
            source=f"build/recordings/{section.key}.webm",
            audio=audio.get(section.number),
        )
        for section in inputs.document.sections
        if section.number in seconds
    ]


def durations(monkeypatch: pytest.MonkeyPatch, by_name: dict[str, float], default: float = 1.0) -> None:
    """Make every probe answer by file name, so several sections can be different lengths at once."""
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: by_name.get(Path(path).name, default))


@pytest.fixture
def rendering(fake_ffmpeg, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
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


@pytest.fixture(name="write_project")
def write_project_fixture():  # noqa: ANN201  (a fixture that hands back one helper)
    return write_project


@pytest.fixture(name="open_run")
def open_run_fixture():  # noqa: ANN201
    return open_run


@pytest.fixture(name="spoken")
def spoken_fixture():  # noqa: ANN201
    return spoken


@pytest.fixture(name="take_index")
def take_index_fixture():  # noqa: ANN201
    return take_index


@pytest.fixture(name="cue_times")
def cue_times_fixture():  # noqa: ANN201
    return cue_times


@pytest.fixture(name="rendered")
def rendered_fixture():  # noqa: ANN201
    return rendered


@pytest.fixture(name="durations")
def durations_fixture():  # noqa: ANN201
    return durations
