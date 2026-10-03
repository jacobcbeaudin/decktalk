"""The numbers that are deliberately not settings, each published with its formula and its reason."""

from __future__ import annotations

import pytest

from decktalk.settings import BY_ID, Settings
from decktalk.settings.layers import (
    load,
)
from decktalk.settings.numbers import NUMBERS, NUMBERS_BY_ID, Number


class TestTheNumbersThatAreNotSettings:
    """Every derived expression and every constant is published, so a missing setting is explained."""

    def test_no_published_number_is_also_a_key(self) -> None:
        assert not set(NUMBERS_BY_ID) & set(BY_ID)

    @pytest.mark.parametrize("number", NUMBERS, ids=lambda number: number.id)
    def test_every_input_of_a_formula_resolves(self, number: Number) -> None:
        for name in number.reads:
            assert name in BY_ID or name in NUMBERS_BY_ID

    @pytest.mark.parametrize("number", NUMBERS, ids=lambda number: number.id)
    def test_every_sentence_opens_with_the_nature_it_declares(self, number: Number) -> None:
        assert number.sentence.split(":")[0].lower() == number.nature.value

    def test_every_derived_relation_holds_at_every_frame_size(self) -> None:
        for width, height in ((1920, 1080), (1280, 720), (3840, 2160)):
            settings = load(machine={}, project={"video": {"width": width, "height": height}}, environ={}).settings
            assert NUMBERS_BY_ID["verify.block_width"].at(settings) * 8 == width
            assert NUMBERS_BY_ID["verify.block_height"].at(settings) * 8 == height
            assert NUMBERS_BY_ID["verify.probe_width"].at(settings) * 4 == width

    def test_the_reference_lead_sits_outside_the_window_the_offset_limit_allows(self) -> None:
        settings = Settings()
        lead = NUMBERS_BY_ID["verify.reference_lead_seconds"].at(settings)
        assert lead > settings.verify.cue_offset_max_ms / 1000

    def test_the_click_floor_sits_under_the_level_the_click_is_generated_at(self) -> None:
        assert BY_ID["verify.click_floor_dbfs"].bounds is not None
        assert BY_ID["verify.click_floor_dbfs"].bounds.le == NUMBERS_BY_ID["CLICK_LEVEL_DBFS"].at(Settings())
