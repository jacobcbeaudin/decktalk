"""What `record` did for one section, and what it judged about the result."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import stored
from decktalk.artifacts.recordings import RecordingLog, input_hash
from decktalk.findings import Code, Finding, Location
from decktalk.media.pagereport import PageReport


def log(**fields: object) -> RecordingLog:
    base = {
        "section": 3,
        "url": "http://project.localhost/deck/index.html",
        "input_hash": "abc",
        "requested_seconds": 10.0,
        "settle_seconds": 0.3,
        "load_seconds": 0.4,
        "clock_start_seconds": 0.8,
        "report": PageReport(),
    }
    return RecordingLog(**{**base, **fields})


def test_the_order_a_page_asked_for_its_files_in_is_not_part_of_the_key(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"one")
    (tmp_path / "b").write_bytes(b"two")
    first = input_hash(["url"], {"a": tmp_path / "a", "b": tmp_path / "b"})
    second = input_hash(["url"], {"b": tmp_path / "b", "a": tmp_path / "a"})
    assert first == second


def test_swapping_one_picture_moves_the_key_although_no_markup_changed(tmp_path: Path) -> None:
    picture = tmp_path / "hero.png"
    picture.write_bytes(b"one")
    before = input_hash(["url"], {"hero.png": picture})
    picture.write_bytes(b"two")
    assert input_hash(["url"], {"hero.png": picture}) != before


def test_a_newer_engine_records_again_what_an_older_one_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording carries the recorder, the probe and the contract that made it, so an upgrade moves the key."""
    picture = tmp_path / "hero.png"
    picture.write_bytes(b"one")
    before = input_hash(["url"], {"hero.png": picture})
    monkeypatch.setattr(stored, "ENGINE_VERSION", "999.0.0")
    assert input_hash(["url"], {"hero.png": picture}) != before


def test_the_head_is_cut_at_the_cover_when_one_was_found_and_at_the_estimate_otherwise() -> None:
    assert log(t0_seconds=1.1).trim_seconds == 1.1
    assert log().trim_seconds == 0.8


def test_every_judgement_the_recorder_made_is_one_list_of_findings() -> None:
    """The page's own warnings and the checks over the frames are read by code, never as sentences."""
    judged = log(
        findings=(
            Finding(
                code=Code.PAGE_STALLED,
                message="the page went 120 ms without drawing, which a viewer sees as a freeze.",
                location=Location(where="deck/index.html", section=3),
            ),
        )
    )
    assert [found.code for found in judged.findings] == [Code.PAGE_STALLED]


def test_the_log_round_trips_through_its_own_file(tmp_path: Path) -> None:
    written = log(assets=(Path("deck/index.html"),), external=("https://cdn.example",))
    path = written.write(tmp_path / "03.json")
    assert RecordingLog.read(path) == written
