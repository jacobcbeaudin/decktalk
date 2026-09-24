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

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.results import Layer, Scope
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
    load,
    machine_config_path,
    read_machine_toml,
    route,
    scoped,
    unset,
    value_of,
    write,
)
from decktalk.tomlmap import Key, Nature, Source

SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "decktalk-1.json"


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


def edges(key_id: str) -> list[object]:
    """Every value worth judging at a key's edges, which is the sweep the panel asked for."""
    bounds = BY_ID[key_id].bounds
    if bounds is None:
        return []
    if bounds.enum is not None:
        return [bounds.enum[0], bounds.enum[-1], "a word no set holds"]
    step = 0.001
    out: list[object] = []
    if bounds.ge is not None:
        out += [bounds.ge, bounds.ge - step]
    if bounds.gt is not None:
        out += [bounds.gt + step, bounds.gt]
    if bounds.le is not None:
        out += [bounds.le, bounds.le + step]
    if bounds.lt is not None:
        out += [bounds.lt - step, bounds.lt]
    return out


SWEPT = [key.id for key in KEYS if key.bounds is not None and key.annotation is not tuple]
"""Every key whose range is a span or a set, which is every key the sweep can reach."""


@pytest.fixture(autouse=True)
def _no_machine_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the per-machine file at nothing, so the machine running the suite never sets a key."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path_factory.mktemp("machine") / "decktalk.toml"))


class TestThePublishedRangeIsTheEnforcedRange:
    """The one mechanism that keeps an agent's trust in a bound worth having."""

    @pytest.mark.parametrize("key_id", SWEPT)
    def test_the_loader_and_the_schema_judge_every_edge_alike(self, key_id: str) -> None:
        document = schema()
        for value in edges(key_id):
            if isinstance(value, str) and BY_ID[key_id].annotation is not str:
                continue
            assert loads(key_id, value) is accepts(document, key_id, value), f"{key_id} at {value!r}"

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
                assert key.source is Source.MEASURED or not key.decides, f"{key.id} decides a verdict per machine"

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

    def test_the_per_machine_file_has_a_place_on_every_platform(self) -> None:
        assert machine_config_path().name == "decktalk.toml"


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
        assert "--project" in caught.value.hint

    def test_a_machine_key_in_the_machine_file_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text('[tools]\nffmpeg = "/opt/ffmpeg"\n', encoding="utf-8")
        assert read_machine_toml(path) == {"tools": {"ffmpeg": "/opt/ffmpeg"}}

    def test_a_table_that_is_not_a_tuning_table_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text('[project]\nname = "x"\n', encoding="utf-8")
        with pytest.raises(InputError, match="not a tuning table"):
            read_machine_toml(path)

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
        written = write(path, "verify.cue_offset_max_ms", "120", scope=Scope.PROJECT)
        assert "# my project" in path.read_text(encoding="utf-8")
        assert "# how late" in path.read_text(encoding="utf-8")
        assert (written.previous, written.value, written.effective) == (100, 120.0, 120.0)
        assert written.layer is Layer.PROJECT
        assert written.shadowed is False
        assert written.line == 4

    def test_a_write_into_a_file_that_does_not_exist_yet_creates_the_table(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        write(path, "video.preset", "veryfast", scope=Scope.PROJECT)
        assert path.read_text(encoding="utf-8") == '[video]\npreset = "veryfast"\n'

    def test_a_dry_run_reports_the_change_and_writes_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        written = write(path, "video.preset", "veryfast", scope=Scope.PROJECT, dry_run=True)
        assert written.dry_run is True
        assert not path.exists()

    def test_a_value_the_loader_would_refuse_never_reaches_the_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("[verify]\ncue_offset_max_ms = 100\n", encoding="utf-8")
        with pytest.raises(InputError, match="must be between 0.001 and 1"):
            write(path, "verify.onset_rise_points", "20", scope=Scope.PROJECT)
        assert path.read_text(encoding="utf-8") == "[verify]\ncue_offset_max_ms = 100\n"

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            write(tmp_path / "decktalk.toml", "verify.cue_offset_maks_ms", "120", scope=Scope.PROJECT)

    def test_a_machine_key_written_to_the_project_file_names_the_other_flag(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="machine-scoped") as caught:
            write(tmp_path / "decktalk.toml", "tools.ffmpeg", "/opt/ffmpeg", scope=Scope.PROJECT)
        assert caught.value.hint is not None
        assert "--machine" in caught.value.hint

    def test_a_measured_key_is_refused_and_the_command_that_takes_it_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="measured rather than chosen") as caught:
            write(tmp_path / "machine.toml", "host.presentation_bias_ms", "5", scope=Scope.MACHINE)
        assert caught.value.hint == "Run `decktalk doctor --measure`."

    def test_a_write_a_higher_layer_shadows_says_so(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECKTALK_VIDEO_PRESET", "slow")
        written = write(tmp_path / "decktalk.toml", "video.preset", "veryfast", scope=Scope.PROJECT)
        assert written.shadowed is True
        assert written.layer is Layer.ENVIRONMENT
        assert written.effective == "slow"


class TestTheRemover:
    """A removal is the write's opposite, and it answers with the layer that shows through."""

    def test_a_removal_keeps_every_comment_and_reports_the_layer_below(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        path.write_text("# my project\n[verify]\n# how late\ncue_offset_max_ms = 100\n", encoding="utf-8")
        removed = unset(path, "verify.cue_offset_max_ms", scope=Scope.PROJECT)
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
        unset(path, "video.preset", scope=Scope.PROJECT)
        assert path.read_text(encoding="utf-8") == "# the encoder\n[video]\n"

    def test_a_key_the_file_never_stated_leaves_the_file_alone(self, tmp_path: Path) -> None:
        """The call is idempotent, because an agent that cannot read the file has to be able to call it twice."""
        path = tmp_path / "decktalk.toml"
        path.write_text('[video]\npreset = "veryfast"\n', encoding="utf-8")
        removed = unset(path, "video.crf", scope=Scope.PROJECT)
        assert removed.keys == ("video.crf",)
        assert removed.previous is None
        assert path.read_text(encoding="utf-8") == '[video]\npreset = "veryfast"\n'

    def test_a_removal_from_a_file_that_is_not_there_writes_no_file(self, tmp_path: Path) -> None:
        path = tmp_path / "decktalk.toml"
        assert unset(path, "video.crf", scope=Scope.PROJECT).previous is None
        assert not path.exists()

    def test_an_environment_variable_that_was_shadowing_the_file_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DECKTALK_VIDEO_PRESET", "slow")
        path = tmp_path / "decktalk.toml"
        path.write_text('[video]\npreset = "veryfast"\n', encoding="utf-8")
        removed = unset(path, "video.preset", scope=Scope.PROJECT)
        assert removed.layer is Layer.ENVIRONMENT
        assert removed.effective == "slow"

    def test_a_file_the_loader_would_refuse_whole_is_not_written_back(self, tmp_path: Path) -> None:
        """The removal goes through the loader the write goes through, so neither launders a bad file."""
        path = tmp_path / "machine.toml"
        path.write_text('[video]\npreset = "slow"\n[tools]\nffmpeg = "/opt/ffmpeg"\n', encoding="utf-8")
        with pytest.raises(InputError, match="project-scoped and does not belong in this file"):
            unset(path, "tools.ffmpeg", scope=Scope.MACHINE)
        assert "ffmpeg" in path.read_text(encoding="utf-8")

    def test_a_key_nobody_knows_is_refused_with_the_nearest_one(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="Did you mean 'verify.cue_offset_max_ms'"):
            unset(tmp_path / "decktalk.toml", "verify.cue_offset_maks_ms", scope=Scope.PROJECT)

    def test_a_machine_key_taken_out_of_the_project_file_names_the_other_flag(self, tmp_path: Path) -> None:
        with pytest.raises(InputError, match="machine-scoped") as caught:
            unset(tmp_path / "decktalk.toml", "tools.ffmpeg", scope=Scope.PROJECT)
        assert caught.value.hint is not None
        assert "--machine" in caught.value.hint

    def test_a_measured_key_may_be_taken_out_although_it_may_not_be_written(self, tmp_path: Path) -> None:
        path = tmp_path / "machine.toml"
        path.write_text("[host]\npresentation_bias_ms = 5.0\n", encoding="utf-8")
        removed = unset(path, "host.presentation_bias_ms", scope=Scope.MACHINE)
        assert removed.keys == ("host.presentation_bias_ms",)
        assert removed.layer is Layer.DEFAULT


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
        assert value_of(settings, "mix.loudness.target_lufs") == settings.mix.loudness.target_lufs

    def test_the_findings_a_key_decides_are_real_codes(self) -> None:
        for key in KEYS:
            for code in key.decides:
                assert isinstance(code, Code)
