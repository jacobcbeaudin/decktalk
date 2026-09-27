"""The assembled project every verify test measures, with each ffmpeg call replaced by a number.

`verify` reads a finished film, so a test of it has to hand it one. Nothing here renders anything:
the cut list says where each section sits, the artifacts say what was promised, and every frame and
every sample the stage would read is a value the test names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from decktalk.artifacts import Cut, Cuts, RecordingLog
from decktalk.errors import Cancel
from decktalk.findings import Finding
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg, frames
from decktalk.media.pagereport import PageReport
from decktalk.results import SectionCues, Voicing
from support.runs import a_machine
from support.takes import a_take, write_takes

PAGES_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""
"""The two-page-section project every verify test starts from."""

SECTION_SECONDS = 5.0
"""How long each section of the test film runs, which keeps every probe well inside its section."""


CUE_SECONDS = 2.0
"""Where the one cue of the test film sits in its section, which is well inside it."""


@dataclass
class Measurements:
    """What the film would measure, as the numbers a test names instead of rendering them.

    A probe compares the reference frame with a frame after the cue, and a control compares two
    frames before it, so the share a comparison reads is decided by which side of the cue it ends
    on. Naming the two apart is what lets a case say a reveal happened without saying the picture
    was moving all along.
    """

    luma: float = 200.0
    changed: float = 5.0
    control: float = 0.0
    shares: Iterator[float] | None = None  # when set, each comparison reads the next of these instead
    series: list[tuple[float, float]] | None = None
    rms_dbfs: float = -80.0
    samples: list[int] | None = None
    duration: float = 2 * SECTION_SECONDS

    def share(self, until: float) -> float:
        """What one comparison ending at `until` reads, which is a probe past the cue and a control before it."""
        if self.shares is not None:
            return next(self.shares)
        return self.changed if until > CUE_SECONDS else self.control


class FakeDecoded:
    """A decoded film whose every comparison is the number the test named, and the plan it was asked for."""

    def __init__(self, said: Measurements, wanted: frames.Wanted) -> None:
        self.said = said
        self.wanted = wanted

    def changed(self, _t1: float, t2: float, **_kw: object) -> float:
        return self.said.share(t2)

    def series(self, *_args: object, **_kw: object) -> list[tuple[float, float]]:
        return list(self.said.series or [])


@contextmanager
def opened(root: Path) -> Iterator[Run]:
    """One run on a machine that read nothing, which is what the facade would hand the stage."""
    machine = a_machine(root)
    with machine.run(cancel=Cancel(), voice=Voicing.PLACEHOLDER, root=root) as run:
        yield run


def write_artifacts(inputs: Inputs, cue_times: dict[int, dict[str, float]]) -> None:
    """The take index, the cut list, the cue times and the narration a finished film leaves behind."""
    workspace = inputs.workspace
    workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    workspace.final_dir.mkdir(parents=True, exist_ok=True)
    workspace.sections_dir.mkdir(parents=True, exist_ok=True)
    sections = inputs.document.sections
    write_takes(inputs, *(a_take(section.number, seconds=SECTION_SECONDS, voiced=False) for section in sections))
    Cuts(
        fps=inputs.settings.video.output_fps,
        sections=tuple(
            Cut(
                section=section.number,
                key=section.key,
                kind="page",
                start=index * SECTION_SECONDS,
                end=(index + 1) * SECTION_SECONDS,
                source=f"build/recordings/{section.key}.webm",
                chapter=f"Section {section.number}",
            )
            for index, section in enumerate(inputs.document.sections)
        ),
    ).write(workspace.cuts_path)
    blocks = tuple(
        SectionCues(
            section=number,
            key=f"{number:02d}",
            estimated=True,
            cues=tuple({"cue": cue, "phrase": "hello", "seconds": at, "offset": 0.0} for cue, at in rows.items()),
        )
        for number, rows in cue_times.items()
    )
    workspace.cue_times_path.write_text(
        json.dumps({"sections": [block.model_dump(mode="json") for block in blocks]}), encoding="utf-8"
    )
    workspace.film.write_bytes(b"film")
    workspace.narration_path.write_bytes(b"narration")
    for section in inputs.document.sections:
        workspace.section_video(section.key).write_bytes(b"cut")


def write_log(inputs: Inputs, section: int, found: Sequence[Finding] = ()) -> None:
    """One recording log, so `verify` has something to repeat rather than measure again."""
    inputs.workspace.recordings_dir.mkdir(parents=True, exist_ok=True)
    RecordingLog(
        section=section,
        url="http://project.localhost/deck/index.html",
        input_hash="abc",
        requested_seconds=SECTION_SECONDS,
        settle_seconds=0.1,
        load_seconds=0.1,
        clock_start_seconds=0.2,
        findings=tuple(found),
        report=PageReport(),
    ).write(inputs.workspace.recording_log(f"{section:02d}"))


@pytest.fixture(autouse=True)
def measured(monkeypatch: pytest.MonkeyPatch) -> Measurements:
    """Every ffmpeg reading the stage would take, replaced by the number the test names.

    It is automatic because a test here that forgot it would shell out to a real ffmpeg against a
    file holding four bytes, and read a failure as a measurement.
    """
    said = Measurements()
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: said.duration)
    monkeypatch.setattr(frames, "luma_at", lambda _path, _t, crop=None: (said.luma / 2, said.luma))
    monkeypatch.setattr(frames, "decode", lambda _path, wanted: FakeDecoded(said, wanted))
    monkeypatch.setattr(audio, "rms_db", lambda _p, _start, _seconds: said.rms_dbfs)
    monkeypatch.setattr(audio, "pcm_span", lambda _p, _start, _seconds, **_kw: list(said.samples or []))
    return said


@pytest.fixture
def assembled(tmp_path: Path) -> Callable[..., Inputs]:
    """A factory for an assembled two-section project, so each test names its own cue times."""

    def make(
        cue_times: dict[int, dict[str, float]] | None = None, *, toml: str = PAGES_TOML, cues: dict | None = None
    ) -> Inputs:
        (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
        (tmp_path / "deck").mkdir(exist_ok=True)
        (tmp_path / "deck" / "index.html").write_text("<html></html>", encoding="utf-8")
        if cues is not None:
            (tmp_path / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
        inputs = Inputs.load(tmp_path, environ={})
        write_artifacts(inputs, cue_times or {})
        return inputs

    return make
