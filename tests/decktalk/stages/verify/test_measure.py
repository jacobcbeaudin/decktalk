"""The ffmpeg calls behind the cue plan, and the row each cue comes out with."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media import frames
from decktalk.media.pagereport import PageReport
from decktalk.results import SkipReason
from decktalk.settings import CLICK_LEVEL_DBFS
from decktalk.stages.verify.measure import (
    best_probe,
    click_seconds,
    cue_checks,
    declared_spans,
    film_starts,
    neighbours_of,
)

from .conftest import SECTION_SECONDS, Measurements, opened

STARTS = {1: 0.0, 2: SECTION_SECONDS}
"""Where the two sections of the test film sit."""

CUES = {1: {"1.1:a": 2.0}}
"""One cue, well inside its section, so nothing is skipped for want of room."""


# ---- where the film's sections sit ---------------------------------------------------------------


def test_the_section_starts_are_read_from_the_cut_list(assembled: Callable[..., Inputs]) -> None:
    """The cut list is the film's own record of its shape, so nothing adds the files up again."""
    inputs = assembled(CUES)
    starts, total = film_starts(inputs, inputs.workspace.film)
    assert starts == STARTS
    assert total == pytest.approx(2 * SECTION_SECONDS)


def test_a_film_with_no_cut_list_falls_back_to_the_section_files(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    inputs.workspace.cuts_path.unlink()
    measured.duration = 4.0
    starts, total = film_starts(inputs, inputs.workspace.film)
    assert starts == {1: 0.0, 2: 4.0}
    assert total == pytest.approx(8.0)


# ---- the spans the page declares ------------------------------------------------------------------


def test_a_cues_forward_span_is_read_from_the_catalog_the_page_published(assembled: Callable[..., Inputs]) -> None:
    """The catalog says what each effect is, so the forward allowance is the page's own arithmetic."""
    inputs = assembled(CUES)
    inputs.workspace.recordings_dir.mkdir(parents=True, exist_ok=True)
    report = PageReport.model_validate(
        {
            "catalog": [
                {
                    "scene": "1",
                    "elements": {
                        "1.1": [
                            {
                                "attrs": {"data-in": "a", "data-in-style": "draw"},
                                "moments": {"data-in": "1.1:a"},
                                "text": "",
                                "box": {"x": 0, "y": 0, "w": 10, "h": 10},
                            }
                        ]
                    },
                }
            ]
        }
    )
    RecordingLog(
        section=1,
        url="http://project.localhost/deck/index.html",
        input_hash="abc",
        requested_seconds=SECTION_SECONDS,
        settle_seconds=0.1,
        load_seconds=0.1,
        clock_start_seconds=0.2,
        report=report,
    ).write(inputs.workspace.recording_log("01"))
    assert declared_spans(inputs, 1) == {"1.1:a": pytest.approx(0.48)}


def test_a_section_that_was_never_recorded_declares_no_span(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    assert declared_spans(inputs, 1) == {}


def test_every_other_cue_of_the_section_becomes_a_neighbour_on_the_films_clock() -> None:
    rows = neighbours_of({"a": 1.0, "b": 2.0}, {"b": 0.3}, 10.0, "a")
    assert [(row.at, row.span) for row in rows] == [(12.0, 0.3)]


# ---- the probes and the click ------------------------------------------------------------------------


def test_the_probe_with_the_largest_margin_wins(
    assembled: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = assembled(CUES)
    shares = iter([1.0, 0.5, 0.5, 9.0, 0.5, 0.5])
    monkeypatch.setattr(frames, "changed_pixels_percent", lambda _p, _a, _b, **_kw: next(shares))
    best = best_probe(inputs.workspace.film, 1.9, 0.0, 2.0, [0.1, 0.2], inputs)
    assert best is not None
    assert best[0] == pytest.approx(8.5)
    assert best[1] == pytest.approx(9.0)
    assert best[3] == pytest.approx(2.2)


def test_no_probe_at_all_measures_nothing(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    assert best_probe(inputs.workspace.film, 1.9, 0.0, 2.0, [], inputs) is None


def test_the_click_is_the_loudest_sample_in_the_window(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    rate = inputs.settings.video.sample_rate
    measured.samples = [0] * rate
    measured.samples[rate // 4] = 20000
    heard = click_seconds(inputs.workspace.film, 2.0, inputs)
    assert heard == pytest.approx(2.0 - inputs.settings.verify.click_search_seconds + 0.25, abs=1e-3)


def test_a_window_with_nothing_loud_enough_in_it_finds_no_click(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    """The floor sits under the level DeckTalk writes its own click at, which the key's hazard names."""
    inputs = assembled(CUES)
    assert inputs.settings.verify.click_floor_dbfs < CLICK_LEVEL_DBFS
    measured.samples = [1] * 100
    assert click_seconds(inputs.workspace.film, 2.0, inputs) is None


# ---- the row each cue comes out with -------------------------------------------------------------------


def judged(inputs: Inputs, **kwargs: object) -> tuple[tuple, list]:
    """Run the cue loop over the one cue the test film declares, and hand back its rows and findings."""
    with opened(inputs.root) as run:
        rows = cue_checks(
            inputs, run, inputs.workspace.film, STARTS, 2 * SECTION_SECONDS, [(1, "1.1:a")], set(), **kwargs
        )
        return rows, list(run.findings)


def test_a_cue_nothing_happened_at_is_a_certain_finding(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    measured.changed = 0.0
    rows, found = judged(inputs)
    assert [row.code for row in found] == [Code.CUE_NO_CHANGE]
    assert "0.00 percent" in found[0].message
    assert rows[0].shown is None


def test_a_cue_that_passed_by_a_thin_margin_is_an_uncertain_finding(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    measured.changed = 0.2
    measured.series = [(1.9, 0.0), (1.94, 0.0), (2.0, 9.0)]
    _rows, found = judged(inputs)
    assert Code.CUE_THIN_CHANGE in [row.code for row in found]


def test_a_cue_whose_onset_no_frame_shows_is_reported_and_never_passed_over(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    """A null offset used to read as a passing row, which is the hole CUE_NO_ONSET closes."""
    inputs = assembled(CUES)
    measured.changed = 9.0
    measured.series = []
    rows, found = judged(inputs)
    assert [row.code for row in found] == [Code.CUE_NO_ONSET]
    assert rows[0].skipped is SkipReason.NO_ONSET
    assert rows[0].change_percent == pytest.approx(9.0)
    assert "9.00 percent" in found[0].message


def test_a_reveal_outside_the_offset_limit_names_the_milliseconds_and_the_limit(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    measured.changed = 9.0
    measured.series = [(1.9, 0.0), (2.4, 9.0)]
    rows, found = judged(inputs)
    assert [row.code for row in found] == [Code.CUE_OFF]
    assert "+400 ms" in found[0].message
    assert f"{inputs.settings.verify.cue_offset_max_ms:.0f} ms" in found[0].message
    assert rows[0].offset == pytest.approx(0.4)


def test_a_reveal_on_its_word_says_nothing_and_reports_where_it_landed(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    measured.changed = 9.0
    measured.series = [(1.9, 0.0), (2.02, 9.0)]
    rows, found = judged(inputs)
    assert found == []
    assert rows[0].shown == pytest.approx(2.02)
    assert rows[0].spoken == pytest.approx(2.0)


def test_a_cue_the_author_opted_out_of_is_skipped_and_never_measured(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    with opened(inputs.root) as run:
        rows = cue_checks(
            inputs, run, inputs.workspace.film, STARTS, 2 * SECTION_SECONDS, [(1, "1.1:a")], {(1, "1.1:a")}
        )
        assert run.findings == []
    assert rows[0].skipped is SkipReason.OPTED_OUT
