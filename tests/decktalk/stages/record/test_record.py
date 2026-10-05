"""Recording every page section, and the order the recorder writes in.

The rule these tests exist for is the pair on disk. The log of the recording being replaced goes
before anything is captured and the log of what was recorded is written last, so a run that stops in
between leaves a webm with no log or a log with no start, and the next run records the
section again rather than trimming a new picture at an old moment.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.errors import InputError, NotBuiltError
from decktalk.events import Event, Level, RunLog, SectionDone, StageProgress
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media import browser, ffmpeg, frames, recording
from decktalk.media.pagereport import PageReport, Recording
from decktalk.media.recording import RecordingSink
from decktalk.pipeline import Outcome
from decktalk.results import Word
from decktalk.stages.record import pool, record, stale_recording
from support.logs import decisions
from support.pages import TWO_SCENE_PAGE, a_report
from support.projects import load_project
from support.runs import a_run
from support.takes import a_take, narrated, write_takes

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

SPAN_SECONDS = 4.0
"""How long each section of the test project speaks for, which is what the recorder asks the page for."""

COVER_CHROMA = 200.0
"""A chroma high enough on both planes that a frame reads as the recorder's own cover."""

BRIGHT_LUMA = 200.0
"""A luma bright enough that no frame of a test recording reads as black."""

LAUNCH_WAIT_SECONDS = 10.0
"""How long a launch waits for the other worker's, which only a pool that never starts one runs out."""


def a_project(tmp_path: Path, *, takes: bool = True, extra: str = "", machine: dict[str, Any] | None = None) -> Inputs:
    inputs = load_project(tmp_path, TOML + extra, page=TWO_SCENE_PAGE)
    if takes:
        write_takes(inputs, *(a_take(section, seconds=SPAN_SECONDS, voiced=False) for section in (1, 2)))
    return Inputs.load(tmp_path, environ={}, machine=machine)


class Driven:
    """A recorder that writes what a real one writes, in the order a real one writes it."""

    def __init__(self, report: PageReport | None = None, *, stalls: int = 0) -> None:
        self.report = report or a_report()
        self.stalls = stalls
        self.urls: list[str] = []
        self.order: list[str] = []
        self.launched = 0
        self.policies: list[str] = []
        self.missing: tuple[str, ...] = ()
        # When set, no launch returns until every party has launched, so no worker can finish first.
        self.launches_together: threading.Barrier | None = None
        # Launches released together count at once, and an unguarded increment could lose one.
        self.counting = threading.Lock()

    @contextmanager
    def chromium(self, _executable: str = "", *, policy: str = "trusted", **_launch: object) -> Iterator[object]:
        if self.launches_together is not None:
            self.launches_together.wait()
        with self.counting:
            self.launched += 1
        self.policies.append(policy)
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
            missing=self.missing,
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
    # One recording at a time, so the order a test reads is the order a single recorder writes in.
    monkeypatch.setattr(pool, "available_cpus", lambda: float(pool.CPUS_PER_RECORDING))
    monkeypatch.setattr(browser, "chromium", fake.chromium)
    monkeypatch.setattr(recording, "record_page", fake.record_page)
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
    result = record(inputs, a_run(inputs.root))
    assert [row.section for row in result.sections] == [1, 2]
    assert [row.kept for row in result.sections] == [False, False]
    assert [row.file for row in result.sections] == [Path("build/recordings/01.webm"), Path("build/recordings/02.webm")]
    assert driven.launched == 1


def test_a_host_that_marks_the_page_untrusted_records_it_under_that_policy(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path, machine={"record": {"page_policy": "untrusted"}})
    record(inputs, a_run(inputs.root))
    assert driven.policies == ["untrusted"]


@pytest.mark.usefixtures("driven")
def test_a_row_carries_the_length_and_the_frame_count_of_its_recording(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    row = record(inputs, a_run(inputs.root)).sections[0]
    assert row.seconds == SPAN_SECONDS
    assert row.frames == round(SPAN_SECONDS * 25)


def test_the_log_is_cleared_before_the_capture_and_written_after_it(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    assert driven.order[:3] == ["cleared 01.webm", "placed 01.webm", "logged 01.webm"]


@pytest.mark.usefixtures("driven")
def test_the_finished_log_carries_narration_t0_the_frames_and_the_judgements(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    assert log.section == 1
    assert log.start is not None
    assert log.checks is not None
    assert log.checks.wanted_seconds == pytest.approx(SPAN_SECONDS + 0.3)


def test_a_run_stopped_before_it_measured_leaves_a_log_the_next_run_records_again(
    tmp_path: Path, driven: Driven
) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    log.model_copy(update={"start": None}).write(inputs.workspace.recording_log("01"))
    driven.order.clear()
    record(inputs, a_run(inputs.root))
    assert "cleared 01.webm" in driven.order


def test_a_section_nothing_moved_under_is_kept_and_no_browser_opens(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    launched = driven.launched
    again = record(inputs, a_run(inputs.root))
    assert [row.kept for row in again.sections] == [True, True]
    assert driven.launched == launched


@pytest.mark.usefixtures("driven")
def test_a_section_whose_recording_still_stands_ends_as_kept_as_a_kept_take_does(tmp_path: Path) -> None:
    """A kept recording ended as `skipped`, which is a section the run left out, while narrate says `kept`."""
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    lines: list[Event] = []
    record(inputs, a_run(inputs.root, lines=lines))
    ended = [(line.section, line.outcome) for line in lines if isinstance(line, SectionDone)]
    assert ended == [(1, Outcome.KEPT), (2, Outcome.KEPT)]


@pytest.mark.usefixtures("driven")
def test_naming_a_section_records_it_although_nothing_moved(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    again = record(inputs, a_run(inputs.root), only=[1])
    assert [row.section for row in again.sections] == [1]
    assert again.sections[0].kept is False


@pytest.mark.usefixtures("driven")
def test_forcing_a_run_records_every_section_again(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    again = record(inputs, a_run(inputs.root), force=True)
    assert [row.kept for row in again.sections] == [False, False]


@pytest.mark.usefixtures("driven")
def test_every_section_recorded_or_kept_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A kept section emitted `skipped` with no reason, so a reader could not tell forced from unchanged."""
    inputs = a_project(tmp_path)

    def said(**options: Any) -> list[tuple[object, ...]]:
        record(inputs, a_run(inputs.root), **options)
        return decisions(caplog, "recording", "section", "hit", "why")

    with caplog.at_level("DEBUG", logger="decktalk"):
        assert said() == [(1, False, "changed"), (2, False, "changed")]
        assert said() == [(1, True, "unchanged"), (2, True, "unchanged")]
        assert said(only=[1]) == [(1, False, "named")]
        assert said(force=True) == [(1, False, "forced"), (2, False, "forced")]


@pytest.mark.usefixtures("driven")
def test_a_run_reports_on_the_sections_it_records_and_not_on_a_recording_it_never_touched(tmp_path: Path) -> None:
    """`record --section 1` reported FILE_MISSING for section 2, whose missing recording `status` reports."""
    inputs = a_project(tmp_path)
    lines: list[Event] = []
    result = record(inputs, a_run(inputs.root, lines=lines), only=[1])
    assert [row.section for row in result.sections] == [1]
    assert result.findings == () and result.ok
    assert [line.message for line in lines if isinstance(line, RunLog) and line.section == 2] == []


@pytest.mark.usefixtures("driven")
def test_a_run_whose_own_section_is_missing_its_page_is_refused(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    (tmp_path / "deck" / "index.html").unlink()
    with pytest.raises(InputError, match="section 1 plays deck/index.html, which is not there"):
        record(inputs, a_run(inputs.root), only=[1])


def project_with_damaged_words(tmp_path: Path, damaged: int, *, voiced: bool = True) -> tuple[Inputs, Path]:
    """Both sections narrated and recorded, then one section's words file damaged on disk.

    A voiced take's words are the provider's, a paid record, and a placeholder's are estimated, a cache.
    """
    inputs = a_project(tmp_path, takes=False)
    said = (Word(word="a", start=0.0, end=0.4),)
    rows = (a_take(n, seconds=SPAN_SECONDS, voiced=voiced) for n in (1, 2))
    takes = narrated(inputs, *rows, words={1: said, 2: said})
    record(inputs, a_run(inputs.root))
    take = takes.of(damaged)
    assert take is not None
    path = inputs.take_places.find(take.digest).words
    assert path.is_file()
    path.write_text("{damaged", encoding="utf-8")
    return inputs, path


@pytest.mark.usefixtures("driven")
@pytest.mark.parametrize("paid", [True, False], ids=["provider words", "estimated words"])
def test_a_run_is_never_refused_over_the_words_of_a_section_it_passes_over(tmp_path: Path, paid: bool) -> None:
    """Section 2's damaged words refused `record --section 1`, which records nothing from them."""
    inputs, damaged = project_with_damaged_words(tmp_path, 2, voiced=paid)
    lines: list[Event] = []
    result = record(inputs, a_run(inputs.root, lines=lines), only=[1])
    assert [row.section for row in result.sections] == [1]
    said = [line.message for line in lines if isinstance(line, RunLog) and line.level is Level.WARNING]
    assert len(said) == 1 and damaged.name in said[0] and said[0].startswith("Section 2 "), said


@pytest.mark.usefixtures("driven")
def test_a_run_that_records_the_section_with_damaged_provider_words_is_refused(tmp_path: Path) -> None:
    inputs, damaged = project_with_damaged_words(tmp_path, 2)
    with pytest.raises(InputError, match="Only voicing this take again gives these words back") as refused:
        record(inputs, a_run(inputs.root), only=[2])
    assert damaged.name in str(refused.value)


@pytest.mark.usefixtures("driven")
def test_a_run_that_records_the_section_with_damaged_estimated_words_is_not_built(tmp_path: Path) -> None:
    inputs, damaged = project_with_damaged_words(tmp_path, 2, voiced=False)
    with pytest.raises(NotBuiltError, match="cannot be read as") as refused:
        record(inputs, a_run(inputs.root), only=[2])
    assert damaged.name in str(refused.value)


def test_a_page_that_stalls_is_recorded_again_while_the_machine_is_quieter(
    tmp_path: Path, driven: Driven, caplog: pytest.LogCaptureFixture
) -> None:
    inputs = a_project(tmp_path)
    driven.stalls = 1
    with caplog.at_level("WARNING", logger="decktalk"):
        record(inputs, a_run(inputs.root), only=[1])
    assert driven.urls.count(driven.urls[0]) == 2
    retried = [vars(record)["data"] for record in caplog.records if "recorded again" in record.getMessage()]
    assert [(row["attempt"], row["retries"]) for row in retried] == [(1, inputs.settings.record.retries)]


def test_what_the_page_could_not_honour_reaches_the_result_as_its_own_code(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    driven.report = a_report(warnings=[{"code": "PAGE_KATEX_MISSING", "message": "KaTeX never arrived."}])
    result = record(inputs, a_run(inputs.root), only=[1])
    assert Code.PAGE_KATEX_MISSING in {row.code for row in result.findings}


@pytest.mark.usefixtures("driven")
def test_one_progress_line_is_emitted_per_section(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    lines: list[Event] = []
    record(inputs, a_run(inputs.root, lines=lines))
    counted = [line for line in lines if isinstance(line, StageProgress)]
    assert [(line.done, line.total) for line in counted] == [(1, 2), (2, 2)]


@pytest.mark.usefixtures("driven")
def test_every_file_the_run_wrote_is_reported(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    result = record(inputs, a_run(inputs.root), only=[1])
    assert set(result.written) == {Path("build/recordings/01.webm"), Path("build/recordings/01.json")}


@pytest.mark.usefixtures("driven")
def test_a_section_no_page_section_carries_is_refused_with_the_page_sections_named(tmp_path: Path) -> None:
    """`record --section 99` said no page section had a length to record, and named none it could record."""
    inputs = a_project(tmp_path)
    with pytest.raises(InputError, match=r"no page section matches \[99\]") as refused:
        record(inputs, a_run(inputs.root), only=[99])
    assert refused.value.hint == "The page sections are [1, 2]."


@pytest.mark.usefixtures("driven")
def test_an_empty_selection_is_refused_with_the_page_sections_named(tmp_path: Path) -> None:
    """An empty selection selects nothing, which is not the same as no selection at all."""
    inputs = a_project(tmp_path)
    with pytest.raises(InputError, match=r"no page section matches \[\]") as refused:
        record(inputs, a_run(inputs.root), only=())
    assert refused.value.hint == "The page sections are [1, 2]."


@pytest.mark.usefixtures("driven")
def test_a_project_with_no_take_index_is_not_built_yet(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, takes=False)
    with pytest.raises(NotBuiltError):
        record(inputs, a_run(inputs.root))


@pytest.mark.usefixtures("driven")
def test_one_rule_decides_whether_a_recording_still_stands(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    section = inputs.document.page_sections[0]
    assert stale_recording(inputs, section) == "section 1 has no recording"
    record(inputs, a_run(inputs.root))
    assert stale_recording(inputs, section) is None
    (tmp_path / "deck" / "index.html").write_text(TWO_SCENE_PAGE.replace("one</p>", "one more</p>"), encoding="utf-8")
    assert "changed since it was recorded" in (stale_recording(Inputs.load(tmp_path, environ={}), section) or "")


def test_sections_recorded_at_once_come_back_in_order_with_their_own_pair_of_lines(
    tmp_path: Path, driven: Driven, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record waited for every section in turn, so a film took as long to record as it runs."""
    monkeypatch.setattr(pool, "available_cpus", lambda: 8.0)
    # One worker could otherwise take both sections before the other worker takes its first.
    driven.launches_together = threading.Barrier(2, timeout=LAUNCH_WAIT_SECONDS)
    inputs = a_project(tmp_path)
    lines: list[Event] = []
    result = record(inputs, a_run(inputs.root, lines=lines))
    assert [row.section for row in result.sections] == [1, 2]
    assert driven.launched == 2, "each worker drives a Chromium of its own"
    for number in (1, 2):
        paired = [type(line).__name__ for line in lines if getattr(line, "section", None) == number]
        assert paired[0] == "SectionStart" and "SectionDone" in paired, paired
    assert [(line.done, line.section) for line in lines if isinstance(line, StageProgress)] == [(1, 1), (2, 2)]


def test_a_file_the_page_asked_for_and_the_project_lacks_is_said_on_the_stream(tmp_path: Path, driven: Driven) -> None:
    """It went to a warning log the command line never showed, and never reached `--json` or a host."""
    inputs = a_project(tmp_path)
    driven.missing = ("media/gone.png",)
    lines: list[Event] = []
    record(inputs, a_run(inputs.root, lines=lines), only=[1])
    said = [line.message for line in lines if isinstance(line, RunLog) and line.level is Level.WARNING]
    assert said == ["Section 1 asked for media/gone.png, which the project does not have."]


@pytest.mark.usefixtures("driven")
def test_a_page_section_numbered_zero_is_recorded_and_logged(tmp_path: Path) -> None:
    """The log refused section 0, which the script and the project number a section by as well as any other."""
    inputs = load_project(tmp_path, TOML.replace("number = 1", "number = 0"), page=TWO_SCENE_PAGE)
    write_takes(inputs, *(a_take(section, seconds=SPAN_SECONDS, voiced=False) for section in (0, 2)))
    inputs = Inputs.load(tmp_path, environ={})
    result = record(inputs, a_run(inputs.root))
    assert [row.section for row in result.sections] == [0, 2]
    log = RecordingLog.read(inputs.workspace.recording_log("00"))
    assert log is not None and log.section == 0
