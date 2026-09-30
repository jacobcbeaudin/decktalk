"""Eleven names, four minted fields, and one stream a renderer cannot stop by raising."""

from __future__ import annotations

import json
import os
import threading
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from decktalk.errors import ErrorCode, ErrorInfo
from decktalk.events import (
    CUT,
    EVENTS,
    LINE_CHARS,
    Event,
    Events,
    FindingEvent,
    JsonlSink,
    Level,
    Line,
    Log,
    Progress,
    RunDone,
    RunStart,
    StageDone,
    StageStart,
    TakeCharged,
    Unit,
)
from decktalk.findings import Code, Finding, Location
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Layer, Spend, SpendState
from decktalk.secret import Secret

NAMES = (
    "run.start",
    "run.done",
    "stage.start",
    "stage.done",
    "section.start",
    "section.done",
    "progress",
    "finding",
    "spend",
    "take.charged",
    "fetch",
    "log",
)
MINTED = ("event", "time", "seq", "run")

FINDING = Finding(code=Code.CUE_OFF, message="It lands 340 ms late.", location=Location(where="2.1:formula"))
SPEND = Spend(
    state=SpendState.ESTIMATE,
    sections=(1,),
    characters=392,
    dollars=0.12,
    ceiling_dollars=0.15,
    price_per_1000_characters=0.3,
    price_layer=Layer.DEFAULT,
)
PAYLOADS: dict[str, dict[str, object]] = {
    "run.start": {"events_path": "build/events/r1.jsonl"},
    "run.done": {"outcome": Outcome.OK, "seconds": 64.0},
    "stage.start": {"stage": Stage.RECORD, "index": 3, "count": 6},
    "stage.done": {"stage": Stage.RECORD, "outcome": Outcome.SKIPPED, "seconds": 0.0},
    "section.start": {"stage": Stage.RECORD, "section": 2},
    "section.done": {"stage": Stage.RECORD, "section": 2, "outcome": Outcome.FAILED, "seconds": 1.0},
    "progress": {"stage": Stage.NARRATE, "done": 1, "total": 3, "unit": Unit.TAKE, "label": "section 1"},
    "finding": {"finding": FINDING},
    "spend": {"spend": SPEND},
    "take.charged": {"section": 2, "take": "0f3a9c1e", "characters": 118, "dollars": 0.04},
    "fetch": {"tool": "ffmpeg", "bytes": 1024, "total_bytes": 4096},
    "log": {"level": Level.INFO, "message": "One sentence."},
}


def test_the_twelve_names_are_the_ones_the_design_named() -> None:
    assert list(EVENTS) == list(NAMES)


def test_a_log_line_carries_where_it_was_written_and_what_it_measured() -> None:
    line = Log(
        time=datetime.now(UTC),
        seq=0,
        run="r1",
        level=Level.DEBUG,
        message="ffmpeg exited 0.",
        source="media.ffmpeg",
        stage=Stage.ASSEMBLE,
        section=2,
        data={"exit": 0, "seconds": 0.25, "argv": "ffmpeg -y out.mp4", "killed": False, "limit": None},
    )
    parsed = TypeAdapter(Line).validate_json(line.model_dump_json())
    assert parsed == line


def test_an_object_in_a_log_lines_data_is_kept_as_text() -> None:
    """A header map passed by mistake becomes text the redaction sees, never an object a writer walks."""
    line = Log(time=datetime.now(UTC), seq=0, run="r1", level=Level.DEBUG, message="m", data={"headers": {"a": "b"}})
    assert line.data == {"headers": "{'a': 'b'}"}


def test_a_log_line_from_an_earlier_release_still_parses() -> None:
    old = '{"event":"log","time":"2026-01-01T00:00:00Z","seq":0,"run":"r1","level":"info","message":"m"}'
    parsed = TypeAdapter(Line).validate_json(old)
    assert isinstance(parsed, Log) and (parsed.source, parsed.stage, parsed.section, parsed.data) == (None,) * 4


def test_skip_and_fail_are_outcomes_rather_than_names() -> None:
    assert not [name for name in EVENTS if name.endswith((".skip", ".fail"))]
    assert "outcome" in EVENTS["stage.done"].model_fields
    assert "outcome" in EVENTS["section.done"].model_fields


def test_the_base_carries_the_four_fields_the_library_mints() -> None:
    assert tuple(Event.model_fields) == MINTED


def test_every_name_is_its_own_class_and_its_own_discriminator() -> None:
    for name, kind in EVENTS.items():
        assert kind.model_fields["event"].default == name
        assert issubclass(kind, Event)


def test_the_union_is_closed_over_the_eleven_names() -> None:
    members = typing.get_args(typing.get_args(Line)[0])
    assert set(members) == set(EVENTS.values())


@pytest.mark.parametrize("name", NAMES)
def test_an_event_round_trips_through_the_union(name: str) -> None:
    built = EVENTS[name](time=datetime(2026, 9, 24, 3, 0, tzinfo=UTC), seq=0, run="r1", **PAYLOADS[name])
    assert TypeAdapter(Line).validate_json(built.model_dump_json()) == built


def test_the_stream_mints_the_run_the_time_and_the_sequence() -> None:
    stream = Events()
    seen: list[Event] = []
    with stream.subscribe(seen.append):
        stream.emit("r1", StageStart, stage=Stage.NARRATE, index=1, count=6)
        stream.emit("r1", StageStart, stage=Stage.CUE, index=2, count=6)
    assert [event.seq for event in seen] == [0, 1]
    assert {event.run for event in seen} == {"r1"}
    assert all(event.time.tzinfo is not None for event in seen)


def test_the_sequence_counts_per_run_so_no_file_has_a_gap() -> None:
    stream = Events()
    seen: list[Event] = []
    with stream.subscribe(seen.append):
        stream.emit("r1", Log, level=Level.INFO, message="One.")
        stream.emit("r2", Log, level=Level.INFO, message="Two.")
        stream.emit("r1", Log, level=Level.INFO, message="Three.")
    assert [(event.run, event.seq) for event in seen] == [("r1", 0), ("r2", 0), ("r1", 1)]


def test_a_subscription_to_named_runs_yields_only_those_runs() -> None:
    machine = Events()
    seen: list[Event] = []
    with machine.subscribe(seen.append, runs=("r1",)):
        machine.emit("r1", Log, level=Level.INFO, message="Mine.")
        machine.emit("r2", Log, level=Level.INFO, message="Somebody else's.")
    assert [event.message for event in seen] == ["Mine."]  # type: ignore[attr-defined]


def test_a_closed_subscription_receives_nothing_more() -> None:
    stream = Events()
    seen: list[Event] = []
    subscription = stream.subscribe(seen.append)
    stream.emit("r1", Log, level=Level.INFO, message="One.")
    subscription.close()
    stream.emit("r1", Log, level=Level.INFO, message="Two.")
    assert len(seen) == 1


def test_a_subscriber_that_raises_becomes_a_log_line_and_never_stops_the_run() -> None:
    stream = Events()
    seen: list[Event] = []

    def angry(event: Event) -> None:
        if event.event != "log":
            raise RuntimeError("no")

    stream.subscribe(angry)
    stream.subscribe(seen.append)
    stream.emit("r1", StageStart, stage=Stage.NARRATE, index=1, count=6)
    assert [event.event for event in seen] == ["stage.start", "log"]
    assert isinstance(seen[1], Log)
    assert seen[1].level is Level.ERROR


RUNS_IN_A_LONG_LIFE = 1000
"""How many runs a long-lived service opens on one machine in this test, which is enough to see a leak."""


def test_two_threads_delivering_at_once_each_report_their_own_subscriber_failure() -> None:
    stream = Events()
    seen: list[Event] = []
    both_inside = threading.Barrier(2)

    def angry(event: Event) -> None:
        if event.event == "log":
            return
        # Each thread waits inside its own delivery until the other is inside too, which is the
        # moment one flag for the whole stream would make the second thread drop its failure line.
        both_inside.wait(timeout=5)
        raise RuntimeError("no")

    stream.subscribe(angry)
    stream.subscribe(seen.append)
    threads = [
        threading.Thread(
            target=stream.emit, args=(run, StageStart), kwargs={"stage": Stage.RECORD, "index": 1, "count": 1}
        )
        for run in ("r1", "r2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(event.run for event in seen if isinstance(event, Log)) == ["r1", "r2"]


def test_a_finished_run_leaves_no_counter_behind() -> None:
    stream = Events()
    for number in range(RUNS_IN_A_LONG_LIFE):
        run = f"r{number}"
        stream.emit(run, RunStart)
        stream.emit(run, RunDone, outcome=Outcome.OK, seconds=0.0)
    assert stream._counters == {}


def test_the_sink_appends_one_line_per_event_and_never_truncates(tmp_path) -> None:
    path = tmp_path / "build" / "events" / "r1.jsonl"
    stream = Events()
    with stream.subscribe(JsonlSink(path)):
        stream.emit("r1", Log, level=Level.INFO, message="One.")
        stream.emit("r1", Log, level=Level.INFO, message="Two.")
    lines = path.read_text("utf-8").splitlines()
    assert [json.loads(line)["message"] for line in lines] == ["One.", "Two."]
    assert [json.loads(line)["seq"] for line in lines] == [0, 1]


def test_pruning_keeps_the_newest_runs_and_leaves_the_directory_alone_when_it_is_absent(tmp_path) -> None:
    directory = tmp_path / "events"
    assert JsonlSink.prune(directory, 2) == ()
    directory.mkdir()
    for index, name in enumerate(("a", "b", "c")):
        path = directory / f"{name}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (index, index))
    gone = JsonlSink.prune(directory, 2)
    assert [path.name for path in gone] == ["a.jsonl"]
    assert sorted(path.name for path in directory.iterdir()) == ["b.jsonl", "c.jsonl"]


def test_a_progress_line_carries_the_count_so_no_renderer_works_out_a_fraction() -> None:
    line = Progress(
        time=datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
        seq=0,
        run="r1",
        stage=Stage.RECORD,
        section=2,
        done=1,
        total=3,
        unit=Unit.SECTION,
        label="section 2",
    )
    assert (line.done, line.total, line.unit) == (1, 3, Unit.SECTION)


def test_the_sink_path_travels_on_the_line_that_opens_the_run() -> None:
    stream = Events()
    opened = stream.emit("r1", RunStart, events_path="build/events/r1.jsonl")
    assert opened.model_dump(mode="json")["events_path"] == "build/events/r1.jsonl"


def test_an_event_refuses_a_field_it_does_not_declare() -> None:
    with pytest.raises(ValidationError):
        Log(time=datetime(2026, 9, 24, 3, 0, tzinfo=UTC), seq=0, run="r1", level=Level.INFO, message="x", extra=1)


# ---- no line can be built holding a registered secret ---------------------------------------------

CANARY = "sk_event_canary_0b7d3e91aa"
Secret(CANARY, "ELEVENLABS_API_KEY")

AROUND = st.text(alphabet=st.characters(codec="ascii", exclude_characters="\r\n"), max_size=12)


def carrying(text: str) -> dict[str, dict[str, object]]:
    """Every event whose payload can hold free text, with `text` in every string field of it."""
    finding = FINDING.model_copy(update={"message": text})
    error = ErrorInfo(code=ErrorCode.PROVIDER, message=text, hint=text, docs=ErrorCode.PROVIDER.url)
    return {
        "run.start": {"events_path": f"build/events/{text}.jsonl"},
        "run.done": {"outcome": Outcome.FAILED, "seconds": 1.0, "error": error},
        "progress": {"stage": Stage.NARRATE, "done": 1, "total": 3, "unit": Unit.TAKE, "label": text},
        "finding": {"finding": finding},
        "fetch": {"tool": text, "bytes": 0},
        "log": {"level": Level.ERROR, "message": text, "source": text, "data": {"said": text, "headers": {"k": text}}},
    }


@given(before=AROUND, after=AROUND, twice=st.booleans())
def test_no_line_can_be_built_holding_a_registered_secret(before: str, after: str, twice: bool) -> None:
    text = before + CANARY + after + (CANARY if twice else "")
    for name, payload in carrying(text).items():
        line = EVENTS[name](time=datetime.now(UTC), seq=0, run="r1", **payload)
        assert CANARY not in line.model_dump_json(), name
        assert "<secret ELEVENLABS_API_KEY>" in line.model_dump_json(), name


def test_a_line_built_by_the_stream_holds_no_registered_secret() -> None:
    stream = Events()
    seen: list[Event] = []
    stream.subscribe(seen.append)
    stream.emit("r1", Log, level=Level.INFO, message=f"key={CANARY}")
    assert seen[0].model_dump_json().count("<secret ELEVENLABS_API_KEY>") == 1


# ---- a bounded file ---------------------------------------------------------------------------------

BOUND = 65_536
"""The smallest bound the setting allows, which a few thousand debug lines pass quickly."""


def test_a_bounded_file_keeps_every_lifecycle_and_money_line_and_counts_what_it_left_out(tmp_path) -> None:
    path = tmp_path / "r1.jsonl"
    sink = JsonlSink(path, max_bytes=BOUND)
    stream = Events()
    kept_kinds = {"stage.start", "finding", "take.charged", "stage.done"}
    sent_kept = 0
    with stream.subscribe(sink):
        stream.emit("r1", RunStart, events_path="build/events/r1.jsonl")
        for number in range(10_000):
            stream.emit("r1", Log, level=Level.DEBUG, message=f"ffmpeg call {number} exited 0.")
            if number % 500 == 0:
                stream.emit("r1", StageStart, stage=Stage.ASSEMBLE, index=1, count=1)
                stream.emit("r1", FindingEvent, finding=FINDING)
                stream.emit("r1", TakeCharged, section=1, take="0f3a9c1e", characters=10, dollars=0.01)
                stream.emit("r1", StageDone, stage=Stage.ASSEMBLE, outcome=Outcome.OK, seconds=1.0)
                sent_kept += 4
    lines = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert sum(1 for line in lines if line["event"] in kept_kinds) == sent_kept
    logged = sum(1 for line in lines if line["event"] == "log")
    assert logged + sink.dropped == 10_000 and sink.dropped > 0
    kept_bytes = sum(len(json.dumps(line)) for line in lines if line["event"] != "log")
    assert path.stat().st_size <= BOUND + kept_bytes + len(lines)


def test_a_bounded_file_keeps_warnings_after_it_has_left_the_quiet_lines_out(tmp_path) -> None:
    path = tmp_path / "r1.jsonl"
    sink = JsonlSink(path, max_bytes=BOUND)
    stream = Events()
    with stream.subscribe(sink):
        while sink.written < BOUND:
            stream.emit("r1", Log, level=Level.INFO, message="x" * 200)
        stream.emit("r1", Progress, stage=Stage.NARRATE, done=1, total=3, unit=Unit.TAKE, label="left out")
        stream.emit("r1", Log, level=Level.DEBUG, message="left out")
        stream.emit("r1", Log, level=Level.WARNING, message="kept")
    said = path.read_text("utf-8")
    assert '"kept"' in said and "left out" not in said
    assert sink.dropped == 2


def test_an_unbounded_file_leaves_nothing_out(tmp_path) -> None:
    sink = JsonlSink(tmp_path / "r1.jsonl")
    stream = Events()
    with stream.subscribe(sink):
        for _ in range(100):
            stream.emit("r1", Log, level=Level.DEBUG, message="x" * 1000)
    assert sink.dropped == 0


def test_a_long_message_and_a_long_value_are_cut_and_say_so() -> None:
    line = Log(time=datetime.now(UTC), seq=0, run="r1", level=Level.INFO, message="m" * 5000, data={"tail": "t" * 5000})
    assert line.message == "m" * LINE_CHARS + CUT
    assert line.data == {"tail": "t" * LINE_CHARS + CUT}


@given(padding=st.integers(min_value=LINE_CHARS - len(CANARY), max_value=LINE_CHARS))
def test_the_cut_runs_after_redaction_so_no_prefix_of_a_secret_survives(padding: int) -> None:
    line = Log(time=datetime.now(UTC), seq=0, run="r1", level=Level.INFO, message="x" * padding + CANARY)
    for length in range(8, len(CANARY) + 1):
        assert CANARY[:length] not in line.message


def test_pruning_survives_a_file_another_run_removed_first(tmp_path, monkeypatch) -> None:
    """Two runs of one project prune one directory, and a file the other removed is one fewer to prune."""
    directory = tmp_path / "events"
    directory.mkdir()
    for name in ("a", "b", "c"):
        (directory / f"{name}.jsonl").write_text("{}\n", encoding="utf-8")
    real = Path.glob

    def racing(self: Path, pattern: str):  # noqa: ANN202  (Path.glob's own signature)
        yield from real(self, pattern)
        yield self / "removed-by-another-run.jsonl"

    monkeypatch.setattr(Path, "glob", racing)
    assert len(JsonlSink.prune(directory, 1)) == 2
