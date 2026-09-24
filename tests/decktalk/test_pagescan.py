"""The static page scan: what the measured catalog says about a slide, with no picture to look at."""

from __future__ import annotations

import pytest

from decktalk.pagescan import (
    CAPTION_BAND,
    REVEAL_SECONDS,
    cdn_assets,
    cues_overlap,
    in_caption_band,
    off_stage,
    slide_findings,
)
from decktalk.verdicts import Verdict

STAGE = {"width": 1920, "height": 1080}


def row(cue: str | None, x: int, y: int, w: int, h: int) -> dict:
    return {"cue": cue, "box": {"x": x, "y": y, "w": w, "h": h}}


def test_a_reveal_wholly_on_the_stage_is_not_a_finding():
    rows = [row("1.1a", 0, 0, 1920, 900), row("1.1b", 100, 100, 200, 200)]
    assert off_stage(rows, **STAGE, page="deck/index.html", section=1) == []


@pytest.mark.parametrize(
    ("x", "y", "w", "h"),
    [
        (-10, 100, 200, 200),  # off the left
        (1800, 100, 200, 200),  # past the right
        (100, -10, 200, 200),  # above the top
        (100, 1000, 200, 200),  # below the bottom
    ],
)
def test_a_reveal_that_leaves_the_stage_is_a_certain_finding(x, y, w, h):
    """An element outside the stage is never on screen, which no frame difference would show."""
    [found] = off_stage([row("1.1a", x, y, w, h)], **STAGE, page="deck/index.html", section=1)
    assert found.verdict is Verdict.OFF_STAGE and found.verdict.certain
    assert (found.section, found.cue, found.where) == (1, "1.1a", "deck/index.html")
    assert found.detail is not None and "1920 by 1080" in found.detail


def test_an_element_with_no_cue_is_not_judged():
    """The rule is about a reveal, and an element the page never reveals is the author's own layout."""
    assert off_stage([row(None, -500, -500, 10, 10)], **STAGE, page="p.html", section=1) == []
    assert in_caption_band([row(None, 0, 1070, 10, 10)], height=1080, page="p.html", section=1) == []


def test_a_reveal_under_the_caption_band_is_an_uncertain_finding():
    """A viewer reading captions cannot see what the captions are drawn over."""
    top = round(1080 * (1 - CAPTION_BAND))
    assert in_caption_band([row("1.1a", 0, 0, 100, top)], height=1080, page="p.html", section=1) == []
    [found] = in_caption_band([row("1.1a", 0, top - 10, 100, 100)], height=1080, page="p.html", section=1)
    assert found.verdict is Verdict.IN_CAPTION_BAND and not found.verdict.certain
    assert found.detail is not None and str(top) in found.detail


def test_two_cues_closer_than_a_reveal_lasts_run_into_each_other():
    """One reveal is still playing when the next fires, so neither lands on its own picture."""
    assert cues_overlap({"1.1a": 0.0, "1.1b": 1.0}, page="p.html", section=1) == []
    [found] = cues_overlap({"1.1a": 1.0, "1.1b": 1.0 + REVEAL_SECONDS / 2}, page="p.html", section=1)
    assert found.verdict is Verdict.CUES_OVERLAP and not found.verdict.certain
    assert found.cue == "1.1b" and found.detail is not None and "1.1a" in found.detail
    # The pair is judged in time order however the map was written.
    [same] = cues_overlap({"1.1b": 1.17, "1.1a": 1.0}, page="p.html", section=1)
    assert same.cue == "1.1b"
    # A gap of exactly one reveal is not an overlap, because the first has finished.
    assert cues_overlap({"a": 0.0, "b": REVEAL_SECONDS}, page="p.html", section=1) == []


def test_an_asset_from_another_origin_is_a_certain_finding_named_once():
    """A recording that depends on a host the project does not own cannot be rebuilt from the project."""
    origins = ["https://cdn.example.com", "https://cdn.example.com", "https://fonts.example.net"]
    found = cdn_assets(origins, page="deck/index.html")
    assert [f.verdict for f in found] == [Verdict.CDN_ASSET, Verdict.CDN_ASSET]
    assert all(f.verdict.certain and f.where == "deck/index.html" for f in found)
    assert "cdn.example.com" in found[0].detail and "fonts.example.net" in found[1].detail
    assert cdn_assets([], page="deck/index.html") == []


def test_one_call_gives_every_static_judgement_a_slide_supports():
    """`preflight` asks once per section, so the three judgements arrive together and in one shape."""
    rows = [row("1.1a", -10, 100, 50, 50), row("1.1b", 0, 1000, 100, 100)]
    times = {"1.1a": 1.0, "1.1b": 1.1}
    found = slide_findings(rows, times, **STAGE, page="deck/index.html", section=1)
    assert {f.verdict for f in found} == {Verdict.OFF_STAGE, Verdict.IN_CAPTION_BAND, Verdict.CUES_OVERLAP}
    assert all(f.where == "deck/index.html" and f.section == 1 for f in found)
    assert all(f.detail for f in found)
