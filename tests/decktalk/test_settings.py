"""The tuning tables: how the four layers stack, what each one refuses, and what every key publishes."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from decktalk.errors import ConfigError
from decktalk.pipeline import Stage
from decktalk.settings import (
    STANDALONE_ENV,
    Settings,
    env_warnings,
    load_settings,
    read_user_toml,
    user_config_path,
)


def test_settings_layering_defaults_toml_env():
    s = load_settings(
        toml={"video": {"preset": "veryfast", "crf": 20}},
        environ={"DECKTALK_VIDEO_CRF": "23", "DECKTALK_RECORD_SETTLE_SECONDS": "0.8"},
    )
    assert s.video.preset == "veryfast"  # from toml
    assert s.video.crf == 23  # env wins over toml
    assert s.record.settle_seconds == 0.8  # env, float coerced
    assert s.video.fps == 25  # default


def test_settings_bad_env_value_is_config_error():
    with pytest.raises(ConfigError):
        load_settings(toml={}, environ={"DECKTALK_VIDEO_FPS": "thirty"})


def test_settings_defaults_are_complete():
    s = Settings()
    assert s.narration.model and s.verify.diff_level > 0 and s.elevenlabs.api_base.startswith("https://")


def test_a_tuning_value_outside_its_range_fails_at_load_naming_its_table_and_key():
    """A number that would divide by zero or break an encoder is a config error, not a traceback."""
    for table, key, value, must in [
        ("video", "fps", 0, "must be above zero"),
        ("video", "crf", -9, "must be an x264 quality between 0 and 51"),
        ("video", "width", -4, "must be above zero"),
        # The tuning table of a stage is named after the stage it tunes.
        (Stage.RECORD.value, "retries", -1, "must not be negative"),
        (Stage.VERIFY.value, "min_changed_percent", 140.0, "must be a percentage between 0 and 100"),
        (Stage.RECORD.value, "black_ymax", 900.0, "must be a luma between 0 and 255"),
    ]:
        with pytest.raises(ConfigError) as info:
            load_settings(toml={table: {key: value}}, environ={}, user={})
        assert str(info.value) == f"[{table}] {key}: {must}, got {value!r}"


def test_a_tuning_value_of_the_wrong_type_names_its_table_and_key():
    """The located error is what lets a CLI fill the error slot's path and hint."""
    with pytest.raises(ConfigError) as info:
        load_settings(toml={"video": {"fps": "high"}}, environ={}, user={})
    assert str(info.value).startswith("[video] fps: expected int, got 'high'")


def test_an_environment_variable_obeys_the_same_range_as_the_table():
    """Every layer that sets a key goes through the same reader, so every layer is checked."""
    with pytest.raises(ConfigError) as info:
        load_settings(toml={}, environ={"DECKTALK_VIDEO_FPS": "0"}, user={})
    assert str(info.value) == "[video] fps: must be above zero, got 0"


def test_the_tuning_tables_are_frozen_so_one_run_never_retunes_another():
    """A flag rebuilds the table it overrides, which is why the dataclasses hold still."""
    settings = load_settings(toml={}, environ={}, user={})
    with pytest.raises(FrozenInstanceError):
        settings.video.preset = "veryfast"
    faster = replace(settings, video=replace(settings.video, preset="veryfast"))
    assert faster.video.preset == "veryfast" and settings.video.preset == "medium"


def test_every_tuning_key_carries_the_sentence_the_reference_prints():
    """The generated page is read from the fields, so a field with no sentence is a blank row."""
    for table in fields(Settings):
        cls = fields(getattr(Settings(), table.name))
        for f in cls:
            assert f.metadata.get("doc"), f"{table.name}.{f.name}"
            assert f.metadata["doc"].endswith("."), f"{table.name}.{f.name}"


def test_user_settings_file_warns_about_unknown_keys(tmp_path, caplog):
    path = tmp_path / "decktalk.toml"
    path.write_text("[record]\nsettle_second = 0.8\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="decktalk"):
        assert read_user_toml(path) == {"record": {"settle_second": 0.8}}
    assert [r.getMessage() for r in caplog.records] == [
        f"{path}: [record]: ignoring unknown key 'settle_second' (did you mean 'settle_seconds'?)."
    ]


def test_a_mistyped_environment_variable_warns_and_a_real_one_does_not(caplog):
    """A variable DeckTalk does not read has no effect, so the warning is what says the name never took hold."""
    environ = {"DECKTALK_VIDEO_PRESSET": "veryfast", "DECKTALK_PROJECT": ".", "DECKTALK_VIDEO_PRESET": "fast"}
    with caplog.at_level("WARNING", logger="decktalk"):
        settings = load_settings(toml={}, environ=environ)
    messages = [r.getMessage() for r in caplog.records]
    expected = "environment: ignoring unknown key 'DECKTALK_VIDEO_PRESSET' (did you mean 'DECKTALK_VIDEO_PRESET'?)."
    assert messages == [expected]
    # The variable that is read takes effect, and the two that are known say nothing.
    assert settings.video.preset == "fast"


def test_every_standalone_variable_is_known_to_the_warning():
    """A variable read outside the tuning tables would otherwise warn on every run that set it."""
    assert env_warnings({name: "x" for name in STANDALONE_ENV}) == []


def test_user_settings_sit_between_defaults_and_the_project(tmp_path, monkeypatch):
    user_file = tmp_path / "decktalk.toml"
    user_file.write_text('[video]\npreset = "veryfast"\ncrf = 22\n[record]\nsettle_seconds = 0.9\n', encoding="utf-8")
    monkeypatch.setenv("DECKTALK_CONFIG", str(user_file))
    assert user_config_path() == user_file
    s = load_settings(toml={"video": {"crf": 20}}, environ={"DECKTALK_RECORD_SETTLE_SECONDS": "1.2"})
    assert s.video.preset == "veryfast"  # from the user file
    assert s.video.crf == 20  # the project wins over the user file
    assert s.record.settle_seconds == 1.2  # the environment wins over both
    user_file.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        read_user_toml(user_file)
