"""Recording every page section, and the order the recorder writes in.

The rule these tests exist for is the pair on disk. A new video is drafted beside the old one, so a
recording that fails leaves the old video and its log. Once the draft is whole the old log goes, the
draft takes the video's place and the log of what was recorded is written last, so a run that stops
in between leaves a video with no log or a log with no checks, and the next run records the section
again rather than assembling a picture nobody judged.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.errors import InputError, NotBuiltError, ToolError
from decktalk.events import Event, Level, RunLog, SectionDone, StageProgress
from decktalk.findings import Code
from decktalk.inputs import Inputs, programs
from decktalk.media import audio, browser, ffmpeg, frames, stepping
from decktalk.media.pagereport import PageReport, Recording
from decktalk.page import Q
from decktalk.pipeline import Outcome
from decktalk.results import Word
from decktalk.settings import PROJECT_FILE, Settings
from decktalk.stages.cue import cue
from decktalk.stages.narrate import narrate
from decktalk.stages.record import LogSink, plan, pool, record, stale_recording
from decktalk.stages.status import status
from support.logs import decisions
from support.pages import TWO_SCENE_PAGE, a_report
from support.projects import load_project
from support.runs import Watched, a_run, notes
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

FPS = Settings().video.fps
"""The rate the fake recorder writes at, which is the rate a project records at by default."""

BRIGHT_LUMA = 200.0
"""A luma bright enough that no frame of a test recording reads as black."""

RUNS_PROGRAMS = {"record": {"programs": programs.RUN}}
"""A machine that runs the programs a project names, which a project with one needs, since the default refuses them."""

LAUNCH_WAIT_SECONDS = 10.0
"""How long a launch waits for the other worker's, which only a pool that never starts one runs out."""


def a_project(tmp_path: Path, *, takes: bool = True, extra: str = "", machine: dict[str, Any] | None = None) -> Inputs:
    inputs = load_project(tmp_path, TOML + extra, page=TWO_SCENE_PAGE)
    if takes:
        write_takes(inputs, *(a_take(section, seconds=SPAN_SECONDS, voiced=False) for section in (1, 2)))
    return Inputs.load(tmp_path, environ={}, machine=machine)


class Driven:
    """A recorder that writes what a real one writes, in the order a real one writes it."""

    def __init__(self, report: PageReport | None = None) -> None:
        self.report = report or a_report()
        self.urls: list[str] = []
        self.order: list[str] = []
        self.launched = 0
        self.policies: list[str] = []
        self.missing: tuple[str, ...] = ()
        # Each file runs the requested span rounded to whole frames, which is what the stepped recorder writes.
        self.lengths: dict[Path, float] = {}
        # When set, no launch returns until every party has launched, so no worker can finish first.
        self.launches_together: threading.Barrier | None = None
        # The frames of each piece a section was joined from, in the order they were handed to the join.
        self.joined: list[list[range]] = []
        self.real_join = stepping.join
        self.real_clear = LogSink.clear
        self.real_write = LogSink.write
        # Launches released together count at once, and an unguarded increment could lose one.
        self.counting = threading.Lock()

    @contextmanager
    def chromium(self, _executable: str = "", *, policy: str = "trusted", **_launch: object) -> Generator[object]:
        if self.launches_together is not None:
            self.launches_together.wait()
        with self.counting:
            self.launched += 1
        self.policies.append(policy)
        yield object()

    def record_piece(self, _browser: object, url: str, frames: range, out: Path, **_kwargs: object) -> stepping.Piece:
        self.urls.append(url)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return stepping.Piece(
            path=out,
            frames=frames,
            url=url,
            fps=FPS,
            assets=("deck/index.html",),
            external=(),
            missing=self.missing,
            load_seconds=0.2,
            page_errors=(),
            console=(),
            report=self.report,
            undeclared=(),
            captured=len(frames),
            steps=len(frames),
        )

    def join(self, pieces: list[stepping.Piece], out: Path, *, seconds: float, name: str) -> Recording:
        self.joined.append([piece.frames for piece in pieces])
        # The joined file runs every frame its pieces were asked for, which is what the stepped recorder writes.
        self.lengths[out.with_name(name + out.suffix)] = sum(len(piece.frames) for piece in pieces) / FPS
        recording = self.real_join(pieces, out, seconds=seconds, name=name)
        self.order.append(f"drafted {name}{out.suffix}")
        return recording

    def clear_log(self, sink: LogSink) -> None:
        self.real_clear(sink)
        out = sink.job.out
        waiting = out.with_name(f".{out.stem}.part{out.suffix}").exists()
        self.order.append(f"cleared {out.name}" if waiting else f"cleared {out.name} with no draft waiting")

    def write_log(self, sink: LogSink, recording: Recording) -> None:
        self.real_write(sink, recording)
        self.order.append(f"logged {sink.job.out.name}")


@pytest.fixture
def driven(monkeypatch: pytest.MonkeyPatch) -> Driven:
    """The media layer replaced at the two seams `record` reaches it through, and no ffmpeg behind it."""
    fake = Driven()
    # One recording at a time, so the order a test reads is the order a single recorder writes in.
    monkeypatch.setattr(pool, "available_cpus", lambda: float(pool.CPUS_PER_RECORDING))
    monkeypatch.setattr(browser, "chromium", fake.chromium)
    monkeypatch.setattr(stepping, "record_piece", fake.record_piece)
    monkeypatch.setattr(stepping, "join", fake.join)
    monkeypatch.setattr(LogSink, "clear", lambda sink: fake.clear_log(sink))
    monkeypatch.setattr(LogSink, "write", lambda sink, recording: fake.write_log(sink, recording))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: fake.lengths[path])
    # The one ffmpeg run the recorder makes is the join of a section's pieces, which writes its last argument.
    monkeypatch.setattr(ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"mp4"))
    monkeypatch.setattr(frames, "luma_at", lambda _path, _at, **_kwargs: (90.0, BRIGHT_LUMA))
    return fake


def test_every_page_section_is_recorded_and_reported_in_section_order(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    result = record(inputs, a_run(inputs.root))
    assert [row.section for row in result.sections] == [1, 2]
    assert [row.kept for row in result.sections] == [False, False]
    assert [row.file for row in result.sections] == [Path("build/recordings/01.mp4"), Path("build/recordings/02.mp4")]
    assert driven.launched == 1


def test_a_host_that_marks_the_page_untrusted_records_it_under_that_policy(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path, machine={"record": {"page_policy": "untrusted"}})
    record(inputs, a_run(inputs.root))
    assert driven.policies == ["untrusted"]


@pytest.mark.usefixtures("driven")
def test_a_row_carries_the_length_and_the_frame_count_of_its_recording(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    row = record(inputs, a_run(inputs.root)).sections[0]
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None and log.checks is not None
    assert row.frames == stepping.frame_count(log.checks.wanted_seconds, FPS)
    assert row.seconds == pytest.approx(row.frames / FPS)


def test_the_log_is_cleared_as_the_draft_is_placed_and_written_after_it(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    assert driven.order[:3] == ["drafted 01.mp4", "cleared 01.mp4", "logged 01.mp4"]


@pytest.mark.usefixtures("driven")
def test_the_finished_log_carries_the_frames_and_the_judgements(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    assert log.section == 1
    assert log.checks is not None
    assert log.checks.wanted_seconds == pytest.approx(SPAN_SECONDS)


def test_a_run_stopped_before_it_measured_leaves_a_log_the_next_run_records_again(
    tmp_path: Path, driven: Driven
) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None
    log.model_copy(update={"checks": None}).write(inputs.workspace.recording_log("01"))
    driven.order.clear()
    record(inputs, a_run(inputs.root))
    assert "cleared 01.mp4" in driven.order


def recorded_pair(inputs: Inputs, key: str) -> tuple[bytes | None, RecordingLog | None]:
    """The video and the log of one section as they are on disk, either of them None when it is not there."""
    video = inputs.workspace.recording(key)
    return (video.read_bytes() if video.is_file() else None), RecordingLog.read(inputs.workspace.recording_log(key))


@pytest.mark.usefixtures("driven")
def test_a_record_again_that_fails_keeps_the_recording_it_was_replacing_with_its_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed record again left the old video with no log, so assemble cut a recording verify could not read."""
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    before = recorded_pair(inputs, "01")

    def failing(*_args: object, **_kwargs: object) -> stepping.Piece:
        raise ToolError("the page stopped answering")

    monkeypatch.setattr(stepping, "record_piece", failing)
    with pytest.raises(ToolError):
        record(inputs, a_run(inputs.root), only=[1])
    video, log = recorded_pair(inputs, "01")
    assert video == before[0]
    assert log is not None and log.checks is not None, "the video on disk still has the log that judged it"
    assert log == before[1]
    assert list(inputs.workspace.recording("01").parent.glob(".*")) == []


def test_a_rendered_section_whose_command_fails_keeps_the_video_it_drew_before(tmp_path: Path, driven: Driven) -> None:
    """The command's video was deleted before it ran, so a failed draw lost the picture the last one made."""
    load_project(tmp_path, RENDERED_TOML)
    inputs = Inputs.load(tmp_path, environ={}, machine=RUNS_PROGRAMS)
    write_takes(inputs, a_take(1, seconds=SPAN_SECONDS, voiced=False))
    [job] = plan(Inputs.load(tmp_path, environ={}), a_run(inputs.root), None)
    driven.lengths[job.out] = stepping.frame_count(job.seconds, FPS) / FPS
    record(inputs, a_run(inputs.root))
    before = recorded_pair(inputs, "01")
    (tmp_path / PROJECT_FILE).write_text(
        RENDERED_TOML.replace("pathlib.Path(sys.argv[1]).write_bytes(b'video')", "sys.exit(4)"), encoding="utf-8"
    )
    failing = Inputs.load(tmp_path, environ={}, machine=RUNS_PROGRAMS)
    with pytest.raises(ToolError, match="failed with exit 4"):
        record(failing, a_run(inputs.root), only=[1])
    assert recorded_pair(inputs, "01") == before
    assert list(inputs.workspace.recording("01").parent.glob(".*")) == []


def test_a_section_nothing_moved_under_is_kept_and_no_browser_opens(tmp_path: Path, driven: Driven) -> None:
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    launched = driven.launched
    again = record(inputs, a_run(inputs.root))
    assert [row.kept for row in again.sections] == [True, True]
    assert driven.launched == launched


@pytest.mark.usefixtures("driven")
def test_a_section_a_run_passed_over_out_of_date_is_named_in_a_sentence(tmp_path: Path) -> None:
    """The reason opened in lower case, so record's own line began `section 2: ...` where status's began `Section`."""
    inputs = a_project(tmp_path)
    record(inputs, a_run(inputs.root))
    (tmp_path / "deck" / "index.html").write_text(TWO_SCENE_PAGE.replace("two</p>", "two more</p>"), encoding="utf-8")
    lines: list[Event] = []
    record(Inputs.load(tmp_path, environ={}), a_run(inputs.root, lines=lines), only=[1])
    said = [line.message for line in lines if isinstance(line, RunLog) and line.level is Level.WARNING]
    assert said == [
        "Section 2: the markup of its scene changed since it was recorded, and this run did not name it, so the "
        "film keeps the picture recorded before."
    ]


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
    assert set(result.written) == {Path("build/recordings/01.mp4"), Path("build/recordings/01.json")}


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
    assert stale_recording(inputs, section) == "Section 1 has no recording"
    record(inputs, a_run(inputs.root))
    assert stale_recording(inputs, section) is None
    (tmp_path / "deck" / "index.html").write_text(TWO_SCENE_PAGE.replace("one</p>", "one more</p>"), encoding="utf-8")
    assert "changed since it was recorded" in (stale_recording(Inputs.load(tmp_path, environ={}), section) or "")


def test_sections_recorded_at_once_come_back_in_order_with_their_own_pair_of_lines(
    tmp_path: Path, driven: Driven, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record waited for every section in turn, so a film took as long to record as it runs."""
    monkeypatch.setattr(pool, "available_cpus", lambda: float(pool.CPUS_PER_RECORDING * pool.MOST_AT_ONCE))
    # One worker could otherwise take every chunk before the others take their first.
    driven.launches_together = threading.Barrier(pool.MOST_AT_ONCE, timeout=LAUNCH_WAIT_SECONDS)
    inputs = a_project(tmp_path)
    lines: list[Event] = []
    result = record(inputs, a_run(inputs.root, lines=lines))
    assert [row.section for row in result.sections] == [1, 2]
    assert driven.launched == pool.MOST_AT_ONCE, "each worker drives a Chromium of its own"
    for number in (1, 2):
        paired = [type(line).__name__ for line in lines if getattr(line, "section", None) == number]
        assert paired[0] == "SectionStart" and "SectionDone" in paired, paired
    assert [(line.done, line.section) for line in lines if isinstance(line, StageProgress)] == [(1, 1), (2, 2)]


CHUNKED = {"record": {"concurrency": 4, "chunk_frames_min": 10, "chunk_startup_seconds": 0.1, "step_seconds": 0.1}}
"""A machine of four recorders whose page loads as fast as it draws a frame, so every section splits four ways."""


def test_a_section_split_across_recorders_is_recorded_in_chunks_and_joined_in_frame_order(
    tmp_path: Path, driven: Driven
) -> None:
    """A motion-heavy section recorded by one Chromium left every other core idle while it stepped."""
    inputs = a_project(tmp_path, machine=CHUNKED)
    lines: list[Event] = []
    result = record(inputs, a_run(inputs.root, lines=lines))
    frames = stepping.frame_count(SPAN_SECONDS, FPS)
    assert [row.section for row in result.sections] == [1, 2]
    assert [sorted((one.start, one.stop) for one in pieces) for pieces in driven.joined] == [
        [(0, 25), (25, 50), (50, 75), (75, frames)]
    ] * 2
    for number in (1, 2):
        paired = [type(line).__name__ for line in lines if getattr(line, "section", None) == number]
        assert paired.count("SectionStart") == paired.count("SectionDone") == 1, paired
        done = [line for line in lines if isinstance(line, SectionDone) and line.section == number]
        assert done[0].outcome is Outcome.RAN
    log = RecordingLog.read(inputs.workspace.recording_log("01"))
    assert log is not None and log.checks is not None
    assert log.recording.pieces == (0, 25, 50, 75)
    assert log.checks.duration_seconds == pytest.approx(frames / FPS)
    assert list(inputs.workspace.recording("01").parent.glob(".*")) == []


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


def test_a_dialogue_section_is_recorded_for_its_whole_span(tmp_path: Path) -> None:
    """The page plays under every turn of its section, so it is recorded for the turns, their gaps and the tail."""
    inputs = load_project(tmp_path, TOML + "\n[voices.narrator]\n\n[voices.guest]\n", page=TWO_SCENE_PAGE)
    turns = (
        a_take(1, seconds=SPAN_SECONDS, voiced=False, tail_seconds=0.3),
        a_take(1, turn=2, voice="guest", seconds=SPAN_SECONDS, voiced=False, tail_seconds=0.7),
        a_take(2, seconds=SPAN_SECONDS, voiced=False),
    )
    write_takes(inputs, *turns)
    loaded = Inputs.load(tmp_path, environ={})
    jobs = plan(loaded, a_run(tmp_path), None)
    assert [job.seconds for job in jobs] == [round(2 * SPAN_SECONDS + 1.0, 3), SPAN_SECONDS]


def test_a_held_section_is_recorded_through_its_hold(tmp_path: Path) -> None:
    """The hold is picture the page keeps drawing, so a motion arriving after the last word lands in it."""
    held = TOML.replace('scene = "1"\n', 'scene = "1"\nhold_seconds = 2.0\n', 1)
    inputs = load_project(tmp_path, held, page=TWO_SCENE_PAGE)
    write_takes(inputs, *(a_take(section, seconds=SPAN_SECONDS, voiced=False) for section in (1, 2)))
    loaded = Inputs.load(tmp_path, environ={})
    jobs = plan(loaded, a_run(tmp_path), None)
    assert [job.seconds for job in jobs] == [SPAN_SECONDS + 2.0, SPAN_SECONDS]


def test_a_page_section_is_recorded_for_exactly_the_frames_the_film_cuts_from_it(tmp_path: Path) -> None:
    """Each section runs whole frames between rounded boundaries, so a recording that long needs no margin and no pad.

    Two spans of 1.02 s at 25 fps round to boundaries at frames 26 and 51, so the second section plays
    25 frames although its own span rounds to 26.
    """
    inputs = load_project(tmp_path, TOML, page=TWO_SCENE_PAGE)
    write_takes(inputs, *(a_take(section, seconds=1.02, voiced=False) for section in (1, 2)))
    loaded = Inputs.load(tmp_path, environ={})
    assert loaded.settings.video.fps == 25
    assert [job.seconds for job in plan(loaded, a_run(tmp_path), None)] == [1.04, 1.0]


# ---- one turn edited in a two-section deck that shares one page ---------------------------------

SHARED_TOML = """
[project]
name = "larkbill"

[voices.narrator]
provider = "elevenlabs"

[voices.guest]
provider = "elevenlabs"

[voices.host]
provider = "elevenlabs"

[elevenlabs]
dollars_per_1000_characters = 0.30

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""
"""Two sections that play two scenes of one page, as the Larkbill deck does."""

SHARED_SCRIPT = """## 1. Open

Open any invoice, and check the total.

## 2. Deliver

[voice narrator]

Click Share, and your client has it.

[voice guest]

Before you close the tab.
"""
"""Section two is a dialogue of two turns, so the edited turn has to be named and not just its section."""

SHARED_PAGE = """<!doctype html><html><head><style>.go { color: green; }</style></head><body>
<div data-scene="1"><template data-slide="1.1"><p data-in="invoice">INV-1042</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p class="go" data-in="click">Share</p></template></div>
</body></html>
"""
"""The page both sections play: one head every scene shares, and the Share button in scene two alone."""

SHARED_CUES = {"1": {"cues": [{"id": "1.1:invoice", "phrase": "any invoice"}]},
               "2": {"cues": [{"id": "2.1:click", "phrase": "Click Share"}]}}  # fmt: skip

SHARED_ENVIRON = {
    "ELEVENLABS_API_KEY": "key-under-test",
    "DECKTALK_VOICES_NARRATOR_ID": "narrator-under-test",
    "DECKTALK_VOICES_GUEST_ID": "guest-under-test",
    "DECKTALK_VOICES_HOST_ID": "host-under-test",
}
"""What the machine hands the project: a stand-in credential the fake voice never reads, and each voice's id."""


@pytest.fixture
def shared_deck(
    tmp_path: Path,
    driven: Driven,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    make_run: Callable[..., Watched],
) -> Callable[[], list[str]]:
    """Build the two-section deck with the fake voice, and hand back a function that builds it again."""
    request.getfixturevalue("fake_voice")
    request.getfixturevalue("fake_ffmpeg")
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)
    # The fake encoder's probe answers for the recorder too: a recording runs the frames it was asked
    # for, and anything else the fake encoder wrote runs the span every section is asked for.
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: driven.lengths.get(path, SPAN_SECONDS))
    load_project(tmp_path, SHARED_TOML, page=SHARED_PAGE, script=SHARED_SCRIPT, cues=SHARED_CUES,
                 environ=SHARED_ENVIRON)  # fmt: skip

    def build() -> list[str]:
        inputs = Inputs.load(tmp_path, environ=SHARED_ENVIRON)
        narrate(inputs, make_run(inputs, spend=True).run)
        cue(inputs, make_run(inputs).run)
        driven.urls.clear()
        record(inputs, make_run(inputs).run)
        return driven.urls

    build()
    return build


def edit(tmp_path: Path, name: str, old: str, new: str) -> None:
    path = tmp_path / name
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def stale_report(tmp_path: Path, make_run: Callable[..., Watched]) -> tuple[list[bool], list[str]]:
    """What `status` says of each section's recording: the Stale column, and the lines that say why."""
    inputs = Inputs.load(tmp_path, environ=SHARED_ENVIRON)
    run = make_run(inputs).run
    said = notes(run)
    rows = status(inputs, run).sections
    return [row.recording_stale for row in rows], [line for line in said if "recorded again" in line]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_a_fresh_build_leaves_no_section_stale(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    assert stale_report(tmp_path, make_run) == ([False, False], [])


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_one_word_changed_in_one_turn_marks_that_section_alone_and_names_the_turn(
    tmp_path: Path, make_run: Callable[..., Watched]
) -> None:
    """The follows film changed Share to Send in turn 2.1, and status called both sections stale for every reason."""
    edit(tmp_path, "script.md", "Click Share", "Click Send")
    stale, why = stale_report(tmp_path, make_run)
    assert stale == [False, True]
    assert why == ["Section 2: the words of turn 2.1 changed since it was recorded, so it would be recorded again."]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_one_word_changed_in_one_scene_marks_the_section_that_plays_it_and_says_so(
    tmp_path: Path, make_run: Callable[..., Watched]
) -> None:
    edit(tmp_path, "deck/index.html", ">Share<", ">Send<")
    stale, why = stale_report(tmp_path, make_run)
    assert stale == [False, True]
    assert why == ["Section 2: the markup of its scene changed since it was recorded, so it would be recorded again."]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_a_change_to_the_part_every_scene_shares_marks_every_section(
    tmp_path: Path, make_run: Callable[..., Watched]
) -> None:
    edit(tmp_path, "deck/index.html", "color: green", "color: teal")
    stale, why = stale_report(tmp_path, make_run)
    assert stale == [True, True]
    assert why == [
        f"Section {number}: the part of deck/index.html every scene shares changed since it was recorded, "
        "so it would be recorded again."
        for number in (1, 2)
    ]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_the_film_s_edit_names_the_turn_and_the_scene_it_touched(
    tmp_path: Path, make_run: Callable[..., Watched]
) -> None:
    """One word across the script, the slide and the cue: the cue file moves no time, so it is not named."""
    edit(tmp_path, "script.md", "Click Share", "Click Send")
    edit(tmp_path, "deck/index.html", ">Share<", ">Send<")
    edit(tmp_path, "cues.json", "Click Share", "Click Send")
    assert stale_report(tmp_path, make_run)[1] == [
        "Section 2: the words of turn 2.1 and the markup of its scene changed since it was recorded, "
        "so it would be recorded again."
    ]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_a_turn_handed_to_another_voice_is_named_by_its_voice(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    edit(tmp_path, "script.md", "[voice guest]", "[voice host]")
    assert stale_report(tmp_path, make_run)[1] == [
        "Section 2: the voice of turn 2.2 changed since it was recorded, so it would be recorded again."
    ]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
@pytest.mark.usefixtures("shared_deck")
def test_a_record_run_before_narrate_keeps_the_take_it_plays(
    tmp_path: Path, make_run: Callable[..., Watched], driven: Driven
) -> None:
    """`record` plays the takes on disk, so an edit narrate has not voiced yet records nothing new."""
    edit(tmp_path, "script.md", "Click Share", "Click Send")
    inputs = Inputs.load(tmp_path, environ=SHARED_ENVIRON)
    driven.urls.clear()
    again = record(inputs, make_run(inputs).run)
    assert [row.kept for row in again.sections] == [True, True]
    assert stale_report(tmp_path, make_run)[0] == [False, True]


@pytest.mark.parametrize("run_environ", [SHARED_ENVIRON])
def test_the_rebuild_after_a_one_word_edit_records_only_the_section_it_touched(
    tmp_path: Path, shared_deck: Callable[[], list[str]], make_run: Callable[..., Watched]
) -> None:
    """The edit the follows film makes: one word in the script, the slide and the cue, all in section two."""
    edit(tmp_path, "script.md", "Click Share", "Click Send")
    edit(tmp_path, "deck/index.html", ">Share<", ">Send<")
    edit(tmp_path, "cues.json", "Click Share", "Click Send")
    urls = shared_deck()
    assert [Q.SCENE.value + "=2" in url for url in urls] == [True]
    assert stale_report(tmp_path, make_run) == ([False, False], [])


RENDERED_TOML = """
[project]
name = "demo"

[[section]]
number = 1
render_command = ["{python}", "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'video')", "{out}"]
"""
"""One section whose picture a command draws, which writes its video and nothing else."""


def test_a_rendered_section_nothing_moved_under_is_kept(tmp_path: Path, driven: Driven) -> None:
    """The log of a drawn section is keyed as the plan keys it, or every run would draw it again."""
    load_project(tmp_path, RENDERED_TOML)
    inputs = Inputs.load(tmp_path, environ={}, machine=RUNS_PROGRAMS)
    write_takes(inputs, a_take(1, seconds=SPAN_SECONDS, voiced=False))
    # The command draws every frame of its span, as a renderer handed the frame count does.
    [job] = plan(Inputs.load(tmp_path, environ={}), a_run(inputs.root), None)
    driven.lengths[job.out] = stepping.frame_count(job.seconds, FPS) / FPS
    record(inputs, a_run(inputs.root))
    again = record(inputs, a_run(inputs.root))
    assert [row.kept for row in again.sections] == [True]
    assert stale_recording(inputs, inputs.document.recorded_sections[0]) is None
    assert driven.launched == 0, "a section a command draws needs no browser"


MARKER = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('started')"
"""A program that leaves a marker where it is told to, so a test can tell that it never started."""


@pytest.mark.usefixtures("driven")
@pytest.mark.parametrize("machine", [None, {"record": {"programs": programs.ASK}}])
def test_a_fresh_project_starts_none_of_its_programs_and_names_the_key_that_allows_them(
    tmp_path: Path, machine: dict[str, Any] | None
) -> None:
    """The security seat's p1 ran a project's program before anything else was judged: it now runs nothing."""
    marker = tmp_path / "marker"
    drawn = f'\n[[section]]\nnumber = 3\nrender_command = ["{{python}}", "-c", "{MARKER}", "{marker}"]\n'
    inputs = a_project(tmp_path, extra=drawn, machine=machine)
    with pytest.raises(InputError, match="record.programs") as refused:
        record(inputs, a_run(inputs.root))
    assert "section 3's render_command" in str(refused.value)
    assert not marker.exists()
