"""Writing one key into a settings file and removing keys from one, judged whole before the file is replaced."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.results import Layer, Scope
from decktalk.settings import BY_ID
from decktalk.settings.edit import key_or_table, rows, stating, under, unset, write
from decktalk.settings.layers import load
from support.links import link


class TestTheWriter:
    """A write goes through the whole loader first, and it keeps the file a person wrote."""

    def test_a_write_keeps_every_comment_and_reports_the_layer_it_wins_from(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("# my project\n[verify]\n# how late\ncue_offset_max_ms = 100\n", encoding="utf-8")
        written = write(path, "verify.cue_offset_max_ms", "120", scope=Scope.PROJECT, environ={})
        assert "# my project" in path.read_text(encoding="utf-8")
        assert "# how late" in path.read_text(encoding="utf-8")
        assert (written.previous, written.value, written.effective) == (100, 120.0, 120.0)
        assert written.layer is Layer.PROJECT
        assert written.written == (path,)

    def test_a_write_into_a_file_that_does_not_exist_yet_creates_the_table(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        write(path, "video.preset", "veryfast", scope=Scope.PROJECT, environ={})
        assert path.read_text(encoding="utf-8") == '[video]\npreset = "veryfast"\n'

    def test_a_dry_run_reports_the_change_and_writes_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        written = write(path, "video.preset", "veryfast", scope=Scope.PROJECT, dry_run=True, environ={})
        assert written.dry_run is True
        assert not path.exists()

    def test_a_value_the_loader_would_refuse_never_reaches_the_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("[verify]\ncue_offset_max_ms = 100\n", encoding="utf-8")
        with pytest.raises(InputError, match="must be between 0.001 and 1"):
            write(path, "verify.onset_rise_points", "20", scope=Scope.PROJECT, environ={})
        assert path.read_text(encoding="utf-8") == "[verify]\ncue_offset_max_ms = 100\n"

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            write(tmp_path / "decktalk.toml", "verify.cue_offset_maks_ms", "120", scope=Scope.PROJECT, environ={})

    def test_a_machine_key_written_to_the_project_file_names_the_other_flag(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="machine-scoped") as caught:
            write(tmp_path / "decktalk.toml", "tools.ffmpeg", "/opt/ffmpeg", scope=Scope.PROJECT, environ={})
        assert caught.value.hint is not None
        assert "--scope machine" in caught.value.hint

    def test_a_write_a_higher_layer_shadows_says_so(self, tmp_path: Path) -> None:
        environ = {"DECKTALK_VIDEO_PRESET": "slow"}
        written = write(tmp_path / "decktalk.toml", "video.preset", "veryfast", scope=Scope.PROJECT, environ=environ)
        assert written.layer is Layer.ENVIRONMENT
        assert written.effective == "slow"

    def test_a_file_that_is_not_valid_toml_is_refused_in_a_sentence_and_left_alone(self, tmp_path: Path) -> None:
        """tomlkit's own parser error is not a refusal, so a caller that catches refusals met a traceback."""
        path = tmp_path / "decktalk.toml"
        path.write_text("[video\n", encoding="utf-8")
        with pytest.raises(InputError, match="is not valid TOML") as refused:
            write(path, "video.width", "1280", scope=Scope.PROJECT, environ={})
        assert refused.value.location is not None and refused.value.location.line == 1
        assert path.read_text(encoding="utf-8") == "[video\n"

    def test_a_machine_file_kept_behind_a_link_is_written_where_the_link_points(self, tmp_path: Path) -> None:
        """A machine file often lives in a dotfiles repository, and replacing the link would unhook it."""
        kept = tmp_path / "dotfiles" / "decktalk.toml"
        kept.parent.mkdir()
        kept.write_text("", encoding="utf-8")
        path = tmp_path / "decktalk.toml"
        link(path, kept)
        write(path, "record.concurrency", "2", scope=Scope.MACHINE, environ={})
        assert path.is_symlink()
        assert "concurrency = 2" in kept.read_text(encoding="utf-8")

    @pytest.mark.parametrize("removing", [False, True])
    def test_a_project_file_linked_out_of_the_project_is_never_written_through(
        self, tmp_path: Path, removing: bool
    ) -> None:
        """A project that arrives with its `decktalk.toml` linked elsewhere chose where `config set` writes."""
        victim = tmp_path / "victim.toml"
        victim.write_text("[video]\nwidth = 640\n", encoding="utf-8")
        root = tmp_path / "project"
        root.mkdir()
        link(root / "decktalk.toml", victim)
        with pytest.raises(InputError, match="leads outside the project"):
            if removing:
                unset(root / "decktalk.toml", "video.width", scope=Scope.PROJECT, environ={})
            else:
                write(root / "decktalk.toml", "video.width", "1280", scope=Scope.PROJECT, environ={})
        assert victim.read_text(encoding="utf-8") == "[video]\nwidth = 640\n"


class TestTheRemover:
    """A removal is the write's opposite, and it answers with the layer that shows through."""

    def test_a_removal_keeps_every_comment_and_reports_the_layer_below(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("# my project\n[verify]\n# how late\ncue_offset_max_ms = 100\n", encoding="utf-8")
        removed = unset(path, "verify.cue_offset_max_ms", scope=Scope.PROJECT, environ={})
        text = path.read_text(encoding="utf-8")
        assert "# my project" in text and "# how late" in text
        assert "cue_offset_max_ms" not in text
        assert removed.keys == ("verify.cue_offset_max_ms",)
        assert removed.previous == 100
        assert removed.layer is Layer.DEFAULT
        assert removed.effective == BY_ID["verify.cue_offset_max_ms"].default

    def test_the_table_the_key_sat_in_stays_where_it_was(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text('# the encoder\n[video]\npreset = "veryfast"\n', encoding="utf-8")
        unset(path, "video.preset", scope=Scope.PROJECT, environ={})
        assert path.read_text(encoding="utf-8") == "# the encoder\n[video]\n"

    def test_a_key_the_file_never_stated_leaves_the_file_alone(self, tmp_path: Path) -> None:
        """The call is idempotent, because an agent that cannot read the file has to be able to call it twice."""
        path = tmp_path / "decktalk.toml"
        path.write_text('[video]\npreset = "veryfast"\n', encoding="utf-8")
        removed = unset(path, "video.crf", scope=Scope.PROJECT, environ={})
        assert removed.keys == ("video.crf",)
        assert removed.previous is None
        assert path.read_text(encoding="utf-8") == '[video]\npreset = "veryfast"\n'

    def test_several_keys_are_taken_out_in_one_write_or_not_at_all(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text('[video]\npreset = "veryfast"\ncrf = 20\n', encoding="utf-8")
        with pytest.raises(InputError):
            unset(path, "video.preset", "tools.ffmpeg", scope=Scope.PROJECT, environ={})
        assert "preset" in path.read_text(encoding="utf-8")
        removed = unset(path, "video.preset", "video.crf", scope=Scope.PROJECT, environ={})
        assert removed.keys == ("video.preset", "video.crf") and removed.written == (path,)
        assert path.read_text(encoding="utf-8") == "[video]\n"

    def test_a_removal_from_a_file_that_is_not_there_writes_no_file(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        assert unset(path, "video.crf", scope=Scope.PROJECT, environ={}).previous is None
        assert not path.exists()

    def test_an_environment_variable_that_was_shadowing_the_file_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text('[video]\npreset = "veryfast"\n', encoding="utf-8")
        removed = unset(path, "video.preset", scope=Scope.PROJECT, environ={"DECKTALK_VIDEO_PRESET": "slow"})
        assert removed.layer is Layer.ENVIRONMENT
        assert removed.effective == "slow"

    def test_a_file_the_loader_would_refuse_whole_is_not_written_back(self, tmp_path: Path) -> None:
        """The removal goes through the loader the write goes through, so neither launders a bad file."""
        path = tmp_path / "machine.toml"
        path.write_text('[video]\npreset = "slow"\n[tools]\nffmpeg = "/opt/ffmpeg"\n', encoding="utf-8")
        with pytest.raises(InputError, match="'video.preset' is project-scoped, so it belongs in"):
            unset(path, "tools.ffmpeg", scope=Scope.MACHINE, environ={})
        assert "ffmpeg" in path.read_text(encoding="utf-8")

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            unset(tmp_path / "decktalk.toml", "verify.cue_offset_maks_ms", scope=Scope.PROJECT, environ={})

    def test_a_machine_key_taken_out_of_the_project_file_names_the_other_flag(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="machine-scoped") as caught:
            unset(tmp_path / "decktalk.toml", "tools.ffmpeg", scope=Scope.PROJECT, environ={})
        assert caught.value.hint is not None
        assert "--scope machine" in caught.value.hint


class TestTheNames:
    """A caller names one key or one table, and each listing reads the same membership rule."""

    @pytest.mark.parametrize(
        ("published", "named", "expected"),
        [("video.crf", "video.crf", True), ("video.crf", "video", True), ("video.crf", "vid", False)],
    )
    def test_a_key_is_under_its_own_name_and_its_table_and_never_a_prefix_of_a_word(
        self, published: str, named: str, expected: bool
    ) -> None:
        assert under(published, named) is expected

    def test_a_name_that_is_neither_a_key_nor_a_table_is_refused_with_the_nearest(self) -> None:
        key_or_table("video")
        with pytest.raises(InputError, match="video.crf"):
            key_or_table("video.crff")

    def test_a_listing_of_a_table_nothing_publishes_is_refused(self) -> None:
        loaded = load(None, environ={})
        assert {row.key for row in rows(loaded, "video", defaults=True, changed=False)} == {
            key for key in BY_ID if key.startswith("video.")
        }
        with pytest.raises(InputError, match="not a table"):
            rows(loaded, "vidoe", defaults=True, changed=False)

    def test_a_table_is_taken_out_whole_only_when_the_caller_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("[video]\ncrf = 20\npreset = 'fast'\n", encoding="utf-8")
        assert stating(path, "video.crf", whole_table=False) == ("video.crf",)
        with pytest.raises(InputError, match="a whole table"):
            stating(path, "video", whole_table=False)
        assert set(stating(path, "video", whole_table=True)) == {"video.crf", "video.preset"}
