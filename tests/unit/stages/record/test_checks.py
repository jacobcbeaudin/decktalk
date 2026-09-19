"""What one finished recording is judged on: its length, its brightness, and what the page reported."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import Luma, RecordingChecks, RecordingLog
from decktalk.media import ffmpeg, frames
from decktalk.settings import RecordConfig
from decktalk.stages.record.checks import check_recording, katex_verdicts, label, log_verdicts, measure_luma
from decktalk.verdicts import Verdict

CFG = RecordConfig()
WEBM = Path("01.webm")

BAD_TEX = 'data-tex could not be parsed: "\\frac{1}" (write \\\\ for every backslash inside a template literal)'
NO_KATEX = "KaTeX did not load within 5 s, so [data-tex] elements stay plain text"


def a_log(**fields) -> RecordingLog:
    base = dict(url="u", requested_seconds=10.0, settle_seconds=0.0, load_seconds=0.0, clock_start_seconds=0.0)
    return RecordingLog(**{**base, **fields})


def bright(monkeypatch, duration: float = 10.0) -> None:
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: duration)
    monkeypatch.setattr(frames, "luma_at", lambda path, at: (90.0, 200.0))


def test_the_page_own_warnings_carry_the_katex_verdict():
    assert katex_verdicts([]) == []
    assert katex_verdicts(["a cue fired twice"]) == []
    assert katex_verdicts([BAD_TEX]) == [Verdict.KATEX_ERROR]
    assert katex_verdicts([NO_KATEX]) == [Verdict.KATEX_NOT_LOADED]
    assert katex_verdicts([BAD_TEX, NO_KATEX, BAD_TEX]) == [Verdict.KATEX_ERROR, Verdict.KATEX_NOT_LOADED]


def test_the_log_carries_the_verdicts_the_frames_cannot_see():
    recording_log = a_log()
    assert log_verdicts(recording_log, CFG) == []
    recording_log.t0_method, recording_log.t0_guessed = "fallback guess + settle", True
    recording_log.page_errors = ["ReferenceError: nope is not defined (index.html:5)"]
    recording_log.warnings = [BAD_TEX]
    recording_log.frame_gaps = [(1.0, 400)]
    assert log_verdicts(recording_log, CFG) == [
        Verdict.NO_COVER,
        Verdict.PAGE_ERROR,
        Verdict.KATEX_ERROR,
        Verdict.STALLED,
    ]


def test_a_dark_middle_frame_is_uncertain_and_a_short_recording_is_certain(monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 8.0)
    monkeypatch.setattr(frames, "luma_at", lambda path, at: (2.0, 4.0))
    checks = check_recording(WEBM, a_log(), CFG)
    assert checks.verdicts == (Verdict.BLACK_UNSURE, Verdict.TRUNCATED)
    assert checks.duration_seconds == 8.0 and checks.wanted_seconds == 10.0 and not checks.ok


def test_a_recording_that_ran_its_length_on_a_bright_page_passes(monkeypatch):
    bright(monkeypatch)
    checks = check_recording(WEBM, a_log(), CFG)
    assert checks.verdicts == () and checks.ok
    assert checks.luma == Luma(y10=90.0, y50=90.0, y90=90.0, max50=200.0)


def test_a_recording_asked_for_nothing_is_never_truncated(monkeypatch):
    bright(monkeypatch, duration=0.5)
    assert check_recording(WEBM, a_log(requested_seconds=0.0), CFG).verdicts == ()


def test_the_luma_is_read_at_a_tenth_a_half_and_nine_tenths(monkeypatch):
    seen: list[float] = []

    def luma_at(path, at):
        seen.append(round(at, 3))
        return (at, at * 2)

    monkeypatch.setattr(frames, "luma_at", luma_at)
    assert measure_luma(WEBM, 10.0) == Luma(y10=1.0, y50=5.0, y90=9.0, max50=10.0)
    assert seen == [1.0, 5.0, 9.0, 5.0]


def test_the_line_a_table_prints_names_every_verdict_and_the_stall_length():
    checks = RecordingChecks(10.0, 10.0, Luma(1, 1, 1, 1), (Verdict.NO_COVER, Verdict.STALLED))
    assert label(checks, 140) == f"{Verdict.NO_COVER.label} {Verdict.STALLED.label} 140ms"
    assert label(checks, 0) == f"{Verdict.NO_COVER.label} {Verdict.STALLED.label}"
    assert label(RecordingChecks(1.0, 1.0, Luma(1, 1, 1, 1)), 0) == Verdict.OK.label
    assert label(None, 0) == Verdict.OK.label


def test_a_page_that_fetched_from_another_origin_is_a_certain_finding():
    """A recording that depends on a host the project does not own cannot be rebuilt from the project."""
    from decktalk.artifacts import RecordingLog
    from decktalk.settings import RecordConfig
    from decktalk.stages.record.checks import log_verdicts

    made = dict(url="http://project.localhost/deck/index.html", requested_seconds=3.0, settle_seconds=0.5,
                load_seconds=0.2, clock_start_seconds=0.7)  # fmt: skip
    clean = RecordingLog(**made, t0_guessed=False)
    assert Verdict.CDN_ASSET not in log_verdicts(clean, RecordConfig())
    reached = RecordingLog(**made, t0_guessed=False, external=["https://cdn.example.com"])
    assert Verdict.CDN_ASSET in log_verdicts(reached, RecordConfig())
    assert Verdict.CDN_ASSET.certain
