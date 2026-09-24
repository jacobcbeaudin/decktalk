"""What `record` did for one section, and what it judged about the result."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.recordings import GONE, RecordingLog, file_digest, input_hash, text_digest
from decktalk.findings import Code, Finding, Location
from decktalk.media.pagereport import FrameGap, PageReport


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


def test_a_file_the_project_no_longer_has_digests_to_one_word(tmp_path: Path) -> None:
    assert file_digest(tmp_path / "gone.png") == GONE


def test_two_files_with_the_same_bytes_digest_alike(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"same")
    (tmp_path / "b").write_bytes(b"same")
    assert file_digest(tmp_path / "a") == file_digest(tmp_path / "b") == text_digest("same")


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


def test_the_worst_stall_counts_only_what_a_viewer_can_see() -> None:
    """Frames before the clock starts sit under the cover and are trimmed, so a scene may warm up."""
    gaps = (FrameGap(at=None, ms=900), FrameGap(at=0.05, ms=400), FrameGap(at=2.0, ms=120))
    stalls = log(report=PageReport(frameGaps=gaps))
    assert stalls.worst_stall_milliseconds == 120


def test_a_recording_with_no_gap_stalls_for_nothing() -> None:
    assert log().worst_stall_milliseconds == 0


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
