"""The five config verbs, driven against a real project file so a write is judged by the loader."""

from __future__ import annotations

import json

import pytest

from decktalk import settings as knobs
from decktalk.cli import config as commands
from decktalk.results import ConfigGetResult, ConfigListResult, Layer
from support.projects import write_project


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """A project directory the config verbs act on, which is where a write lands."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "machine.toml"))
    return write_project(tmp_path)


def test_list_prints_every_key_with_its_value_and_its_layer(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "list", "--json")
    assert ran.exit_code == 0
    written = ConfigListResult.model_validate_json(ran.out)
    assert len(written.keys) > 50
    assert {row.layer for row in written.keys} <= set(Layer)


def test_list_takes_one_table_at_a_time(run, project_dir) -> None:
    written = ConfigListResult.model_validate_json(run("-p", str(project_dir), "config", "list", "video", "--json").out)
    assert written.keys
    assert all(row.key.startswith("video.") for row in written.keys)


def test_list_refuses_a_table_that_is_not_one(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "list", "nope")
    assert ran.exit_code == 2
    assert "is not a table" in ran.err


def test_get_prints_one_key(run, project_dir) -> None:
    written = ConfigGetResult.model_validate_json(
        run("-p", str(project_dir), "config", "get", "video.crf", "--json").out
    )
    assert written.key.key == "video.crf"


def test_get_refuses_a_key_that_is_not_one(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "get", "video.crfff")
    assert ran.exit_code == 2
    assert "not a settings key" in ran.err


def test_set_writes_the_project_file_through_the_loader(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "set", "video.crf", "20", "--json")
    assert ran.exit_code == 0
    assert json.loads(ran.out)["value"] == 20
    assert "crf = 20" in (project_dir / "decktalk.toml").read_text(encoding="utf-8")


def test_set_refuses_a_value_the_loader_would_refuse(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "set", "video.crf", "99")
    assert ran.exit_code == 2
    assert "must be between 0 and 32" in ran.err
    assert "crf" not in (project_dir / "decktalk.toml").read_text(encoding="utf-8")


def test_set_on_a_dry_run_reports_the_change_and_writes_nothing(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "set", "video.crf", "20", "--dry-run", "--json")
    assert json.loads(ran.out)["dry_run"] is True
    assert "crf" not in (project_dir / "decktalk.toml").read_text(encoding="utf-8")


def test_unset_takes_one_key_back_out(run, project_dir) -> None:
    run("-p", str(project_dir), "config", "set", "video.crf", "20")
    ran = run("-p", str(project_dir), "config", "unset", "video.crf", "--json")
    assert ran.exit_code == 0
    assert json.loads(ran.out)["keys"] == ["video.crf"]
    assert "crf" not in (project_dir / "decktalk.toml").read_text(encoding="utf-8")


def test_unset_prints_the_value_that_now_applies_and_the_layer_it_comes_from(run, project_dir) -> None:
    """The settings panel has the remover say what decides the key now, which the library measures."""
    run("-p", str(project_dir), "config", "set", "video.crf", "20")
    written = json.loads(run("-p", str(project_dir), "config", "unset", "video.crf", "--json").out)
    assert written["previous"] == 20
    assert written["effective"] == knobs.BY_ID["video.crf"].default
    assert written["layer"] == Layer.DEFAULT.value


def test_unset_of_a_key_the_file_does_not_set_says_so(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "unset", "video.crf")
    assert ran.exit_code == 3
    assert "sets nothing under" in ran.err


def test_unset_of_a_whole_table_without_a_terminal_refuses_and_names_all(run, project_dir) -> None:
    run("-p", str(project_dir), "config", "set", "video.crf", "20")
    ran = run("-p", str(project_dir), "config", "unset", "video")
    assert ran.exit_code == 3
    assert "--all" in ran.err


def test_unset_of_a_whole_table_with_all_takes_every_key_it_set(run, project_dir) -> None:
    run("-p", str(project_dir), "config", "set", "video.crf", "20")
    ran = run("-p", str(project_dir), "config", "unset", "video", "--all", "--json")
    assert ran.exit_code == 0
    assert json.loads(ran.out)["keys"] == ["video.crf"]


def test_explain_reads_one_knob_whole(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "explain", "video.crf", "--json")
    assert ran.exit_code == 0
    written = json.loads(ran.out)
    assert written["key"] == "video.crf"
    assert written["range"]
    assert written["environment"] == "DECKTALK_VIDEO_CRF"
    assert written["docs"].endswith("/configuration#video")


def test_explain_holds_a_candidate_to_the_same_range(run, project_dir) -> None:
    ran = run("-p", str(project_dir), "config", "explain", "video.crf", "--value", "99")
    assert ran.exit_code == 2


def test_the_five_verbs_are_registered_on_the_one_nested_group() -> None:
    assert {"list", "get", "set", "unset", "explain"} == {
        registered.name for registered in commands.config.registered_commands
    }
