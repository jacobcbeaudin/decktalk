"""The warning for a key nobody reads, and the key it offers."""

from __future__ import annotations

from decktalk.settings import BY_ID
from decktalk.tomlmap.suggest import did_you_mean, unknown_key_message, unknown_key_warnings


class TestMessages:
    """An unknown key is a warning with the nearest name, because a typo is the common failure."""

    def test_an_unknown_key_names_the_closest_one_that_is_known(self) -> None:
        assert "did you mean 'width'?" in unknown_key_message("widht", ["width", "height"], "[video]")

    def test_an_unknown_key_with_nothing_near_it_names_nothing(self) -> None:
        assert unknown_key_message("zzz", ["width"], "[video]") == "[video]: ignoring unknown key 'zzz'."

    def test_one_warning_per_unknown_key_in_a_table(self) -> None:
        assert len(unknown_key_warnings({"width": 1, "a": 2, "b": 3}, ["width"], "[video]")) == 2

    def test_the_did_you_mean_clause_is_empty_when_nothing_is_near(self) -> None:
        assert did_you_mean("verify.cue_offset_maks_ms", ["verify.cue_offset_max_ms"]) == (
            " Did you mean 'verify.cue_offset_max_ms'?"
        )
        assert did_you_mean("zzzzzzzz", ["verify.cue_offset_max_ms"]) == ""

    def test_a_thing_written_under_the_wrong_table_is_offered_the_key_that_sets_it(self) -> None:
        """A person who remembers `fps` and not `[video]` meant the frame rate, not the retry count."""
        assert did_you_mean("record.fps", BY_ID) == " Did you mean 'video.output_fps'?"

    def test_a_part_too_short_to_name_one_thing_falls_back_to_the_closest_spelling(self) -> None:
        assert did_you_mean("mix.db", ["mix.ambience_db", "mix.music_db"]) == " Did you mean 'mix.music_db'?"
