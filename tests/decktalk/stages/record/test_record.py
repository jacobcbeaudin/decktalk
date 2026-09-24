"""Recording every page section, and the order the recorder writes in.

The rule these tests exist for is the pair on disk. The log of the recording being replaced goes
before anything is captured and the log of what was recorded is written last, so a run that stops in
between leaves a webm with no log or a log with no narration t=0, and the next run records the
section again rather than trimming a new picture at an old moment.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from decktalk.artifacts import RecordingLog, Take, Takes
from decktalk.errors import Cancel, NotBuiltError
from decktalk.events import Event, Progress
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media import browser, ffmpeg, frames
from decktalk.media.browser import Recording, RecordingSink
from decktalk.media.pagereport import PageReport
from decktalk.results import Voicing
from decktalk.stages.record import record, stale_recording
from support.projects import write_project

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

PAGE = """<!doctype html><html><body>
<div data-scene="1"><template data-slide="1.1"><p data-in="open">one</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-in="open">two</p></template></div>
</body></html>
"""

SPAN_SECONDS = 4.0
"""How long each section of the test project speaks for, which is what the recorder asks the page for."""

COVER_CHROMA = 200.0
"""A chroma high enough on both planes that a frame reads as the recorder's own cover."""

BRIGHT_LUMA = 200.0
"""A luma bright enough that no frame of a test recording reads as black."""


def a_take(section: int) -> Take:
    return Take(
        section=section,
        key=f"{section:02d}",
        chapter=f"Section {section}",
        hash=f"hash{section}",
        voiced=False,
        word_count=8,
        characters=40,
        estimated_seconds=SPAN_SECONDS,
        duration_seconds=SPAN_SECONDS,
        speech_end_seconds=SPAN_SECONDS,
        sound_end_seconds=SPAN_SECONDS,
        spoken="one two three",
    )


def a_project(tmp_path: Path, *, takes: bool = True) -> Inputs:
    write_project(tmp_path, TOML)
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    (deck / "index.html").write_text(PAGE, encoding="utf-8")
    inputs = Inputs.load(tmp_path, environ={})
    if takes:
        Takes(
            script="script.md",
            model="test-model",
            output_format="mp3_44100_128",
            sections=(a_take(1), a_take(2)),
        ).write(inputs.workspace.takes_path)
    return Inputs.load(tmp_path, environ={})


def a_run(inputs: Inputs, lines: list[Event] | None = None) -> Run:
    machine = Machine(
        environ={}, tables={}, config_path=inputs.root / "machine.toml", cwd=inputs.root, toolchain=Toolchain()
    )
    if lines is not None:
        machine.events.subscribe(lines.append)
    return Run(machine, id="run-1", cancel=Cancel(), voice=Voicing.PLACEHOLDER, root=inputs.root)


def a_report(**fields: object) -> PageReport:
    return PageReport.model_validate({"version": "0.5.0", "mode": "cue", "scene": "1", "slide": "1.1", **fields})


class Driven:
    """A recorder that writes what a real one writes, in the order a real one writes it."""

    def __init__(self, report: PageReport | None = None, *, stalls: int = 0) -> None:
        self.report = report or a_report()
        self.stalls = stalls
        self.urls: list[str] = []
        self.order: list[str] = []
        self.launched = 0

    @contextmanager
    def chromium(self, _browser_path: str = "") -> Iterator[object]:
        self.launched += 1
        yield object()

    def record_page(
        self,
        _browser: object,
        url: str,
        seconds: float,
        out: Path,
        *,
        log_sink: RecordingSink,
        **_kwargs: object,
    ) -> Recording:
        self.urls.append(url)
        log_sink.clear()
        self.order.append(f"cleared {out.name}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"webm")
        self.order.append(f"placed {out.name}")
        report = self.report
        if self.stalls:
            self.stalls -= 1
            report = a_report(frameGaps=[{"at": 1.0, "ms": 5000}])
        recording = Recording(
            url=url,
            assets=("deck/index.html",),
            external=(),
            requested_seconds=seconds,
            load_seconds=0.2,
            settle_seconds=0.5,
            clock_start_seconds=1.5,
            page_errors=(),
            report=report,
        )
        log_sink.write(recording)
        self.order.append(f"logged {out.name}")
        return recording


@pytest.fixture
def driven(monkeypatch: pytest.MonkeyPatch) -> Driven:
    """The media layer replaced at the two seams `record` reaches it through, and no ffmpeg behind it."""
    fake = Driven()
    monkeypatch.setattr(browser, "chromium", fake.chromium)
    monkeypatch.setattr(browser, "record_page", fake.record_page)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: SPAN_SECONDS)
    monkeypatch.setattr(frames, "luma_at", lambda _path, _at, **_kwargs: (90.0, BRIGHT_LUMA))
    monkeypatch.setattr(
        frames,
        "frame_stats",
        lambda _path, _seconds: [
            frames.FrameStats(pts=0.0, yavg=100.0, ymax=BRIGHT_LUMA, uavg=COVER_CHROMA, vavg=COVER_CHROMA),
            frames.FrameStats(pts=0.04, yavg=90.0, ymax=BRIGHT_LUMA, uavg=128.0, vavg=128.0),
        ],
    )
    return fake


def test_every_page_section_is_recorded_and_reported_in_section_order(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    result = record(inputs, a_run(inputs))
    assert [row.section for row in result.sections] == [1, 2]
    assert [row.kept for row in result.sections] == [False, False]
    assert [row.file for row in result.sections] == [Path("build/recordings/01.webm"), Path("build/recordings/02.webm")]
    assert driven.launched == 1


@pytest.mark.usefixtures("driven")
def test_a_row_carries_the_length_and_the_frame_count_of_its_recording(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    row = record(inputs, a_run(inputs)).sections[0]
    assert row.seconds == SPAN_SECONDS
    assert row.frames == round(SPAN_SECONDS * 25)


def test_the_log_is_cleared_before_the_capture_and_written_after_it(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    assert driven.order[:3] == ["cleared 01.webm", "placed 01.webm", "logged 01.webm"]


@pytest.mark.usefixtures("driven")
def test_the_finished_log_carries_narration_t0_the_frames_and_the_judgements(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    assert log.section == 1
    assert log.t0_seconds is not None
    assert log.checks is not None
    assert log.checks.wanted_seconds == pytest.approx(SPAN_SECONDS + 0.3)


def test_a_run_stopped_before_it_measured_leaves_a_log_the_next_run_records_again(
    tmp_path: Path, driven: Driven
) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    log.model_copy(update={"t0_seconds": None}).write(inputs.workspace.recording_log("01"))
    driven.order.clear()
    record(inputs, a_run(inputs))
    assert "cleared 01.webm" in driven.order


def test_a_section_nothing_moved_under_is_kept_and_no_browser_opens(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    launched = driven.launched
    again = record(inputs, a_run(inputs))
    assert [row.kept for row in again.sections] == [True, True]
    assert driven.launched == launched


@pytest.mark.usefixtures("driven")
def test_naming_a_section_records_it_although_nothing_moved(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    again = record(inputs, a_run(inputs), only=[1])
    assert [row.section for row in again.sections] == [1]
    assert again.sections[0].kept is False


@pytest.mark.usefixtures("driven")
def test_forcing_a_run_records_every_section_again(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs))
    again = record(inputs, a_run(inputs), force=True)
    assert [row.kept for row in again.sections] == [False, False]


@pytest.mark.usefixtures("driven")
def test_a_section_the_run_passed_over_with_no_recording_at_all_is_a_missing_file(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    result = record(inputs, a_run(inputs), only=[1])
    assert [row.code for row in result.findings] == [Code.FILE_MISSING]
    assert result.findings[0].location.section == 2
    assert not result.ok


def test_a_page_that_stalls_is_recorded_again_while_the_machine_is_quieter(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    driven.stalls = 1
    record(inputs, a_run(inputs), only=[1])
    assert driven.urls.count(driven.urls[0]) == 2


def test_what_the_page_could_not_honour_reaches_the_result_as_its_own_code(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    driven.report = a_report(warnings=[{"code": "PAGE_KATEX_MISSING", "message": "KaTeX never arrived."}])
    result = record(inputs, a_run(inputs), only=[1])
    assert Code.PAGE_KATEX_MISSING in {row.code for row in result.findings}


@pytest.mark.usefixtures("driven")
def test_one_progress_line_is_emitted_per_section(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    lines: list[Event] = []
    record(inputs, a_run(inputs, lines))
    counted = [line for line in lines if isinstance(line, Progress)]
    assert [(line.done, line.total) for line in counted] == [(1, 2), (2, 2)]


@pytest.mark.usefixtures("driven")
def test_every_file_the_run_wrote_is_reported(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    result = record(inputs, a_run(inputs), only=[1])
    assert set(result.written) == {Path("build/recordings/01.webm"), Path("build/recordings/01.json")}


@pytest.mark.usefixtures("driven")
def test_a_project_with_no_take_index_is_not_built_yet(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, takes=False)
    with pytest.raises(NotBuiltError):
        record(inputs, a_run(inputs))


@pytest.mark.usefixtures("driven")
def test_one_rule_decides_whether_a_recording_still_stands(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    section = inputs.document.page_sections[0]
    assert stale_recording(inputs, section) == "section 1 has no recording"
    record(inputs, a_run(inputs))
    assert stale_recording(inputs, section) is None
    (tmp_path / "deck" / "index.html").write_text(PAGE.replace("one</p>", "one more</p>"), encoding="utf-8")
    assert "changed since it was recorded" in (stale_recording(Inputs.load(tmp_path, environ={}), section) or "")
