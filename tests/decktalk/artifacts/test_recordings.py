"""What `record` did for one section, and what it judged about the result."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.recordings import RecordingLog
from decktalk.findings import Code, Finding, Location
from support.pages import a_recording


def log(**fields: object) -> RecordingLog:
    base = {"section": 3, "made_from": {"scene": "abc"}, "recording": a_recording()}
    return RecordingLog.model_validate({**base, **fields})


def test_every_judgement_the_recorder_made_is_one_list_of_findings() -> None:
    """The page's own warnings and the checks over the frames are read by code, never as sentences."""
    judged = log(
        findings=(
            Finding(
                code=Code.RECORD_BLACK,
                message="the frame half way through is black, so nothing the slide declares was on screen.",
                location=Location(where="deck/index.html", section=3),
            ),
        )
    )
    assert [found.code for found in judged.findings] == [Code.RECORD_BLACK]


def test_the_log_round_trips_through_its_own_file(tmp_path: Path) -> None:
    written = log(recording=a_recording(assets=("deck/index.html",), external=("https://cdn.example",)))
    path = written.write(tmp_path / "03.json")
    assert RecordingLog.read(path) == written
