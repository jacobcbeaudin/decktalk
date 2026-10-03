"""The knob surface: how the five layers stack, what each refuses, and what every key publishes.

The test that earns its keep is the boundary sweep. It builds the same value five ways at each edge
of every key and asserts that the loader and a JSON Schema validator reach the same verdict, which
is the only mechanism that keeps the published range and the enforced range one range. Everything
else here holds a rule the design states in a sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import tomlkit
from hypothesis import given, settings
from hypothesis import strategies as st

from decktalk.errors import ErrorCode, InputError
from decktalk.findings import Code
from decktalk.results import Layer, Nature, Scope, Source
from decktalk.settings import (
    BY_ID,
    DOCUMENT_TABLES,
    KEYS,
    NUMBERS,
    NUMBERS_BY_ID,
    SHARED_TABLES,
    STANDALONE_ENV,
    Number,
    Settings,
    env_warnings,
    key_warnings,
    load,
    machine_config_path,
    merge_tables,
    read_machine_toml,
    refuse_off_scope,
    route,
    scoped,
    unset,
    value_of,
    write,
)
from decktalk.tomlmap import Bounds, Key
from support.links import link
from support.projects import MINIMAL_TOML, load_project

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "v1" / "decktalk.json"


def schema() -> dict[str, Any]:
    """The committed whole-file schema, which is what the loader is held equal to."""
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def subschema(document: dict[str, Any], key_id: str) -> dict[str, Any]:
    """One key's own subschema, found by walking the tables of its dotted id."""
    body = document["properties"]
    for part in key_id.split("."):
        body = body[part] if "properties" not in body else body["properties"][part]
        body = body.get("properties", body)
    return body


def accepts(document: dict[str, Any], key_id: str, value: object) -> bool:
    """Whether the published schema admits this value for this key."""
    try:
        jsonschema.validate(value, subschema(document, key_id))
    except jsonschema.ValidationError:
        return False
    return True


def loads(key_id: str, value: object) -> bool:
    """Whether the loader admits this value for this key, written in the file the key belongs in."""
    table: dict[str, Any] = {}
    holder = table
    parts = key_id.split(".")
    for part in parts[:-1]:
        holder = holder.setdefault(part, {})
    holder[parts[-1]] = value
    machine = BY_ID[key_id].scope is Scope.MACHINE
    try:
        load(machine=table if machine else {}, project={} if machine else table, environ={})
    except InputError:
        return False
    return True


def values(key_id: str) -> st.SearchStrategy[object]:
    """Every value worth judging for a key, as its own type or as a TOML array of that type."""
    key = BY_ID[key_id]
    assert key.bounds is not None
    if key.bounds.items is not None:
        return st.lists(around(key.bounds.items), max_size=3)
    return around(key.bounds, words=key.annotation is str)


def around(bounds: Bounds, *, words: bool = False) -> st.SearchStrategy[object]:
    """A range's edges exactly, and numbers of either type on each side of every edge.

    A float key is also handed integers and an integer key floats, because TOML writes both, and
    JSON Schema counts a float with no fraction such as `320.0` as an integer, as the loader does.
    """
    if bounds.pattern is not None:
        # A value that matches, the same value with a filter or a rule after it, and any text at all.
        matching = st.from_regex(bounds.pattern, fullmatch=True)
        return matching | matching.map(lambda value: f"{value}:s=1x1,movie=/etc/passwd") | st.text()
    if bounds.enum is not None:
        members = st.sampled_from(bounds.enum)
        if words:
            return members | st.text()
        return members | st.integers(min_value=min(bounds.enum) - 2, max_value=max(bounds.enum) + 2)
    edges = [edge for edge in (bounds.ge, bounds.gt, bounds.le) if edge is not None]
    return st.sampled_from(edges).flatmap(
        lambda edge: (
            st.just(edge)
            | st.floats(min_value=edge - 1, max_value=edge + 1)
            | st.integers(min_value=int(edge) - 2, max_value=int(edge) + 2)
        )
    )


SWEEP_EXAMPLES = 40
"""How many values each key is judged at, which reaches both sides of every edge of every key."""

SWEPT = [key.id for key in KEYS if key.bounds is not None]
"""Every key with a range, whether a span, a set or the range of each item of an array."""


class TestThePublishedRangeIsTheEnforcedRange:
    """The one mechanism that keeps an agent's trust in a bound worth having."""

    @pytest.mark.parametrize("key_id", SWEPT)
    @settings(max_examples=SWEEP_EXAMPLES)
    @given(data=st.data())
    def test_the_loader_and_the_schema_judge_every_edge_alike(self, key_id: str, data: st.DataObject) -> None:
        value = data.draw(values(key_id))
        assert loads(key_id, value) is accepts(schema(), key_id, value), f"{key_id} at {value!r}"

    @pytest.mark.parametrize("key_id", SWEPT)
    def test_a_value_of_the_wrong_type_is_refused_by_both(self, key_id: str) -> None:
        wrong = "a string" if BY_ID[key_id].annotation is not str else 17
        assert loads(key_id, wrong) is False
        assert accepts(schema(), key_id, wrong) is False

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_default_validates_against_its_own_subschema(self, key: Key) -> None:
        value = list(key.default) if isinstance(key.default, tuple) else key.default
        assert accepts(schema(), key.id, value), f"{key.id} default {value!r} is outside its published range"

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_default_is_inside_its_own_safe_range(self, key: Key) -> None:
        assert key.bounds is None or key.bounds.holds(key.default)


class TestTheRecordEveryKeyCarries:
    """A key that cannot be read whole is a key an agent cannot turn with confidence."""

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_key_says_what_it_changes_in_a_whole_sentence(self, key: Key) -> None:
        assert key.description.endswith(".")
        assert ";" not in key.description
        assert "—" not in key.description
        assert len(key.description.split()) > 3

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_hazard_is_a_whole_sentence_with_no_dash(self, key: Key) -> None:
        assert key.hazard is None or (key.hazard.endswith(".") and "—" not in key.hazard)

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_see_also_and_every_requires_resolves(self, key: Key) -> None:
        for name in key.see_also:
            assert name in BY_ID or name in NUMBERS_BY_ID, f"{key.id} sees {name}, which is neither"
        if key.requires is not None:
            left, _op, right = key.requires.split()
            for side in (left, right):
                assert side in BY_ID or side in NUMBERS_BY_ID or side.replace(".", "").isdigit()

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_a_key_that_is_not_chosen_says_what_produces_it(self, key: Key) -> None:
        assert key.source is Source.CHOSEN or key.evidence is not None

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_no_key_is_a_truth_or_a_derived_number(self, key: Key) -> None:
        assert key.nature in (Nature.TASTE, Nature.APPARATUS)

    def test_every_key_name_ends_in_its_own_unit_where_it_has_one(self) -> None:
        units = {"ms": "milliseconds", "seconds": "seconds", "dbfs": "dBFS", "percent": "percent", "luma": "luma"}
        for key in KEYS:
            last = key.name.rsplit("_", 1)[-1]
            if last in units:
                assert key.unit is not None, f"{key.id} names a unit it does not publish"

    def test_no_verdict_limit_is_machine_scoped(self) -> None:
        for key in KEYS:
            if key.scope is Scope.MACHINE:
                assert not key.decides, f"{key.id} decides a verdict per machine"

    def test_the_key_a_diagnostic_names_is_the_key_config_set_takes(self) -> None:
        for key in KEYS:
            assert BY_ID[key.id] is key
            assert key.environment.startswith("DECKTALK_")


class TestTheFiveLayers:
    """Which value wins, and the record that says which one did."""

    def test_each_layer_overrides_the_one_below_it(self) -> None:
        here = load(
            machine={"tools": {"ffmpeg": "/m"}},
            project={"video": {"preset": "veryfast", "crf": 20}},
            environ={"DECKTALK_VIDEO_CRF": "23"},
            overrides=("video.preset=slow",),
        )
        assert here.settings.tools.ffmpeg == "/m"
        assert here.settings.video.crf == 23
        assert here.settings.video.preset == "slow"
        assert here.settings.video.output_fps == 25

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
            load(machine={}, project={}, environ={"DECKTALK_VIDEO_OUTPUT_FPS": "thirty"})

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
            ("darwin", {}, "home/Library/Application Support/decktalk/decktalk.toml"),
            ("linux", {}, "home/.config/decktalk/decktalk.toml"),
            ("linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/decktalk/decktalk.toml"),
            ("win32", {"APPDATA": "/roaming"}, "/roaming/decktalk/decktalk.toml"),
            ("win32", {}, "home/AppData/Roaming/decktalk/decktalk.toml"),
            ("linux", {"DECKTALK_CONFIG": "/named.toml"}, "/named.toml"),
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
        monkeypatch.setenv("DECKTALK_CONFIG", str(planted))
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
        assert "--where project" in caught.value.hint

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
        assert "--where machine" in caught.value.hint

    def test_the_browser_path_in_a_project_file_is_refused_at_its_line(self, tmp_path: Path) -> None:
        (tmp_path / "decktalk.toml").write_text(
            '[project]\nname = "x"\n\n[record]\nbrowser_path = "/tmp/not-a-browser"\n', encoding="utf-8"
        )
        with pytest.raises(InputError, match="record.browser_path") as caught:
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

    def test_a_project_key_in_a_project_is_read(self) -> None:
        refuse_off_scope({"verify": {"cue_offset_max_ms": 400}}, Scope.PROJECT, file=Path("decktalk.toml"))

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
        assert BY_ID["video.output_fps"].requires == "video.output_fps >= CAPTURE_FPS"


class TestTheNumbersThatAreNotKnobs:
    """Every derived expression and every constant is published, so a missing knob is explained."""

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
        assert "--where machine" in caught.value.hint

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
        assert "--where machine" in caught.value.hint


class TestTheTables:
    """The table list is the vocabulary, so a name that belongs to two things is caught here."""

    def test_the_document_tables_and_the_tuning_tables_do_not_collide(self) -> None:
        tuning = {key.table.split(".")[0] for key in KEYS}
        assert not tuning & set(DOCUMENT_TABLES)

    def test_every_shared_table_is_a_table_some_key_sits_in_or_under(self) -> None:
        tables = {key.table for key in KEYS}
        for shared in SHARED_TABLES:
            assert shared in tables or any(table.startswith(f"{shared}.") for table in tables)

    def test_no_key_name_still_says_sfx(self) -> None:
        assert not [key.id for key in KEYS if "sfx" in key.id]

    def test_the_value_of_a_dotted_key_is_the_value_the_tree_holds(self) -> None:
        settings = Settings()
        assert value_of(settings, "score.music.model") == settings.score.music.model

    def test_the_findings_a_key_decides_are_real_codes(self) -> None:
        for key in KEYS:
            for code in key.decides:
                assert isinstance(code, Code)


MOVED: list[tuple[str, str, Any]] = [
    ("voice.stability", "elevenlabs.stability", 0.4),
    ("voice.similarity_boost", "elevenlabs.similarity_boost", 0.6),
    ("voice.style", "elevenlabs.style", 0.2),
    ("voice.speaker_boost", "elevenlabs.speaker_boost", False),
    ("voice.price_per_1000_characters", "elevenlabs.price_per_1000_characters", 0.3),
    ("narration.model", "voice.model", "eleven_flash_v2_5"),
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
    ("elevenlabs.max_music_chunk_seconds", "score.music.max_chunk_seconds", 120),
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
and the mix's ambience ramps alike, `timeout_seconds` names three timeouts, and `model` names the
voice's model and each adapter's default model, so for these the warning is held to offering a key
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
    """Each provider owns its table, `[voice]` keeps four keys, and a moved key has no alias."""

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
        """`model` names the voice's model and each adapter's default, so any of them is a fair offer."""
        (said,) = env_warnings({"DECKTALK_NARRATION_MODEL": "x"})
        offered = said.split("did you mean '", 1)[1].split("'", 1)[0]
        assert offered in {BY_ID[key].environment for key in ("voice.model", "elevenlabs.model", "dtsp.model")}

    def test_voice_holds_the_four_keys_every_voice_has(self) -> None:
        voice = sorted(key.name for key in KEYS if key.table == "voice")
        assert voice == ["id", "model", "provider", "speed"]
