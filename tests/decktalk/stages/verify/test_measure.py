"""The ffmpeg calls behind the cue plan, and the row each cue comes out with."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.events import Event, Level, RunLog
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine.run import Run
from decktalk.media import frames
from decktalk.media.frames import Decoded
from decktalk.media.pagereport import PageReport
from decktalk.page import ENTRANCES
from decktalk.results import SkipReason
from decktalk.settings import CLICK_LEVEL_DBFS
from decktalk.stages.verify.measure import (
    best_probe,
    click_seconds,
    cue_checks,
    declared_spans,
    film_starts,
    fitted_note,
    neighbours_of,
    planned_cues,
)
from support.pages import elements, write_log

from .conftest import SECTION_SECONDS, Measurements, opened

STARTS = {1: 0.0, 2: SECTION_SECONDS}
"""Where the two sections of the test film sit."""

CUES = {1: {"1.1:a": 2.0}}
"""One cue, well inside its section, so nothing is skipped for want of room."""


# ---- where the film's sections sit ---------------------------------------------------------------


def test_the_section_starts_are_read_from_the_placements(assembled: Callable[..., Inputs]) -> None:
    """The placements are the film's own record of its shape, so nothing adds the files up again."""
    inputs = assembled(CUES)
    starts, total = film_starts(inputs, inputs.workspace.film)
    assert starts == STARTS
    assert total == pytest.approx(2 * SECTION_SECONDS)


def test_a_film_with_no_placements_falls_back_to_the_section_files(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    inputs.workspace.placements_path.unlink()
    measured.duration = 4.0
    starts, total = film_starts(inputs, inputs.workspace.film)
    assert starts == {1: 0.0, 2: 4.0}
    assert total == pytest.approx(8.0)


# ---- the spans the page declares ------------------------------------------------------------------


def test_a_cues_forward_span_is_read_from_the_catalog_the_page_published(assembled: Callable[..., Inputs]) -> None:
    """The catalog says what each effect is, so the forward allowance is the page's own arithmetic."""
    inputs = assembled(CUES)
    [drawn] = elements({"1.1": ["1.1:a"]}, text="")["1.1"]
    drawn["attrs"]["data-in-style"] = "draw"
    report = PageReport.model_validate({"catalog": [{"scene": "1", "elements": {"1.1": [drawn]}}]})
    write_log(inputs, 1, report=report, requested_seconds=SECTION_SECONDS)
    assert declared_spans(inputs, 1) == {"1.1:a": pytest.approx(ENTRANCES["draw"].seconds)}


def test_a_section_that_was_never_recorded_declares_no_span(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    assert declared_spans(inputs, 1) == {}


def test_every_other_cue_of_the_section_becomes_a_neighbour_on_the_films_clock() -> None:
    rows = neighbours_of({"a": 1.0, "b": 2.0}, {"b": 0.3}, 10.0, "a")
    assert [(row.at, row.span) for row in rows] == [(12.0, 0.3)]


# ---- the probes and the click ------------------------------------------------------------------------


def test_the_probe_with_the_largest_margin_wins(assembled: Callable[..., Inputs], measured: Measurements) -> None:
    inputs = assembled(CUES)
    measured.shares = iter([1.0, 0.5, 0.5, 9.0, 0.5, 0.5])
    best = best_probe(decoded(inputs), 1.9, 0.0, 2.0, [0.1, 0.2], inputs)
    assert best is not None
    assert best[0] == pytest.approx(8.5)
    assert best[1] == pytest.approx(9.0)
    assert best[3] == pytest.approx(2.2)


def test_no_probe_at_all_measures_nothing(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    assert best_probe(decoded(inputs), 1.9, 0.0, 2.0, [], inputs) is None


def test_the_click_is_the_loudest_sample_in_the_window(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(CUES)
    rate = inputs.settings.audio.sample_rate
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


def decoded(inputs: Inputs) -> Decoded:
    """The film as the stage decodes it, which the autouse fixture answers with the test's own numbers."""
    return frames.decode(inputs.workspace.film, frames.Wanted())


def checked(inputs: Inputs, run: Run, opted: set[tuple[int, str]]) -> tuple:
    """Both passes of the cue loop over the one cue the test film declares."""
    planned = planned_cues(inputs, run, STARTS, 2 * SECTION_SECONDS, [(1, "1.1:a")], opted)
    return cue_checks(inputs, run, inputs.workspace.film, planned, decoded(inputs))


def judged(inputs: Inputs) -> tuple[tuple, list]:
    """Run the cue loop over the one cue the test film declares, and hand back its rows and findings."""
    with opened(inputs.root) as run:
        rows = checked(inputs, run, set())
        return rows, list(run.findings)


def test_a_cue_nothing_happened_at_is_an_error(assembled: Callable[..., Inputs], measured: Measurements) -> None:
    inputs = assembled(CUES)
    measured.changed = 0.0
    rows, found = judged(inputs)
    assert [row.code for row in found] == [Code.CUE_NO_CHANGE]
    assert "0.00 percent" in found[0].message
    assert rows[0].shown is None


def test_a_cue_that_passed_by_a_thin_margin_is_a_warning(
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
    """A null offset never reads as a passing row: CUE_NO_ONSET flags it."""
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
    assert rows[0].offset_seconds == pytest.approx(0.4)


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


LIMIT_SECONDS = 0.1
"""The 80 ms offset limit with the half frame at 25 fps beside it, which a reveal may land on and pass."""


@pytest.mark.parametrize(
    ("onset", "off"),
    [
        pytest.param(2.0 + LIMIT_SECONDS, False, id="late on the limit"),
        pytest.param(2.001 + LIMIT_SECONDS, True, id="late one ms past it"),
        pytest.param(2.0 - LIMIT_SECONDS, False, id="early on the limit"),
        pytest.param(1.999 - LIMIT_SECONDS, True, id="early one ms past it"),
    ],
)
def test_a_reveal_on_the_offset_limit_passes_and_one_millisecond_past_it_is_off(
    assembled: Callable[..., Inputs], measured: Measurements, onset: float, off: bool
) -> None:
    """The limit is the configured milliseconds and half a frame more, so its own rounding never fails it."""
    inputs = assembled(CUES)
    assert inputs.settings.verify.cue_offset_max_ms == 80.0
    assert inputs.settings.video.fps == 25
    measured.changed = 9.0
    measured.series = [(onset - 0.04, 0.0), (onset, 9.0)]
    rows, found = judged(inputs)
    assert rows[0].offset_seconds == pytest.approx(onset - 2.0)
    assert ([row.code for row in found] == [Code.CUE_OFF]) is off, found


@pytest.mark.parametrize(
    ("control", "no_change"),
    [pytest.param(0.15, False, id="margin on the floor"), pytest.param(0.16, True, id="margin under the floor")],
)
def test_a_reveal_whose_margin_reaches_its_floor_changed_and_one_under_it_did_not(
    assembled: Callable[..., Inputs], measured: Measurements, control: float, no_change: bool
) -> None:
    """The changed share alone is well over its floor, so only the margin over the control decides."""
    inputs = assembled(CUES)
    floor = inputs.settings.verify.margin_min_points
    measured.changed, measured.control = 0.25, control
    assert measured.changed > 2 * inputs.settings.verify.changed_share_min_percent
    assert (measured.changed - control == floor) is not no_change
    measured.series = [(1.9, 0.0), (2.02, 9.0)]
    _rows, found = judged(inputs)
    assert (Code.CUE_NO_CHANGE in [row.code for row in found]) is no_change


def test_a_cue_the_author_opted_out_of_is_skipped_and_never_measured(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    with opened(inputs.root) as run:
        rows = checked(inputs, run, {(1, "1.1:a")})
        assert run.findings == []
    assert rows[0].skipped is SkipReason.OPTED_OUT


def test_a_cue_fitted_between_its_neighbours_is_a_detail_that_says_nothing_is_wrong(
    assembled: Callable[..., Inputs],
) -> None:
    """Four such lines on the clean starter read to a first-time user as four problems."""
    inputs = assembled({1: {"1.1:a": 2.0, "1.1:b": 2.3}})
    lines: list[Event] = []
    with opened(inputs.root) as run, run.machine.events.subscribe(lines.append):
        planned_cues(inputs, run, STARTS, 2 * SECTION_SECONDS, [(1, "1.1:a")], set())
    notes = [line for line in lines if isinstance(line, RunLog)]
    assert notes and all(line.level is Level.DEBUG for line in notes)
    assert notes[0].message[0].isupper()
    assert "s after its word" in notes[0].message and "not a problem" in notes[0].message


def test_the_fitted_line_names_each_delay_as_seconds():
    said = fitted_note(1, "1.1:title", [0.7, 1.281])
    assert said.startswith("Cue 1.1:title in section 1 ")
    assert "0.7 s and 1.281 s after its word" in said
