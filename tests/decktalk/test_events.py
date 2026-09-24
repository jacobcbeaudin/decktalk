"""Eleven names, four minted fields, and one stream a renderer cannot stop by raising."""

from __future__ import annotations

import json
import os
import typing
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from decktalk.events import EVENTS, Event, Events, JsonlSink, Level, Line, Log, Progress, RunStart, StageStart, Unit
from decktalk.findings import Code, Finding, Location
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Layer, Spend, SpendState

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
    "fetch": {"tool": "ffmpeg", "bytes": 1024, "total_bytes": 4096},
    "log": {"level": Level.INFO, "message": "One sentence."},
}


def test_the_eleven_names_are_the_ones_the_design_named() -> None:
    assert list(EVENTS) == list(NAMES)


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


def test_a_project_view_yields_only_the_runs_that_project_opened() -> None:
    machine = Events()
    project = machine.view(("r1",))
    seen: list[Event] = []
    with project.subscribe(seen.append):
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
