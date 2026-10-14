"""Verify on a moving picture: masks over what the page said moved, and the landings of every motion.

Every film here is drawn in this process, a black frame with white boxes at 25 frames a second, so
each case is exact and needs no ffmpeg and no browser.
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

import pytest

from decktalk.findings import Code
from decktalk.media.frames import Decoded, Mask, Rect, Size
from decktalk.media.pagereport import MotionLane
from decktalk.settings import Settings
from decktalk.stages.verify import motion
from decktalk.stages.verify.plan import onset_offset_seconds

FPS = 25
SIZE = Size(96, 54)
SETTINGS = Settings()
LEVEL = SETTINGS.verify.onset_diff_luma
FLOOR = SETTINGS.verify.motion_floor_percent


def film(seconds: float, draw: Callable[[float], list[tuple[int, int, int, int]]]) -> Decoded:
    """A decoded film whose frame at second `t` holds the white boxes `draw(t)` names."""
    kept: dict[int, bytes] = {}
    for index in range(round(seconds * FPS)):
        pixels = bytearray(SIZE.width * SIZE.height)
        for x, y, w, h in draw(index / FPS):
            for row in range(max(0, y), min(SIZE.height, y + h)):
                left, right = max(0, x), min(SIZE.width, x + w)
                pixels[row * SIZE.width + left : row * SIZE.width + right] = b"\xff" * max(0, right - left)
        kept[index] = bytes(pixels)
    return Decoded(rate=Fraction(FPS), frames={SIZE: kept}, ends={SIZE: len(kept)})


def lane(departs: float, arrives: float, *, box: Rect, area: int, landings: tuple = ()) -> motion.Lane:
    return motion.Lane(kind="motion", box=box, area=area, departs=departs, arrives=arrives, landings=landings)


# ---- the mask ---------------------------------------------------------------------------------------


def a_reveal_beside_a_square_that_never_stops(t: float) -> list[tuple[int, int, int, int]]:
    square = (int(4 + 30 * t), 2, 6, 6)
    revealed = [(60, 30, 20, 20)] if t >= 1.0 else []
    return [square, *revealed]


def test_steady_motion_moves_a_reveal_s_onset_and_its_lane_masked_puts_it_back_on_its_frame() -> None:
    """Unmasked, the square's every step reads as the reveal starting early. Masked, the reveal lands on its cue."""
    moving = film(2.0, a_reveal_beside_a_square_that_never_stops)
    before, cue = 0.8, 1.0
    rise = SETTINGS.verify.onset_rise_points

    def onset(mask: Mask | None) -> float | None:
        series = moving.series(before, before, 1.4, level=LEVEL, size=SIZE, mask=mask)
        return onset_offset_seconds(series, before, cue, rise)

    unmasked = onset(None)
    assert unmasked is not None and unmasked < -1 / FPS
    swept = lane(0.0, 2.0, box=Rect(0, 0, 96, 10), area=36)
    masked = onset(motion.mask_over([swept], before - 0.4, 1.4, SIZE))
    assert masked is not None and 0 <= masked <= 1 / FPS


def test_a_lane_outside_the_window_a_cue_reads_masks_nothing() -> None:
    early = lane(0.0, 0.5, box=Rect(0, 0, 10, 10), area=100)
    assert motion.mask_over([early], 1.0, 2.0, SIZE) is None


def test_a_lane_is_read_from_the_recording_on_the_film_s_clock_at_the_probe_s_size() -> None:
    row = MotionLane.model_validate(
        {
            "id": "motion-1",
            "kind": "motion",
            "box": {"x": 0, "y": 120, "w": 1152, "h": 192},
            "size": {"w": 192, "h": 192},
            "departs": 0.2,
            "arrives": 0.6,
            "landings": [
                {"cue": "1.1:go", "kind": "depart", "at": 0.2},
                {"cue": "1.1:land", "kind": "arrive", "at": 0.6},
            ],
        }
    )
    (read,) = motion.lanes_of([row], start=10.0, recorded=Size(1920, 1080), probe=Size(480, 270))
    assert read.box == Rect(0, 30, 288, 48)
    assert read.area == 48 * 48
    assert (read.departs, read.arrives) == (10.2, 10.6)
    assert [(one.cue, one.kind, one.at) for one in read.landings] == [
        ("1.1:go", "depart", 10.2),
        ("1.1:land", "arrive", 10.6),
    ]


# ---- the landings -----------------------------------------------------------------------------------


def travelling(start: float, end: float, ease: Callable[[float], float]) -> Callable[[float], list]:
    """A 6 pixel square that travels 60 pixels from `start` to `end` under `ease`, then rests."""

    def at(t: float) -> list[tuple[int, int, int, int]]:
        u = min(1.0, max(0.0, (t - start) / (end - start)))
        return [(round(10 + 60 * ease(u)), 20, 6, 6)]

    return at


def firm(u: float) -> float:
    """An ease that keeps some of its speed into its last frame, so its last frame still moves."""
    return u * (1.4 - 0.4 * u)


def settle(u: float) -> float:
    """An ease to rest, which the eye reads as stopping well before its last frame."""
    return 1 - (1 - u) ** 5


LANE_BOX = Rect(0, 15, 96, 16)


def measured(
    decoded: Decoded, cue: str, kind: motion.LandingKind, at: float, *, departs: float, arrives: float
) -> motion.Landed:
    swept = lane(departs, arrives, box=LANE_BOX, area=36, landings=(motion.Landing(cue, kind, at),))
    (row,) = motion.measure(decoded, swept, level=LEVEL, floor=FLOOR, size=SIZE, settings=SETTINGS)
    return row


def test_a_departure_is_the_first_frame_after_its_cue_that_moves() -> None:
    """A cue at 1.5 s falls between frames 37 and 38, so the first frame that has moved is 1.52 s, 20 ms after it."""
    decoded = film(4.0, travelling(1.5, 3.0, firm))
    row = measured(decoded, "1.1:go", "depart", 1.5, departs=1.5, arrives=3.0)
    assert row.shown == pytest.approx(1.52)
    assert row.offset_seconds == pytest.approx(0.02)
    assert motion.finding(row, settings=SETTINGS, fps=FPS, film="film.mp4") is None


def test_a_firm_arrival_stops_on_its_cue_s_frame_and_passes() -> None:
    decoded = film(4.0, travelling(1.5, 3.0, firm))
    row = measured(decoded, "1.1:land", "arrive", 3.0, departs=1.5, arrives=3.0)
    assert row.shown == pytest.approx(3.0)
    assert motion.finding(row, settings=SETTINGS, fps=FPS, film="film.mp4") is None


def test_an_arrival_that_eases_to_rest_stops_before_its_word_and_has_no_edge() -> None:
    decoded = film(4.0, travelling(1.5, 3.0, settle))
    row = measured(decoded, "1.1:land", "arrive", 3.0, departs=1.5, arrives=3.0)
    assert row.shown is not None and row.shown < 3.0 - SETTINGS.verify.cue_offset_max_ms / 1000
    found = motion.finding(row, settings=SETTINGS, fps=FPS, film="film.mp4")
    assert found is not None and found.code is Code.MOTION_NO_EDGE


def test_a_motion_still_moving_well_after_its_arrival_cue_lands_off_its_word() -> None:
    decoded = film(4.0, travelling(1.5, 3.4, firm))
    row = measured(decoded, "1.1:land", "arrive", 3.0, departs=1.5, arrives=3.4)
    found = motion.finding(row, settings=SETTINGS, fps=FPS, film="film.mp4")
    assert found is not None and found.code is Code.MOTION_OFF
    assert "+400 ms" in found.message


# ---- the honesty guard ------------------------------------------------------------------------------


def test_a_mask_over_more_of_the_frame_than_the_setting_allows_is_said() -> None:
    limit = SETTINGS.verify.motion_mask_max_percent
    wide = Mask((Rect(0, 0, 96, int(54 * (limit + 10) / 100)),), SIZE)
    found = motion.mask_finding(wide, size=SIZE, settings=SETTINGS, cue="1.1:a", section=1, film="film.mp4")
    assert found is not None and found.code is Code.MOTION_MASK_LARGE
    narrow = Mask((Rect(0, 0, 10, 10),), SIZE)
    assert motion.mask_finding(narrow, size=SIZE, settings=SETTINGS, cue="1.1:a", section=1, film="film.mp4") is None
