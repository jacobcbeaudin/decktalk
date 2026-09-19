"""The typed options each command is handed, and the fields a command cannot read."""

from __future__ import annotations

import dataclasses

import pytest

from decktalk.cli.options import (
    AlignOptions,
    BuildOptions,
    ClipOptions,
    DoctorOptions,
    Options,
    RecordOptions,
    WordsOptions,
    load_project,
    project_root,
)
from decktalk.cli.parser import COMMANDS, build_parser
from decktalk.model import Project
from decktalk.scaffold import init

REQUIRED = {"init": ["somewhere"], "clip": ["1", "--start", "0", "--end", "1", "--out", "a.mp4"]}


def test_options_take_their_own_fields_from_the_parsed_line():
    args = build_parser().parse_args(["build", "-p", "d", "--only", "3-4", "--no-voice", "--crf", "20"])
    opts = BuildOptions.of(args)
    assert (opts.project, opts.only, opts.no_voice, opts.crf) == ("d", [3, 4], True, 20)
    assert (opts.preset, opts.force, opts.progress) == (None, False, None)
    assert opts.json is False and opts.strict is False


def test_a_flag_a_command_does_not_offer_is_a_field_it_does_not_have():
    """A handler cannot read a flag its command never took, because the field is not there."""
    assert not hasattr(AlignOptions(), "only")
    assert not hasattr(WordsOptions(), "force")
    assert not hasattr(DoctorOptions(), "project")


def test_an_option_object_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Options().json = True  # type: ignore[misc]


def test_every_command_builds_its_own_options_from_its_own_line():
    parser = build_parser()
    for command in COMMANDS:
        args = parser.parse_args([command.name, *REQUIRED.get(command.name, [])])
        opts = command.options.of(args)
        assert isinstance(opts, command.options), command.name
        assert opts.strict is False and opts.exit_zero is False, command.name


def test_a_flag_rebuilds_the_frozen_table_it_overrides(tmp_path, monkeypatch):
    """The tables are frozen, so a flag replaces one rather than writing into a shared object."""
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "deck", name="deck").root
    plain = Project.load(root)
    loaded = load_project(ClipOptions(project=str(root), preset="veryfast", crf=20))
    assert (loaded.settings.video.preset, loaded.settings.video.crf) == ("veryfast", 20)
    assert (plain.settings.video.preset, plain.settings.video.crf) != ("veryfast", 20)
    # A table no flag touched is the one the file gave, so an override reaches only what it names.
    assert loaded.settings.record == plain.settings.record
    recorded = load_project(RecordOptions(project=str(root), settle=1.5))
    assert recorded.settings.record.settle_seconds == 1.5
    assert recorded.settings.video == plain.settings.video


def test_the_root_an_error_names_its_file_against_follows_the_project_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("DECKTALK_PROJECT", raising=False)
    assert project_root(ClipOptions(project=str(tmp_path))) == tmp_path.resolve()
    assert project_root(DoctorOptions()) is None
    monkeypatch.setenv("DECKTALK_PROJECT", str(tmp_path))
    assert project_root(ClipOptions()) == tmp_path.resolve()
    # The project file stands for the directory holding it, as `Project.load` reads it.
    named = tmp_path / "decktalk.toml"
    named.write_text("[project]\nname = 'deck'\n", encoding="utf-8")
    assert project_root(ClipOptions(project=str(named))) == tmp_path.resolve()
