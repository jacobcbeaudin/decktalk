"""The recording log: what one section was recorded from, where t=0 landed, and how it checked out."""

from __future__ import annotations

import json

import pytest

from decktalk.artifacts.recordings import (
    Luma,
    RecordingChecks,
    RecordingLog,
    file_digest,
    gap_time,
    input_hash,
    text_digest,
)
from decktalk.verdicts import Verdict


def a_log(**fields) -> RecordingLog:
    base = dict(url="u", requested_seconds=1.0, settle_seconds=0.0, load_seconds=0.0, clock_start_seconds=0.0)
    return RecordingLog(**{**base, **fields})


def test_the_trim_prefers_the_measured_start_over_the_recorders_estimate():
    recording_log = a_log(requested_seconds=5, settle_seconds=0.5, load_seconds=0.1, clock_start_seconds=0.7)
    assert recording_log.trim_seconds == 0.7
    recording_log.t0_seconds = 0.2
    assert recording_log.trim_seconds == 0.2


def test_a_log_written_by_record_round_trips_with_its_checks(tmp_path):
    path = tmp_path / "01.json"
    checks = RecordingChecks(
        duration_seconds=10.0415,
        wanted_seconds=10.3,
        luma=Luma(y10=50.0, y50=60.0, y90=70.0, max50=80.0),
        verdicts=(Verdict.NO_COVER, Verdict.STALLED),
    )
    a_log(assets=["deck/index.html", "media/panel.png"], input_hash="abc123", checks=checks, t0_seconds=1.2).save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["checks"] == {
        "duration_seconds": 10.041,
        "wanted_seconds": 10.3,
        "luma": {"y10": 50.0, "y50": 60.0, "y90": 70.0, "max50": 80.0},
        "verdicts": [Verdict.NO_COVER.to_dict(), Verdict.STALLED.to_dict()],
    }
    again = RecordingLog.load(path)
    assert again is not None and again.checks is not None
    assert again.checks.verdicts == (Verdict.NO_COVER, Verdict.STALLED)
    assert again.assets == ["deck/index.html", "media/panel.png"] and again.input_hash == "abc123"
    assert not again.checks.ok


def test_a_log_from_a_run_that_wrote_no_checks_still_loads(tmp_path):
    path = tmp_path / "01.json"
    bare = {"url": "u", "requested_seconds": 1, "settle_seconds": 0, "load_seconds": 0, "clock_start_seconds": 0}
    path.write_text(json.dumps(bare), encoding="utf-8")
    recording_log = RecordingLog.load(path)
    assert recording_log is not None
    assert recording_log.checks is None and recording_log.warnings == [] and recording_log.assets == []
    assert RecordingLog.load(tmp_path / "nothing.json") is None


def test_the_worst_stall_counts_only_what_a_viewer_sees():
    recording_log = a_log()
    recording_log.frame_gaps = [(None, 900), (0.05, 216), (0.4, 120), (12.8, 132)]
    # The first gap ended under the cover. The second began there and shows for 50 ms.
    assert recording_log.worst_stall_ms == 132
    recording_log.frame_gaps = [(None, 900), (0.05, 216)]
    assert recording_log.worst_stall_ms == 50
    recording_log.frame_gaps = [(None, 900)]
    assert recording_log.worst_stall_ms == 0
    recording_log.frame_gaps = []
    assert recording_log.worst_stall_ms == 0


def test_a_gap_before_the_clock_is_written_as_null(tmp_path):
    path = tmp_path / "01.json"
    recording_log = a_log()
    recording_log.frame_gaps = [(float("-inf"), 900), (None, 400), (0.05, 216)]
    recording_log.save(path)
    text = path.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    # Standard JSON parsers such as JSON.parse and jq reject the -Infinity token.
    data = json.loads(text, parse_constant=lambda token: pytest.fail(f"non-standard JSON token {token}"))
    assert data["frame_gaps"] == [[None, 900], [None, 400], [0.05, 216]]
    again = RecordingLog.load(path)
    assert again is not None and again.frame_gaps == [(None, 900), (None, 400), (0.05, 216)]
    assert again.worst_stall_ms == 50


def test_gap_time_turns_the_page_value_for_a_gap_before_the_clock_into_none():
    # The page reports a gap that ended before narration t=0 at negative infinity, and the recorder
    # is the boundary where that becomes None.
    assert gap_time(float("-inf")) is None
    assert gap_time(float("nan")) is None
    assert gap_time(None) is None
    assert gap_time(0.4) == 0.4
    assert gap_time(0) == 0


def test_a_file_hashes_to_its_content_and_a_missing_one_says_so(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<p>one</p>", encoding="utf-8")
    first = file_digest(page)
    assert len(first) == 16
    page.write_text("<p>two</p>", encoding="utf-8")
    assert file_digest(page) != first
    assert file_digest(tmp_path / "nothing.png") == "gone"


def test_a_slice_of_a_page_hashes_to_its_text(tmp_path):
    """One scene's markup joins the key as a digest, because the key is a line of strings."""
    assert len(text_digest('<div data-scene="1">one</div>')) == 16
    assert text_digest("one") != text_digest("two")
    assert text_digest("") == text_digest("")


def test_the_input_hash_moves_when_an_asset_changes_and_not_when_the_order_does(tmp_path):
    page = tmp_path / "index.html"
    picture = tmp_path / "panel.png"
    page.write_text("<img src=panel.png>", encoding="utf-8")
    picture.write_bytes(b"first picture")
    files = {"index.html": page, "panel.png": picture}
    base = input_hash(["url"], files)
    assert base == input_hash(["url"], {"panel.png": picture, "index.html": page})
    assert base != input_hash(["other url"], files)
    assert base != input_hash(["url"], {"index.html": page})

    # The founder's own case: the page is untouched and only the picture beside it is replaced.
    picture.write_bytes(b"second picture")
    assert input_hash(["url"], files) != base
