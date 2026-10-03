"""The five layers: how they stack, what each file may set, the overrides, the relations and the warnings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import tomlkit

from decktalk.errors import ErrorCode, InputError
from decktalk.results import Layer, Scope
from decktalk.settings import BY_ID, KEYS, STANDALONE_ENV, Settings
from decktalk.settings.layers import (
    env_warnings,
    key_warnings,
    load,
    machine_config_path,
    merge_tables,
    read_machine_toml,
    refuse_off_scope,
    route,
    scoped,
    value_of,
)
from decktalk.tomlmap import Key
from support.projects import MINIMAL_TOML, load_project


class TestTheFiveLayers:
    """Which value wins, and the record that says which one did."""

    def test_each_layer_overrides_the_one_below_it(self) -> None:
        """The project, the environment and `--set` compete on the preset, and each wins over the one before."""
        here = load(
            machine={"tools": {"ffmpeg": "/m"}},
            project={"video": {"preset": "veryfast", "crf": 20}},
            environ={"DECKTALK_VIDEO_CRF": "23", "DECKTALK_VIDEO_PRESET": "medium"},
            overrides=("video.preset=slow",),
        )
        assert here.settings.tools.ffmpeg == "/m"
        assert here.settings.video.crf == 23
        assert here.settings.video.preset == "slow"
        assert here.layers.winner("video.preset").layer is Layer.OVERRIDE
        assert [row.value for row in here.layers.of("video.preset")][1:] == ["veryfast", "medium", "slow"]
        assert here.settings.video.fps == 25

    def test_the_record_names_every_layer_that_stated_a_key(self) -> None:
        here = load(machine={}, project={"video": {"crf": 20}}, environ={"DECKTALK_VIDEO_CRF": "23"})
        rows = here.layers.of("video.crf")
        assert [row.layer for row in rows] == [Layer.DEFAULT, Layer.PROJECT, Layer.ENVIRONMENT]
        assert here.layers.winner("video.crf").layer is Layer.ENVIRONMENT
        assert here.layers.winner("video.crf").value == 23

    def test_a_key_nobody_stated_wins_from_the_default(self) -> None:
        here = load(machine={}, project={}, environ={})
        assert here.layers.winner("video.crf").layer is Layer.DEFAULT

    def test_the_record_carries_the_file_and_the_line_a_project_wrote(self, tmp_path: Path) -> None:
        (tmp_path / "decktalk.toml").write_text("[video]\ncrf = 20\n", encoding="utf-8")
        here = load(tmp_path, machine={}, environ={})
        row = here.layers.winner("video.crf")
        assert row.file is not None
        assert row.line == 2

    def test_an_environment_value_is_read_as_the_key_s_own_type(self) -> None:
        here = load(machine={}, project={}, environ={"DECKTALK_RECORD_SETTLE_SECONDS": "0.8"})
        assert here.settings.record.settle_seconds == 0.8

    def test_a_bad_environment_value_is_refused(self) -> None:
        with pytest.raises(InputError):
            load(machine={}, project={}, environ={"DECKTALK_VIDEO_FPS": "thirty"})

    def test_a_variable_decktalk_does_not_read_is_warned_about_by_name(self) -> None:
        assert env_warnings({"DECKTALK_ZZZZZZZZZZZZ": "1"}) == [
            "environment: ignoring unknown key 'DECKTALK_ZZZZZZZZZZZZ'."
        ]
        assert env_warnings({"DECKTALK_VIDEO_CRV": "20"})[0].endswith("(did you mean 'DECKTALK_VIDEO_CRF'?).")
        assert env_warnings({"DECKTALK_VIDEO_CRF": "20"}) == []

    def test_the_three_standalone_variables_name_no_key(self) -> None:
        assert not {name.lower().removeprefix("decktalk_") for name in STANDALONE_ENV} & {
            key.id.replace(".", "_") for key in KEYS
        }

    @pytest.mark.parametrize(
        ("platform", "environ", "expected"),
        [
            ("darwin", {}, "home/Library/Application Support/decktalk/machine.toml"),
            ("linux", {}, "home/.config/decktalk/machine.toml"),
            ("linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/decktalk/machine.toml"),
            ("win32", {"APPDATA": "/roaming"}, "/roaming/decktalk/machine.toml"),
            ("win32", {}, "home/AppData/Roaming/decktalk/machine.toml"),
            ("linux", {"DECKTALK_MACHINE_FILE": "/named.toml"}, "/named.toml"),
        ],
    )
    def test_the_per_machine_file_has_a_place_on_every_platform_from_the_environment_passed_in(
        self, platform: str, environ: dict[str, str], expected: str
    ) -> None:
        assert machine_config_path(environ, Path("home"), platform) == Path(expected)

    def test_no_machine_file_is_read_unless_one_is_named(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The process's own file is the machine's to read, and a loader handed none reads none."""
        planted = tmp_path / "planted.toml"
        planted.write_text("[tools]\ntimeout_seconds = 30\n", encoding="utf-8")
        monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(planted))
        monkeypatch.setenv("DECKTALK_TOOLS_TIMEOUT_SECONDS", "31")
        here = load(project={}, environ={})
        assert here.layers.winner("tools.timeout_seconds").layer is Layer.DEFAULT


class TestScope:
    """The per-machine file is restricted by key, because a limit is a statement about the film."""

    def test_a_project_key_in_the_machine_file_is_refused_by_name(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text("[verify]\ncue_offset_max_ms = 400\n", encoding="utf-8")
        with pytest.raises(InputError, match="project-scoped") as caught:
            read_machine_toml(path)
        assert caught.value.location is not None
        assert caught.value.location.line == 2
        assert caught.value.hint is not None
        assert "--scope project" in caught.value.hint

    def test_a_machine_key_in_the_machine_file_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text('[tools]\nffmpeg = "/opt/ffmpeg"\n', encoding="utf-8")
        assert read_machine_toml(path) == {"tools": {"ffmpeg": "/opt/ffmpeg"}}

    def test_a_table_that_is_not_a_tuning_table_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text('[project]\nname = "x"\n', encoding="utf-8")
        with pytest.raises(InputError, match="not a tuning table"):
            read_machine_toml(path)

    @pytest.mark.parametrize("key", [key for key in KEYS if key.scope is Scope.MACHINE], ids=lambda key: key.id)
    def test_every_machine_key_in_a_project_is_refused(self, key: Key) -> None:
        # A project someone else wrote must not choose what this machine runs or where it writes.
        project: dict[str, Any] = {key.name: "/tmp/elsewhere"}
        for table in reversed(key.table.split(".")):
            project = {table: project}
        with pytest.raises(InputError, match="machine-scoped") as caught:
            load(project=project, machine={}, environ={})
        assert caught.value.hint is not None
        assert "--scope machine" in caught.value.hint

    @pytest.mark.parametrize("key", ["elevenlabs.base_url", "dtsp.base_url"])
    def test_a_base_url_in_a_project_is_refused(self, key: str) -> None:
        """A request carries the key or the script to the host a base URL names, so only the machine names one."""
        table, name = key.split(".")
        with pytest.raises(InputError, match=f"'{key}' is machine-scoped"):
            load(project={table: {name: "http://127.0.0.1:9/v1"}}, machine={}, environ={})

    def test_a_base_url_the_machine_names_is_read_with_no_switch(self) -> None:
        machine = {"elevenlabs": {"base_url": "http://127.0.0.1:9/v1"}, "dtsp": {"base_url": "http://10.0.0.2:9"}}
        loaded = load(project={}, machine=machine, environ={}).settings
        assert (loaded.elevenlabs.base_url, loaded.dtsp.base_url) == ("http://127.0.0.1:9/v1", "http://10.0.0.2:9")

    def test_the_chromium_in_a_project_file_is_refused_at_its_line(self, tmp_path: Path) -> None:
        (tmp_path / "decktalk.toml").write_text(
            '[project]\nname = "x"\n\n[tools]\nchromium = "/tmp/not-a-browser"\n', encoding="utf-8"
        )
        with pytest.raises(InputError, match="tools.chromium") as caught:
            load(tmp_path, machine={}, environ={})
        assert caught.value.location is not None
        assert caught.value.location.line == 5

    def test_a_project_cannot_trust_its_own_page_over_the_machine(self) -> None:
        """A service marks strangers' pages untrusted once, on its machine, and no project loosens that."""
        untrusted = {"record": {"page_policy": "untrusted"}}
        assert load(project={}, machine=untrusted, environ={}).settings.record.page_policy == "untrusted"
        with pytest.raises(InputError, match="record.page_policy"):
            load(project={"record": {"page_policy": "trusted"}}, machine=untrusted, environ={})

    @pytest.mark.parametrize(
        "hostile",
        [
            "black:s=1x1,movie=/etc/passwd",
            "0x0e1116:s=2x2[v];movie=/etc/passwd",
            "#0e1116;}body{background:url(http://127.0.0.1/)",
            "red",
            "0x0e1116\n",
        ],
    )
    def test_a_slate_colour_that_is_not_a_hex_colour_is_refused_at_load(self, hostile: str) -> None:
        """The colour is placed inside a filter graph and a stylesheet, so only a colour ever reaches either."""
        places: list[dict[str, Any]] = [
            {"project": {"video": {"slate_color": hostile}}, "environ": {}},
            {"project": {}, "environ": {"DECKTALK_VIDEO_SLATE_COLOR": hostile}},
        ]
        for where in places:
            with pytest.raises(InputError, match="video.slate_color") as caught:
                load(machine={}, **where)
            assert caught.value.code is ErrorCode.INPUT

    @pytest.mark.parametrize("color", ["0x0e1116", "0XFFFFFF", "#0e1116", "#A0b1C2"])
    def test_a_slate_colour_in_either_hex_spelling_is_read(self, color: str) -> None:
        here = load(machine={}, project={"video": {"slate_color": color}}, environ={})
        assert here.settings.video.slate_color == color

    @pytest.mark.parametrize("hostile", ["../../user", "a/b", "voice?x=1", "voice id", "v\n", "v.1"])
    def test_a_voice_id_that_could_leave_its_path_segment_is_refused_at_load(self, hostile: str) -> None:
        """The id is a path segment of every speech request, so a stranger's project cannot aim the key elsewhere."""
        places: list[dict[str, Any]] = [
            {"project": {"voice": {"id": hostile}}, "environ": {}},
            {"project": {}, "environ": {"DECKTALK_VOICE_ID": hostile}},
        ]
        for where in places:
            with pytest.raises(InputError, match="voice.id"):
                load(machine={}, **where)

    def test_the_voice_id_is_read_from_the_project_and_overridden_by_its_variable(self) -> None:
        project = {"voice": {"id": "03SG2XsqDqqfP8RMUXX1"}}
        assert load(machine={}, project=project, environ={}).settings.voice.id == "03SG2XsqDqqfP8RMUXX1"
        exported = load(machine={}, project=project, environ={"DECKTALK_VOICE_ID": "kept_private-1"})
        assert exported.settings.voice.id == "kept_private-1"
        assert exported.layers.winner("voice.id").layer is Layer.ENVIRONMENT

    def test_a_project_key_in_a_project_is_let_through_and_read(self) -> None:
        project = {"verify": {"cue_offset_max_ms": 400}}
        refuse_off_scope(project, Scope.PROJECT, file=Path("decktalk.toml"))
        here = load(machine={}, project=project, environ={})
        assert here.settings.verify.cue_offset_max_ms == 400
        assert here.layers.winner("verify.cue_offset_max_ms").layer is Layer.PROJECT

    def test_malformed_toml_is_refused_at_its_own_line(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text('[tools]\nffmpeg = "open\n', encoding="utf-8")
        with pytest.raises(InputError) as caught:
            read_machine_toml(path)
        assert caught.value.location is not None
        assert caught.value.location.line == 2


class TestTheOverrideLayer:
    """One generic flag replaces every hand-kept flag, and it is routed by the scope each key publishes."""

    def test_every_pair_is_routed_by_the_scope_its_key_publishes(self) -> None:
        pairs = route(("verify.cue_offset_max_ms=120", "tools.ffmpeg=/opt/ffmpeg"))
        assert scoped(pairs, Scope.PROJECT) == {"verify.cue_offset_max_ms": "120"}
        assert scoped(pairs, Scope.MACHINE) == {"tools.ffmpeg": "/opt/ffmpeg"}

    def test_a_pair_with_no_equals_sign_is_refused(self) -> None:
        with pytest.raises(InputError, match="has no '='"):
            route(("verify.cue_offset_max_ms",))

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            route(("verify.cue_offset_maks_ms=120",))

    def test_project_content_is_refused_with_the_table_to_edit(self) -> None:
        with pytest.raises(InputError, match="project content"):
            route(("project.name=x",))

    def test_an_override_meets_the_same_range_a_written_value_meets(self) -> None:
        with pytest.raises(InputError, match="must be between"):
            load(machine={}, project={}, environ={}, overrides=("verify.onset_rise_points=20",))


class TestCrossTableRelations:
    """A relation an editor cannot check is enforced by the loader, which is the only place it can be."""

    def test_a_frame_gap_under_the_runtime_s_own_floor_is_refused(self) -> None:
        with pytest.raises(InputError):
            load(machine={}, project={"record": {"frame_gap_max_ms": 50}}, environ={})

    def test_the_relation_reads_a_published_number_by_name(self) -> None:
        assert BY_ID["record.frame_gap_max_ms"].requires == "record.frame_gap_max_ms >= REPORT_FRAME_GAP_MS"
        assert BY_ID["video.fps"].requires == "video.fps >= CAPTURE_FPS"


MOVED: list[tuple[str, str, Any]] = [
    ("voice.stability", "elevenlabs.stability", 0.4),
    ("voice.similarity_boost", "elevenlabs.similarity_boost", 0.6),
    ("voice.style", "elevenlabs.style", 0.2),
    ("voice.speaker_boost", "elevenlabs.speaker_boost", False),
    ("voice.dollars_per_1000_characters", "elevenlabs.dollars_per_1000_characters", 0.3),
    ("narration.model", "elevenlabs.model", "eleven_flash_v2_5"),
    ("audio.duck_ramp_seconds", "mix.duck_ramp_seconds", 0.25),
    ("audio.ambience_ramp_seconds", "mix.ambience_ramp_seconds", 2.0),
    ("audio.ambience_pad_seconds", "mix.ambience_pad_seconds", 1.0),
    ("audio.marker_mute_ramp_seconds", "mix.marker_mute_ramp_seconds", 0.08),
    ("audio.marker_boost_ramp_seconds", "mix.marker_boost_ramp_seconds", 0.6),
    ("video.audio_bitrate", "audio.bitrate", "256k"),
    ("video.sample_rate", "audio.sample_rate", 44100),
    ("video.channels", "audio.channels", 1),
    ("mix.loudness.target_lufs", "audio.target_lufs", -14.0),
    ("mix.loudness.true_peak_max_dbtp", "audio.true_peak_max_dbtp", -2.0),
    ("mix.loudness.range_max_lu", "audio.range_max_lu", 9.0),
    ("elevenlabs.timeout_seconds", "score.timeout_seconds", 300),
    ("elevenlabs.music_model", "score.music.model", "music_v3"),
    ("elevenlabs.music_bitrate", "score.music.bitrate", "256k"),
    ("elevenlabs.max_music_chunk_seconds", "score.music.chunk_max_seconds", 120),
    ("elevenlabs.music_crossfade_seconds", "score.music.crossfade_seconds", 4),
    ("elevenlabs.ambience_seconds", "score.ambience.duration_seconds", 30.0),
    ("elevenlabs.ambience_prompt_influence", "score.ambience.prompt_influence", 0.4),
    ("elevenlabs.effect_seconds", "score.effects.duration_seconds", 1.0),
    ("elevenlabs.effect_prompt_influence", "score.effects.prompt_influence", 0.6),
    ("score.music.seconds", "score.music.duration_seconds", 120),
]
"""Every key the regroup moved, as (where it was, where it is, a value other than its default)."""

GUESSED = {"elevenlabs.ambience_seconds", "elevenlabs.timeout_seconds", "narration.model"}
"""The old spellings whose words name several keys equally well, so the warning offers one of them.

There is no table of old names, because a key is read under its own name alone, so a warning finds
the key it offers from the words of the one it was given. `ambience_seconds` names the bed's length
and the mix's ambience ramps alike, `timeout_seconds` names three timeouts, and `model` names each
adapter's model and each sound's, so for these the warning is held to offering a key
rather than to offering the one that moved.
"""

CONTENT = {"score.music": {"prompt": "calm"}}
"""The content a shared table needs beside a setting before the document will read it."""


def tables_of(dotted: str, value: object) -> dict[str, Any]:
    """One dotted key as the nested tables a TOML file spells it in."""
    *tables, name = dotted.split(".")
    out: dict[str, Any] = {name: value}
    for table in reversed(tables):
        out = {table: out}
    return out


class TestTheRegroup:
    """Each provider owns its table, `[voice]` keeps three keys, and a moved key has no alias."""

    @pytest.mark.parametrize(("old", "new", "value"), MOVED, ids=[new for _old, new, _value in MOVED])
    def test_a_moved_key_is_read_from_its_new_table(self, old: str, new: str, value: object) -> None:
        del old
        assert value_of(load(machine={}, project=tables_of(new, value), environ={}).settings, new) == value

    @pytest.mark.parametrize(("old", "new", "value"), MOVED, ids=[old for old, _new, _value in MOVED])
    def test_the_old_spelling_is_not_read_and_warns_with_the_key_it_meant(
        self, tmp_path: Path, old: str, new: str, value: object
    ) -> None:
        """A tuning table warns through the settings loader and a shared one through the document, alike."""
        table, name = old.split(".", 1)
        while name.count(".") and f"{table}.{name.split('.', 1)[0]}" in {key.table for key in KEYS}:
            head, name = name.split(".", 1)
            table = f"{table}.{head}"
        written = merge_tables(tables_of(table, CONTENT.get(table, {})), tables_of(old, value))
        project = load_project(tmp_path, MINIMAL_TOML + "\n" + tomlkit.dumps(written), environ={})
        assert value_of(project.settings, new) == value_of(Settings(), new)
        meant = new.rsplit(".", 1)[-1] if new.rsplit(".", 1)[0] == table else new
        (said,) = [note for note in project.notes if f"'{name}'" in note]
        if old in GUESSED:
            assert said.startswith(f"decktalk.toml: [{table}]: ignoring unknown key '{name}' (did you mean '")
        else:
            assert said == f"decktalk.toml: [{table}]: ignoring unknown key '{name}' (did you mean '{meant}'?)."

    def test_the_sound_models_moved_beside_the_items_they_default(self) -> None:
        """One old key named the model of the ambience and of every effect, and each now has its own."""
        loaded = load(machine={}, project={"score": {"effects": {"model": "s2"}}}, environ={}).settings
        assert loaded.score.effects.model == "s2"
        assert loaded.score.ambience.model == Settings().score.ambience.model
        (said,) = key_warnings({"elevenlabs": {"sound_model": "s2"}}, "decktalk.toml")
        assert said.startswith("decktalk.toml: [elevenlabs]: ignoring unknown key 'sound_model'")

    @pytest.mark.parametrize(
        ("variable", "meant"),
        [
            ("DECKTALK_ELEVENLABS_MUSIC_MODEL", "DECKTALK_SCORE_MUSIC_MODEL"),
            ("DECKTALK_VOICE_STABILITY", "DECKTALK_ELEVENLABS_STABILITY"),
            ("DECKTALK_VIDEO_SAMPLE_RATE", "DECKTALK_AUDIO_SAMPLE_RATE"),
            ("DECKTALK_VIDEO_CRV", "DECKTALK_VIDEO_CRF"),
        ],
    )
    def test_an_old_variable_names_the_variable_of_the_key_that_moved(self, variable: str, meant: str) -> None:
        """The music model's old variable must never point at the speech model's, which re-voices every take."""
        (said,) = env_warnings({variable: "x"})
        assert said == f"environment: ignoring unknown key '{variable}' (did you mean '{meant}'?)."

    def test_the_old_speech_model_variable_offers_a_speech_model_and_never_the_musics(self) -> None:
        """`model` names each speech adapter's model, so either of them is a fair offer."""
        (said,) = env_warnings({"DECKTALK_NARRATION_MODEL": "x"})
        offered = said.split("did you mean '", 1)[1].split("'", 1)[0]
        assert offered in {BY_ID[key].environment for key in ("elevenlabs.model", "dtsp.model")}

    def test_a_voice_model_is_no_key_and_is_warned_about_as_unknown(self, tmp_path: Path) -> None:
        """A model is set in its provider's table alone, so one value is never stated under two keys."""
        project = load_project(tmp_path, MINIMAL_TOML + '\n[voice]\nmodel = "eleven_v3"\n', environ={})
        assert project.settings.elevenlabs.model == Settings().elevenlabs.model
        (said,) = [note for note in project.notes if "'model'" in note]
        assert said.startswith("decktalk.toml: [voice]: ignoring unknown key 'model'")

    def test_voice_holds_the_three_keys_every_voice_has(self) -> None:
        voice = sorted(key.name for key in KEYS if key.table == "voice")
        assert voice == ["id", "provider", "speed"]
