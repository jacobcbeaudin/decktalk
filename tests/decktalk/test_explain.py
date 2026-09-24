"""The three things about a knob that have to be computed rather than looked up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.explain import explain
from decktalk.findings import Code
from decktalk.results import Layer, Scope
from decktalk.settings import BY_ID
from decktalk.tomlmap import Nature, Source

CUES = {
    "estimated": False,
    "sections": {
        "01": [
            {"cue": "1.1:first", "on": "first", "at": 1.0},
            {"cue": "1.1:close", "on": "close", "at": 1.1},
            {"cue": "1.1:far", "on": "far", "at": 5.0},
        ]
    },
}
"""One section whose second cue sits a tenth of a second after its first, which a wider lead swallows."""


@pytest.fixture(autouse=True)
def _no_machine_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the per-machine file at nothing, so the machine running the suite never sets a key."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path_factory.mktemp("machine") / "decktalk.toml"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project that states one key and has been cued once."""
    (tmp_path / "decktalk.toml").write_text("[verify]\ncue_offset_max_ms = 100\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "cue-times.json").write_text(json.dumps(CUES), encoding="utf-8")
    return tmp_path


class TestTheKeyItself:
    """Everything the schema already publishes travels with the explanation, so one call is enough."""

    def test_the_record_comes_through_whole(self, project: Path) -> None:
        found = explain("verify.cue_offset_max_ms", project=project)
        key = BY_ID["verify.cue_offset_max_ms"]
        assert found.description == key.description
        assert found.hazard == key.hazard
        assert found.range == "must be between 20 and 400"
        assert found.unit == "milliseconds"
        assert found.decides == (Code.CUE_OFF,)
        assert found.scope is Scope.PROJECT
        assert found.nature is Nature.TASTE
        assert found.source is Source.CHOSEN
        assert found.environment == "DECKTALK_VERIFY_CUE_OFFSET_MAX_MS"
        assert found.docs.endswith("/settings/verify.cue_offset_max_ms")

    def test_a_key_with_a_wider_type_range_publishes_both(self) -> None:
        found = explain("verify.onset_rise_points")
        assert found.range == "must be between 0.001 and 1"
        assert found.typed_range == "must be between 0 and 100"

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            explain("verify.cue_offset_maks_ms")

    def test_a_knob_is_explainable_before_a_project_exists(self) -> None:
        found = explain("video.output_fps")
        assert found.value == 25
        assert found.winner is Layer.DEFAULT
        assert found.measured is False


class TestTheLayerView:
    """The one lookup that cannot be answered from the schema, because it is about this machine."""

    def test_every_layer_that_stated_the_key_is_a_row_with_the_winner_last(self, project: Path) -> None:
        found = explain("verify.cue_offset_max_ms", project=project)
        assert [row.layer for row in found.layers] == [Layer.DEFAULT, Layer.PROJECT]
        assert found.winner is Layer.PROJECT
        assert found.value == 100.0
        assert found.default == 80.0

    def test_the_row_of_a_file_carries_the_file_and_the_line(self, project: Path) -> None:
        row = explain("verify.cue_offset_max_ms", project=project).layers[-1]
        assert row.file is not None
        assert row.line == 2


class TestTheNumbersTheKeyFeeds:
    """A knob is only understood once the arithmetic above it is visible."""

    def test_each_derived_number_is_shown_with_its_inputs_at_their_effective_values(self, project: Path) -> None:
        found = explain("verify.cue_offset_max_ms", project=project)
        lead = next(number for number in found.numbers if number.id == "verify.reference_lead_seconds")
        assert lead.reads["verify.cue_offset_max_ms"] == 100.0
        assert lead.value == pytest.approx(0.16)
        assert lead.sentence.startswith("Derived:")

    def test_a_candidate_recomputes_every_number_the_key_feeds(self, project: Path) -> None:
        found = explain("verify.cue_offset_max_ms", project=project, value="300")
        lead = found.numbers[0]
        assert found.candidate == 300.0
        assert lead.candidate == pytest.approx(0.36)

    def test_a_key_that_feeds_nothing_shows_no_arithmetic(self, project: Path) -> None:
        assert explain("video.preset", project=project).numbers == ()

    def test_a_candidate_outside_the_safe_range_is_refused_rather_than_computed(self, project: Path) -> None:
        with pytest.raises(InputError, match="must be between 20 and 400"):
            explain("verify.cue_offset_max_ms", project=project, value="5000")


class TestTheCuesACandidateWouldClamp:
    """The reason the explainer sits above the settings layer rather than inside it."""

    def test_a_wider_lead_names_the_cue_it_reaches_back_over(self, project: Path) -> None:
        assert explain("verify.cue_offset_max_ms", project=project, value="300").clamped == ("1.1:close",)

    def test_the_lead_in_force_already_names_a_cue_that_is_too_close(self, project: Path) -> None:
        assert explain("verify.cue_offset_max_ms", project=project).clamped == ("1.1:close",)

    def test_a_narrow_lead_clamps_nothing(self, project: Path) -> None:
        assert explain("verify.cue_offset_max_ms", project=project, value="20").clamped == ()

    def test_a_key_that_feeds_no_lead_names_no_cue(self, project: Path) -> None:
        assert explain("video.crf", project=project, value="30").clamped == ()

    def test_a_project_that_has_never_been_cued_says_it_measured_nothing(self, tmp_path: Path) -> None:
        found = explain("verify.cue_offset_max_ms", project=tmp_path, value="300")
        assert found.measured is False
        assert found.clamped == ()

    def test_a_cue_file_that_will_not_parse_costs_nothing(self, project: Path) -> None:
        (project / "build" / "cue-times.json").write_text("{", encoding="utf-8")
        assert explain("verify.cue_offset_max_ms", project=project).measured is False
