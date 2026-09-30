"""The bridge from standard logging to the run's stream, which never prints and never recurses."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextvars import copy_context
from pathlib import Path

import pytest

from decktalk import logs
from decktalk.events import Event, Level, Log
from decktalk.logs import HANDLER, LOGGER, RunHandler, install, level_of, logging_into, where, within
from decktalk.pipeline import Stage
from support.runs import a_machine

log = logging.getLogger("decktalk.media.ffmpeg")


def lines_of(seen: list[Event]) -> list[Log]:
    return [line for line in seen if isinstance(line, Log)]


def test_a_record_written_inside_a_run_becomes_a_line_of_that_run(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with (
        here.events.subscribe(seen.append),
        here.run() as run,
        run.stage(Stage.ASSEMBLE),
        run.section(Stage.ASSEMBLE, 3),
    ):
        log.debug("ffmpeg exited %d.", 0, extra={"data": {"exit": 0, "seconds": 0.5}})
    [line] = lines_of(seen)
    assert (line.run, line.level, line.message, line.source) == (
        run.id,
        Level.DEBUG,
        "ffmpeg exited 0.",
        "media.ffmpeg",
    )
    assert (line.stage, line.section, line.data) == (Stage.ASSEMBLE, 3, {"exit": 0, "seconds": 0.5})


def test_a_stages_own_sentence_says_which_section_it_was_said_in(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run() as run, run.section(Stage.RECORD, 2):
        run.note("kept.")
    [line] = lines_of(seen)
    assert (line.source, line.stage, line.section) == (None, Stage.RECORD, 2)


def test_an_exception_on_a_record_is_named_and_its_traceback_is_not_kept(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run():
        try:
            raise OSError("the disk is full\nand this second line is detail")
        except OSError:
            log.warning("could not write", exc_info=True)
    [line] = lines_of(seen)
    assert line.data == {"error": "OSError: the disk is full"}
    assert "Traceback" not in line.model_dump_json()


@pytest.mark.parametrize(
    ("number", "level"),
    [
        (logging.DEBUG - 5, Level.DEBUG),
        (logging.DEBUG, Level.DEBUG),
        (logging.INFO, Level.INFO),
        (logging.WARNING, Level.WARNING),
        (logging.ERROR, Level.ERROR),
        (logging.CRITICAL, Level.ERROR),
    ],
)
def test_every_standard_level_lands_on_one_of_the_streams_four(number: int, level: Level) -> None:
    assert level_of(number) is level


@pytest.fixture
def last_resort(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[logging.LogRecord]]:
    """Every record Python's last-resort handler would have printed to bare stderr."""
    printed: list[logging.LogRecord] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            printed.append(record)

    monkeypatch.setattr(logging, "lastResort", Spy())
    yield printed


def test_nothing_prints_when_no_run_is_bound(capsys: pytest.CaptureFixture[str], last_resort: list) -> None:
    for name in ("decktalk", "decktalk.media.ffmpeg", "decktalk.toolchain.chromium_fetch", "decktalk.media.origin"):
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
            logging.getLogger(name).log(level, "a sentence nobody bound a run for")
    assert capsys.readouterr() == ("", "")
    assert last_resort == []


def test_a_record_that_cannot_be_rendered_is_counted_and_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", True)
    # pytest's own capture handler on the root logger raises on a bad record, and a host's handler is
    # the host's business, so the record is kept to the package's handler alone.
    monkeypatch.setattr(LOGGER, "propagate", False)
    here = a_machine(tmp_path)
    before = HANDLER.failures
    with here.run():
        log.warning("%d cues", "not a number")
    assert HANDLER.failures == before + 1
    assert capsys.readouterr().err == ""


def test_a_renderer_that_logs_does_not_recurse_into_the_stream(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []

    def chatty(event: Event) -> None:
        seen.append(event)
        log.info("rendered a %s line", event.event)

    with here.events.subscribe(chatty), here.run() as run:
        run.note("one")
    assert [line.event for line in seen] == ["run.start", "log", "run.done"]


def test_two_runs_on_two_threads_each_keep_their_own_lines(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    both_bound = threading.Barrier(2)

    def one(section: int) -> None:
        with here.run() as run, run.section(Stage.RECORD, section):
            both_bound.wait(timeout=5)
            # A worker a stage starts runs under a copy of its parent's context, as the pools do.
            copy_context().run(log.info, "recorded %d", section)

    with here.events.subscribe(seen.append):
        threads = [threading.Thread(target=one, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    runs = {line.run: line for line in seen if line.event == "run.start"}
    said = lines_of(seen)
    assert len(said) == 2 and len(runs) == 2
    for line in said:
        assert line.message == f"recorded {line.section}"
    assert len({line.run for line in said}) == 2


def test_a_hosts_own_handler_hears_the_record_stamped_with_the_run(tmp_path: Path) -> None:
    heard: list[logging.LogRecord] = []

    class Host(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            heard.append(record)

    host = Host()
    logging.getLogger().addHandler(host)
    try:
        here = a_machine(tmp_path)
        with here.run() as run, run.section(Stage.RECORD, 4):
            log.info("stamped")
    finally:
        logging.getLogger().removeHandler(host)
    [record] = [record for record in heard if record.getMessage() == "stamped"]
    assert (record.decktalk_run, record.decktalk_stage, record.decktalk_section) == (run.id, "record", 4)


def test_a_level_the_host_chose_is_kept_and_the_handler_is_installed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LOGGER, "handlers", [])
    monkeypatch.setattr(LOGGER, "level", logging.WARNING)
    first = install()
    assert install() is first
    assert [type(handler) for handler in LOGGER.handlers] == [RunHandler]
    assert LOGGER.level == logging.WARNING


def test_the_package_logger_hears_debug_when_nobody_chose_a_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LOGGER, "handlers", [])
    monkeypatch.setattr(LOGGER, "level", logging.NOTSET)
    install()
    assert LOGGER.level == logging.DEBUG


def test_the_place_is_restored_when_a_block_closes() -> None:
    def receiver(record: logging.LogRecord) -> None:
        del record

    with logging_into(receiver, run="r1"):
        with within(stage=Stage.CUE, section=1):
            assert (where().run, where().stage, where().section) == ("r1", Stage.CUE, 1)
        assert (where().stage, where().section) == (None, None)
    assert where() == logs.Where()
