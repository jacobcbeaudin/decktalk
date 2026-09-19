"""Unit tests that need neither ffmpeg nor Chromium nor an API key."""

from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

import decktalk
from decktalk import ConfigError, Project, load_settings
from decktalk.artifacts import (
    CueTime,
    CueTimes,
    Take,
    Takes,
    Timeline,
    TimelineSection,
    Word,
)
from decktalk.cli import build_parser, main
from decktalk.model.script import parse_script, strip_markdown
from decktalk.settings import Settings
from decktalk.stages.assemble import timeline_targets
from decktalk.tomlmap import RENAMES_PAGE
from decktalk.verdicts import Findings, SkipReason, Verdict

MINIMAL_TOML = """
[project]
name = "t"

[[section]]
number = 0
clip = "media/open.mp4"

[[section]]
number = 1
page = "deck/index.html"
scene = 1

[[section]]
number = 2
page = "deck/index.html"
hold_seconds = 1.5
"""


def write_project(tmp_path: Path, toml: str = MINIMAL_TOML) -> Path:
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    return tmp_path


# ---- config ------------------------------------------------------------------------------


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


# ---- project -----------------------------------------------------------------------------


def test_project_loads_sections_in_order(tmp_path):
    p = Project.load(write_project(tmp_path), environ={})
    assert [s.number for s in p.sections] == [0, 1, 2]
    assert p.clip_numbers == {0}
    assert p.page_sections[0].scene == "1"
    assert p.page_sections[1].scene == "2"  # defaults to the section number
    assert p.name == "t" and p.final == tmp_path / "build" / "out" / "t.mp4"


@pytest.mark.parametrize(
    "toml, message",
    [
        ("[[section]]\nnumber = 1\n", "needs 'page'"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\nclip = 'b.mp4'\n", "either 'clip' or 'page'"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 1\npage = 'a.html'\n", "duplicate"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\nlead_seconds = -1\n", "'lead_seconds' must be 0 or more, got -1"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\nhold_seconds = -0.5\n", "'hold_seconds' must be 0 or more"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\nrecord_margin_seconds = 'lots'\n", "must be"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\n[transition]\ndips = [[1, 9]]\n", "does not exist"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\n[bogus]\nx = 1\n", "unknown table"),
        (
            "[[section]]\nnumber = 1\npage = 'a.html'\n[[mix.sfx]]\nfile = 'x.mp3'\nsection = 1\n",
            "missing required key 'cue'",
        ),
    ],
)
def test_project_validation_messages(tmp_path, toml, message):
    with pytest.raises(ConfigError, match=re.escape(message) if message.endswith(".") else message):
        Project.load(write_project(tmp_path, toml), environ={})


def test_project_allows_clips_at_both_edges(tmp_path):
    toml = (
        "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\npage = 'a.html'\n[[section]]\nnumber = 9\nclip = 'close.mp4'\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    assert p.clip_numbers == {0, 9} and [s.number for s in p.page_sections] == [1, 2]


def test_project_allows_clips_between_page_sections(tmp_path):
    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\nclip = 'broll.mp4'\n"
        "[[section]]\nnumber = 3\nclip = 'more.mp4'\n[[section]]\nnumber = 4\npage = 'a.html'\n"
        "hold_seconds = 1\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    assert [s.number for s in p.sections] == [1, 2, 3, 4]
    assert p.clip_numbers == {2, 3} and [s.number for s in p.page_sections] == [1, 4]


def test_project_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="decktalk init"):
        Project.load(tmp_path, environ={})


def test_project_tuning_tables_reach_settings(tmp_path):
    p = Project.load(write_project(tmp_path, MINIMAL_TOML + "\n[video]\npreset = 'ultrafast'\n"), environ={})
    assert p.settings.video.preset == "ultrafast"


def test_project_env_reads_dotenv_and_ignores_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    root = write_project(tmp_path)
    (root / ".env").write_text("ELEVENLABS_API_KEY=<fill me>\nELEVENLABS_VOICE_ID='abc' # comment\n", encoding="utf-8")
    p = Project.load(root, environ={})
    assert not p.env.get("ELEVENLABS_API_KEY")  # the placeholder counts as unset
    assert p.env.get("ELEVENLABS_VOICE_ID").reveal() == "abc"
    # A secret is named by its variable and never by its value, in a repr as in an error.
    assert repr(p.env.get("ELEVENLABS_VOICE_ID")) == "<secret ELEVENLABS_VOICE_ID>"
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY") as info:
        p.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
    assert "abc" not in str(info.value)


def test_project_warns_about_unknown_keys_and_suggests_the_closest(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    toml = (
        "[project]\nname = 't'\nscirpt = 'script.md'\n"
        "[voice]\nstabilty = 0.4\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\nscnee = 2\nslate_seconds = 3\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nzebra = 1\n"
        "[mix.loudness]\nrange_luu = 9\n"
        "[soundscape.music]\nprompt = 'calm'\nsecond = 60\n"
        "[video]\npresett = 'veryfast'\n"
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        p = Project.load(write_project(tmp_path, toml), environ={})
    page = f". {RENAMES_PAGE} lists every name DeckTalk renamed."
    assert [r.getMessage() for r in caplog.records] == [
        f"decktalk.toml: [project]: ignoring unknown key 'scirpt' (did you mean 'script'?){page}",
        f"decktalk.toml: [[section]] number=1: ignoring unknown key 'scnee' (did you mean 'scene'?){page}",
        "decktalk.toml: [[section]] number=1: ignoring 'slate_seconds', which applies only to a clip section",
        f"decktalk.toml: [[section]] number=2: ignoring unknown key 'zebra'{page}",
        f"decktalk.toml: [voice]: ignoring unknown key 'stabilty' (did you mean 'stability'?){page}",
        f"decktalk.toml: [mix.loudness]: ignoring unknown key 'range_luu' (did you mean 'range_lu'?){page}",
        f"decktalk.toml: [soundscape.music]: ignoring unknown key 'second' (did you mean 'seconds'?){page}",
        f"decktalk.toml: [video]: ignoring unknown key 'presett' (did you mean 'preset'?){page}",
    ]
    # A warning, not an error: the load succeeds and every misspelled key keeps its default.
    assert p.voice.stability == 0.55 and p.settings.video.preset == "medium" and p.soundscape.music.seconds == 360


def test_a_tuning_value_outside_its_range_fails_at_load_naming_its_table_and_key(tmp_path):
    """A number that would divide by zero or break an encoder is a config error, not a traceback."""
    for table, key, value, must in [
        ("video", "fps", 0, "must be above zero"),
        ("video", "crf", -9, "must be an x264 quality between 0 and 51"),
        ("video", "width", -4, "must be above zero"),
        ("record", "retries", -1, "must not be negative"),
        ("verify", "min_changed_percent", 140.0, "must be a percentage between 0 and 100"),
        ("record", "black_ymax", 900.0, "must be a luma between 0 and 255"),
    ]:
        with pytest.raises(ConfigError) as info:
            load_settings(toml={table: {key: value}}, environ={}, user={})
        assert str(info.value) == f"[{table}] {key}: {must}, got {value!r}"


def test_a_tuning_value_of_the_wrong_type_names_its_table_and_key(tmp_path):
    """The located error is what lets a CLI fill the error slot's path and hint."""
    with pytest.raises(ConfigError) as info:
        load_settings(toml={"video": {"fps": "high"}}, environ={}, user={})
    assert str(info.value).startswith("[video] fps: expected int, got 'high'")


def test_an_environment_variable_obeys_the_same_range_as_the_table(tmp_path):
    """Every layer that sets a key goes through the same reader, so every layer is checked."""
    with pytest.raises(ConfigError) as info:
        load_settings(toml={}, environ={"DECKTALK_VIDEO_FPS": "0"}, user={})
    assert str(info.value) == "[video] fps: must be above zero, got 0"


def test_the_tuning_tables_are_frozen_so_one_run_never_retunes_another(tmp_path):
    """A flag rebuilds the table it overrides, which is why the dataclasses hold still."""
    settings = load_settings(toml={}, environ={}, user={})
    with pytest.raises(FrozenInstanceError):
        settings.video.preset = "veryfast"
    faster = replace(settings, video=replace(settings.video, preset="veryfast"))
    assert faster.video.preset == "veryfast" and settings.video.preset == "medium"


def test_every_tuning_key_carries_the_sentence_the_reference_prints(tmp_path):
    """The generated page is read from the fields, so a field with no sentence is a blank row."""
    for table in fields(Settings):
        cls = fields(getattr(Settings(), table.name))
        for f in cls:
            assert f.metadata.get("doc"), f"{table.name}.{f.name}"
            assert f.metadata["doc"].endswith("."), f"{table.name}.{f.name}"


def test_a_table_reads_every_key_its_dataclass_declares(tmp_path, caplog):
    """Each key is written once, as a field, so a table's reader and its class cannot drift apart."""
    toml = (
        "[project]\nname = 't'\nscript = 'script.md'\ncues = 'cues.json'\nbuild = 'build'\nlanguage = 'fr'\n"
        "[voice]\nprovider = 'elevenlabs'\nmodel = 'm'\nstability = 0.5\nsimilarity_boost = 0.7\n"
        "style = 0.1\nspeaker_boost = true\nspeed = 1.0\nprice_per_1000_characters = 0.3\n"
        "[transition]\ndips = [[1, 2]]\ndip_seconds = 0.2\npage_fades_in = true\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\npage = 'b.html'\n"
        "[mix]\nmusic_db = -20\n"
        "[mix.loudness]\ntarget_lufs = -16\ntrue_peak_db = -1.5\nrange_lu = 9\n"
        "[[mix.sfx]]\nfile = 'a.wav'\nsection = 1\ncue = '1.1'\ndb = -16\noffset = 0.1\ncaption = 'a chime'\n"
        "[soundscape.sfx.tap]\ntext = 'a tap'\nout = 'tap.mp3'\nduration_seconds = 0.5\n"
        "prompt_influence = 0.4\nmodel_id = 'sound'\n"
        "[soundscape.music]\nprompt = 'calm'\nseconds = 60\nforce_instrumental = true\nout = 'm.mp3'\n"
        "model_id = 'music'\n"
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        p = Project.load(write_project(tmp_path, toml), environ={})
    assert [r.getMessage() for r in caplog.records] == []
    assert p.document.language == "fr"
    assert p.voice.price_per_1000_characters == 0.3
    assert p.mix.sfx[0].caption == "a chime"


def test_the_markers_file_is_parsed_into_rows_and_a_bad_one_names_its_file(tmp_path, caplog):
    """The music answers to these rows, so a malformed file fails at load with the row named."""
    from decktalk.model.markers import load_markers

    path = tmp_path / "markers.json"
    path.write_text(
        json.dumps(
            {
                "boost_db": 4,
                "boost_seconds": 1.5,
                "markers": [
                    {"name": "turn", "section": 3, "on": "$start", "mute_seconds": 0.4},
                    {"name": "land", "section": 4, "on": "seal", "offset": 0.2, "occurrence": 2, "zebra": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        markers = load_markers(path)
    assert (markers.boost_db, markers.boost_seconds) == (4.0, 1.5)
    assert [(m.name, m.section, m.key, m.on, m.offset, m.occurrence) for m in markers.markers] == [
        ("turn", 3, "03", "$start", 0.0, 1),
        ("land", 4, "04", "seal", 0.2, 2),
    ]
    assert "ignoring unknown key 'zebra'" in caplog.text

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected an object with 'markers'"):
        load_markers(path)

    path.write_text(json.dumps({"markers": [{"name": "turn"}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required key 'section'"):
        load_markers(path)

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=str(path)):
        load_markers(path)


def test_a_section_with_no_chapter_is_titled_by_its_script_heading(tmp_path):
    """The author already wrote a heading, so the mp4's chapter carries it rather than a number."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\npage = 'b.html'\nchapter = 'Its own'\n"
    )
    root = write_project(tmp_path, toml)
    (root / "script.md").write_text("## 1. What a cue is\n\nOne.\n\n## 2. The edit\n\nTwo.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    assert p.chapters() == {1: "What a cue is", 2: "Its own"}
    (root / "script.md").unlink()
    assert Project.load(root, environ={}).chapters() == {1: "Section 1", 2: "Its own"}


def test_user_settings_file_warns_about_unknown_keys(tmp_path, caplog):
    from decktalk.settings import read_user_toml

    path = tmp_path / "decktalk.toml"
    path.write_text("[record]\nsettle_second = 0.8\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="decktalk"):
        assert read_user_toml(path) == {"record": {"settle_second": 0.8}}
    assert [r.getMessage() for r in caplog.records] == [
        f"{path}: [record]: ignoring unknown key 'settle_second' (did you mean 'settle_seconds'?). "
        f"{RENAMES_PAGE} lists every name DeckTalk renamed."
    ]


def test_scaffold_loads_without_warnings_and_has_nine_sections(tmp_path, monkeypatch, caplog):
    from decktalk.scaffold import init

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = init(tmp_path / "proj", name="proj")
    caplog.clear()
    with caplog.at_level("WARNING", logger="decktalk"):
        p = Project.load(root, environ={})
    assert [r.getMessage() for r in caplog.records] == []
    # Seven page sections. A page keeps its scene numbers, so sections 8 and 9 play scenes 7 and 5.
    pages = [(1, "1"), (2, "2"), (3, "3"), (4, "4"), (6, "6"), (8, "7"), (9, "5")]
    assert [(s.number, s.scene) for s in p.page_sections] == pages
    # Two clip sections that ship no file. Both are optional, so a fresh scaffold passes `build --strict` on
    # their slates, and each names the words file its clip's captions will read.
    clips = [(s.number, s.clip, s.words, s.optional) for s in p.clip_sections]
    assert clips == [
        (5, "media/edit-before.mov", "media/edit-before.words.json", True),
        (7, "media/edit-after.mov", "media/edit-after.words.json", True),
    ]
    # Sections 4 to 8 head "The edit" in the script and set no `chapter`, so the default groups them
    # into one chapter with no repeated line in decktalk.toml.
    chapters = ["Open", "How it works", "How AI learns", *["The edit"] * 5, "Close"]
    assert [s.chapter for s in p.sections] == ["Open", "How it works", "How AI learns", "", "", "", "", "", "Close"]
    assert list(p.chapters().values()) == chapters


def test_strict_fails_on_a_missing_clip_unless_the_section_is_optional(tmp_path, monkeypatch, caplog):
    import importlib

    from decktalk.errors import MissingInputError

    asm = importlib.import_module("decktalk.stages.assemble")
    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\nclip = 'media/real.mp4'\n"
        "[[section]]\nnumber = 3\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    real, slot = p.clip_sections
    assert (real.optional, slot.optional) == (False, True)
    ran = []
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: ran.append(args))
    monkeypatch.setattr(asm, "section_slate", lambda project, sec: None)
    enc = asm.Encoder(p.settings.video)
    out = tmp_path / "out.mp4"

    with pytest.raises(MissingInputError) as err:
        asm._render_clip(p, enc, real, out, (False, False), 0.0, strict=True)
    assert str(err.value) == (
        f"section 2: clip missing: {p.root / 'media' / 'real.mp4'}. Put your clip at that path, "
        "or set optional = true on the section to play its slate under --strict."
    )
    assert ran == []

    with caplog.at_level("WARNING", logger="decktalk"):
        assert asm._render_clip(p, enc, slot, out, (False, False), 0.0, strict=True) == ("slate", None)
        assert asm._render_clip(p, enc, real, out, (False, False), 0.0, strict=False) == ("slate", None)
    assert len(ran) == 2
    assert [r.getMessage() for r in caplog.records] == [
        "section 03: media/slot.mp4 missing; slate for 4s (drop your clip at that path; the section is optional, "
        "so --strict allows the slate)",
        "section 02: media/real.mp4 missing; slate for 5s (drop your clip at that path)",
    ]


# ---- artifacts -----------------------------------------------------------------------------


def test_takes_roundtrip_sorts_and_totals(tmp_path):
    m = Takes(script="s.md", model="m", output_format="mp3")
    m.sections["02"] = Take(2, "B", "02-b.mp3", "02-b.words.json", "h", 3, 1.0, 2.5)
    m.sections["01"] = Take(1, "A", "01-a.mp3", "01-a.words.json", "h", 3, 1.0, 1.5)
    path = tmp_path / "takes.json"
    m.save(path)
    back = Takes.load(path)
    assert back is not None and list(back.sections) == ["01", "02"] and back.total_seconds == 4.0
    assert not list(tmp_path.glob(".*.tmp"))  # atomic write left nothing behind


def test_timeline_and_cue_times_roundtrip(tmp_path):
    tl = Timeline(
        narration="n.mp3",
        total_seconds=3.0,
        sections={"01": TimelineSection("A", 0, 3.0, 3.0, 2.8, [Word("hi", 0.1, 0.4)])},
    )
    tl.save(tmp_path / "t.json")
    back = Timeline.load(tmp_path / "t.json")
    assert back is not None and back.span("01") == 3.0 and back.sections["01"].words[0].word == "hi"
    rows = [CueTime("a", "hi", 1.5, 1.2), CueTime("panel:bought", "$end", 2.0)]
    b = CueTimes({"01": rows}, estimated=True)
    b.save(tmp_path / "b.json")
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {
        "estimated": True,
        "sections": {"01": [
            {"cue": "a", "on": "hi", "at": 1.5, "word_at": 1.2},
            {"cue": "panel:bought", "on": "$end", "at": 2.0, "word_at": None},
        ]},
    }  # fmt: skip
    back = CueTimes.load(tmp_path / "b.json")
    assert back.estimated and back.get("01", "panel:bought") == 2.0 and back.word_at("01", "a") == 1.2
    assert back.query("01") == "a@1.5,panel:bought@2.0" and back.times("01") == {"a": 1.5, "panel:bought": 2.0}
    assert back.get("01", "nope") is None and back.query("99") is None


# ---- narrate -------------------------------------------------------------------------------

SCRIPT = """# Title

## 0. On camera

[clip]

Hi there.

## 1. Open — 0:00 to 0:20

[Deck. Title.]

Welcome to the **deck**. It has `code` and a [link](http://x).

[Deck. Next.]
Second paragraph, with [NUMBER] placeholder.

## Notes

not a section
"""


def test_parse_script_sections_and_directions():
    segs = parse_script(SCRIPT)
    assert [s.index for s in segs] == [0, 1]
    one = segs[1]
    assert one.title == "Open" and one.target_seconds == 20 and one.placeholders == ["NUMBER"]
    assert "**" not in one.text and "`" not in one.text and "http" not in one.text
    assert "<break" not in one.text and one.text.count(" —") == 1  # a direction between paragraphs becomes one beat
    assert one.word_count == 15


def test_strip_markdown_keeps_placeholders():
    assert "[VENUE]" in strip_markdown("At [VENUE] tonight. [not spoken]")


# ---- assemble ---------------------------------------------------------------------------------


def test_timeline_targets_are_frame_exact():
    tl = Timeline(
        narration="n",
        total_seconds=2.5,
        sections={"01": TimelineSection("a", 0, 1.02, 1.02, None), "02": TimelineSection("b", 1.02, 2.5, 1.48, None)},
    )
    t = timeline_targets(tl, 30)
    assert abs(t["01"] - 1.0333) < 1e-3 and abs(t["02"] - 1.4667) < 1e-3


def test_fade_flags_follow_dips_and_page_fade_in(tmp_path):
    p = Project.load(
        write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = [[0, 1]]\npage_fades_in = true\n"), environ={}
    )
    flags = p.document.fade_flags
    assert flags["00"] == (False, True)  # clip fades out into the dip
    assert flags["01"] == (False, False)  # page fades itself in, with no dip after
    assert flags["02"] == (False, False)
    p2 = Project.load(write_project(tmp_path, MINIMAL_TOML), environ={})  # no dips key: every cut dips
    assert p2.document.fade_flags["01"] == (False, True)
    # The assemble log names what the cuts do rather than always saying "straight cuts".
    assert p.document.cut_summary == "dips at 1 cut"
    assert p2.document.cut_summary == "dips at every cut"
    p3 = Project.load(write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = []\n"), environ={})
    assert p3.document.cut_summary == "straight cuts"


# ---- package and cli -----------------------------------------------------------------------


def test_package_exports_every_public_name():
    """__all__ is the whole supported API: every command the CLI runs, and the type each one returns."""
    for name in decktalk.__all__:
        assert hasattr(decktalk, name), name
    assert decktalk.__all__ == sorted(decktalk.__all__)
    for name in ("Project", "Voice", "Word", "SpeechProvider", "register_speech_provider", "__version__"):
        assert name in decktalk.__all__
    actions = build_parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
    # The four commands that are not an operation on a loaded project: three act on a machine or a
    # directory, and `serve` runs a web server until it is stopped, so none of them returns a result.
    project_commands = set(actions.choices) - {"init", "install", "doctor", "serve"}  # type: ignore[attr-defined]
    assert project_commands <= set(decktalk.__all__), sorted(project_commands - set(decktalk.__all__))
    # One word names the command, the call, the type it returns and its --json key, so the result
    # class of every command is exported beside its function.
    for command in sorted(project_commands):
        result = f"{command.title().replace('_', '')}Result"
        assert result in decktalk.__all__, result


def test_every_result_tallies_its_own_rows():
    """The CLI's exit code is this arithmetic, so each result is measured against rows it holds."""
    from decktalk.stages.assemble import AssembleResult
    from decktalk.stages.build import BuildResult
    from decktalk.stages.clip import ClipResult, WordsResult
    from decktalk.stages.screenshots import ScreenshotsResult
    from decktalk.stages.verify import CueCheck, StartCheck, VerifyResult

    # verify: one certain start, one uncertain cue, one passing cue.
    starts = [StartCheck(key="01", start=0.0, probe_at=0.2, yavg=2.0, ymax=10.0, ok=False)]
    cues = [
        CueCheck("1:1.1", 1.0, 1.0, 0.2, 0.2, True, verdict=Verdict.THIN_CHANGE),
        CueCheck("1:1.2", 2.0, 2.0, 9.0, 1.0, True, verdict=Verdict.CHANGED),
    ]
    verification = VerifyResult(total_seconds=9.0, starts=starts, cues=cues)
    assert [s.verdict for s in starts] == [Verdict.BLACK]
    assert verification.findings == Findings(certain=1, uncertain=1)

    # The results that judge a count rather than a verdict.
    assembly = AssembleResult(
        final=Path("f.mp4"),
        stamped=None,
        duration=9.0,
        sections=[],
        warnings=[],
        loudness=None,
        loudness_problems=["quiet", "loud"],
    )
    assert assembly.findings == Findings(uncertain=2)
    clip_row = ClipResult(
        section=1,
        video=Path("c.mp4"),
        words_file=Path("c.json"),
        start=0.0,
        end=1.0,
        first_frame=0,
        last_frame=24,
        hold_seconds=0.0,
        duration=1.0,
        gain_db=0.0,
        estimated=False,
        cut_words=["step"],
    )
    assert clip_row.findings == Findings(uncertain=1)
    # The results that judge nothing say so, which is what keeps the CLI free of stage arithmetic.
    for quiet in (WordsResult(sections=[]), ScreenshotsResult(files=[])):
        assert quiet.findings == Findings(), type(quiet).__name__

    # build adds what its stages found, and a stage that did not run adds nothing.
    assert BuildResult().findings == Findings()
    whole = BuildResult(assembly=assembly, verification=verification)
    assert whole.findings == assembly.findings + verification.findings == Findings(certain=1, uncertain=3)


def test_the_public_api_carries_no_name_the_contract_retired():
    """`Timeline` leaves the public names, and the module that reads the file stays where it is."""
    for name in ("Timeline", "TimelineSection", "LeadMeasurement", "RecordingCheck", "measure", "check"):
        assert name not in decktalk.__all__, name
    from decktalk.artifacts import Timeline  # still readable, and not part of the supported API

    assert Timeline.load(Path("nowhere.json")) is None


def test_cli_verbose_and_quiet_parse_on_either_side_of_the_command():
    parser = build_parser()
    for argv in (["-v", "status"], ["status", "-v"], ["-p", "d", "status", "-v"], ["status", "-v", "-p", "d"]):
        args = parser.parse_args(argv)
        assert getattr(args, "verbose", False) is True, argv
        assert getattr(args, "quiet", False) is False, argv
    args = parser.parse_args(["init", "d", "-q"])
    assert args.quiet is True and args.dir == "d"
    args = parser.parse_args(["build", "-p", "d", "--only", "1", "--only", "2"])
    assert args.project == "d" and args.only == [1, 2]


def test_cli_missing_project_is_a_clean_error(tmp_path, capsys):
    assert main(["status", "-v", "-p", str(tmp_path / "nowhere")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err


def test_cli_unexpected_exception_is_reported_and_reraised_with_verbose(tmp_path, capsys, monkeypatch):
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("decktalk.cli.cmd_doctor", boom)
    assert main(["doctor"]) == 1
    assert capsys.readouterr().err == "error: RuntimeError: kaboom (add -v for the traceback)\n"
    with pytest.raises(RuntimeError, match="kaboom"):
        main(["doctor", "-v"])


# ---- scaffold lesson: pauses, cue keys, KaTeX vendoring, runtime warnings ----------------------
# Appended for the lesson-shaped scaffold. Everything above this line belongs to earlier work.


def test_pause_direction_yields_a_timed_break():
    from decktalk.model.script import BREAK_RE

    seg = parse_script("## 1. A\n\nThink about it.\n\n[pause 3]\n\nOnly two x is left. [beat] Done.")[0]
    assert BREAK_RE.findall(seg.text) == ["3"]  # only a timed pause becomes a break tag
    assert seg.text.count(" —") == 1  # the beat is a dash
    assert 'Think about it. <break time="3s" />' in seg.text
    assert seg.spoken == "Think about it. Only two x is left. Done."
    # The silent placeholder honours the declared pauses, so the pause lengthens the section.
    short = parse_script("## 1. A\n\nThink about it.\n\nOnly two x is left. Done.")[0]
    cfg = Settings().narration
    assert abs((seg.silent_seconds(cfg) - short.silent_seconds(cfg)) - 3.7) < 1e-6


def test_pause_direction_accepts_decimals_and_case():
    seg = parse_script("## 1. A\n\nOne. [Pause 1.5] Two.")[0]
    assert '<break time="1.5s" />' in seg.text
    assert seg.spoken == "One. Two."


def test_cues_load_with_cue_keys_and_reject_any_other_id_key(tmp_path):

    root = write_project(tmp_path)
    (root / "cues.json").write_text(
        json.dumps({"sections": {"1": {"cues": [{"cue": "1.1a", "on": "$start"}, {"cue": "1.1b", "on": "hello"}]}}}),
        encoding="utf-8",
    )
    project = Project.load(root, environ={})
    (section,) = project.cue_specs()
    assert [c.cue for c in section.cues] == ["1.1a", "1.1b"]
    assert [c.on for c in section.cues] == ["$start", "hello"]
    # A cue row that names the id under any other key fails with the normal missing-key error.
    (root / "cues.json").write_text(
        json.dumps({"sections": {"1": {"cues": [{"id": "1.1a", "on": "$start"}]}}}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="missing required key 'cue'"):
        Project.load(root, environ={}).cue_specs()


def test_init_copies_every_file_of_the_template_deck(tmp_path, monkeypatch):
    """Every page and asset under the template's deck/ arrives, with placeholders filled only in HTML."""
    from decktalk.scaffold import init
    from decktalk.toolchain.assets import RUNTIME_FILE, package_file, runtime_path

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    src = package_file("template/deck")
    wanted = {f.relative_to(src) for f in src.rglob("*") if f.is_file() and not f.name.startswith(".")}
    assert Path("index.html") in wanted
    got = {f.relative_to(root / "deck") for f in (root / "deck").rglob("*") if f.is_file()}
    assert wanted <= got
    assert (root / "deck" / RUNTIME_FILE).read_bytes() == runtime_path().read_bytes()
    # Beside the template's own files arrive the runtime and the packaged KaTeX, and nothing else.
    assert {p if p.parts[0] != "katex" else Path("katex") for p in got - wanted} == {
        Path("decktalk-runtime.js"),
        Path("katex"),
    }
    for rel in wanted:
        if rel.suffix == ".html":
            html = (root / "deck" / rel).read_text(encoding="utf-8")
            assert "__NAME__" not in html, rel
        else:
            assert (root / "deck" / rel).read_bytes() == (src / rel).read_bytes(), rel
    assert not (root / "deck" / "vendor").exists()


def test_template_ids_agree_across_page_cues_and_script(tmp_path, monkeypatch):
    """Every cue id in cues.json is named in the page, every data-cue is cued, and every phrase is spoken."""
    from decktalk.model.cues import find_phrase
    from decktalk.scaffold import init
    from decktalk.stages.align import uncued_elements, unknown_cue_ids

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    project = Project.load(root, environ={})
    specs = project.cue_specs()
    assert unknown_cue_ids(project, specs) == []
    assert uncued_elements(project, specs) == []
    segments = {s.index: s for s in parse_script((root / "script.md").read_text(encoding="utf-8"))}
    for section in specs:
        words = [Word(w, i, i + 1) for i, w in enumerate(segments[section.number].spoken.split())]
        for cue in section.cues:
            if not cue.on.startswith("$"):
                assert find_phrase(words, cue.on) is not None, (
                    f"cue {cue.id}: {cue.on!r} is not in section {section.number}"
                )


# ---- post-production: dips, captions, chapters, the mix plan, loudness, verify offsets ----------
#
# Added with the post-production review fixes. Nothing here needs ffmpeg, Chromium or a build.


def test_frame_dip_quantizes_to_whole_frames():
    from decktalk.model.document import frame_dip

    assert frame_dip(0.15, 25) == 0.16  # 3.75 frames rounds up to 4
    assert frame_dip(0.15, 30) == 0.1333  # 4.5 frames rounds to the even 4
    assert frame_dip(0.001, 25) == 0.04  # never shorter than one frame
    assert frame_dip(0.0, 25) == 0.0


def _spoken(text: str, start: float = 0.0, step: float = 0.4, gap_after: str | None = None) -> list[Word]:
    words: list[Word] = []
    t = start
    for w in text.split():
        words.append(Word(w, round(t, 3), round(t + 0.3, 3)))
        t += step
        if gap_after is not None and w == gap_after:
            t += 3.0
    return words


def _film(*words: tuple[str, float, float]) -> list[Word]:
    return [Word(w, start, end) for w, start, end in words]


def test_caption_cues_group_whole_sentences_and_keep_lines_short():
    from decktalk.captions import CAPTION_MAX_CHARS, caption_cues

    text = (
        "Welcome. This is a narrated deck, cut to the word. Every visual you see lands on the word "
        "that names it, and nothing drifts. Short."
    )
    cues = caption_cues(_spoken(text))
    for cue in cues:
        assert 1 <= len(cue.lines) <= 2
        assert all(len(line) <= CAPTION_MAX_CHARS for line in cue.lines)
    # The one-word sentences join a neighbour, and a cue of two sentences breaks its line between them.
    assert [c.lines for c in cues] == [
        ("Welcome.", "This is a narrated deck, cut to the word."),
        ("Every visual you see lands on the word", "that names it, and nothing drifts. Short."),
    ]
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start  # a cue never overlaps the next one
    assert cues[0].end == cues[1].start  # the tail stops at the next cue
    assert cues[-1].end == round(cues[-1].end, 3) == 10.1  # last word end 9.9 plus the 0.2 s tail


def test_caption_cues_do_not_strand_step_at_a_line_start():
    """The demo film's words. A break here would strand "step." at the start of a line after "for every"."""
    from decktalk.captions import caption_cues

    words = _film(
        ("For", 127.312, 127.463), ("a", 127.51, 127.533), ("big", 127.58, 127.742), ("model,", 127.8, 128.125),
        ("the", 128.148, 128.218), ("chips", 128.264, 128.532), ("do", 128.59, 128.706),
        ("billions", 128.822, 129.298), ("of", 129.344, 129.402), ("billions", 129.46, 129.89),
        ("of", 129.936, 130.006), ("small", 130.076, 130.378), ("calculations", 130.447, 131.271),
        ("for", 131.341, 131.469), ("every", 131.55, 131.806), ("step.", 131.875, 132.398),
    )  # fmt: skip
    # Before: "For a big model, the chips do billions of" / "billions of small calculations for every", then "step." ...
    cues = caption_cues(words)
    assert [(c.start, c.end, c.lines) for c in cues] == [
        (127.312, 128.822, ("For a big model, the chips do",)),
        (128.822, 132.598, ("billions of billions of small", "calculations for every step.")),
    ]


def test_caption_cues_do_not_strand_of_chips_on_a_cue_of_its_own():
    """The demo film's words. A break here would leave "of chips." as a cue of its own."""
    from decktalk.captions import caption_cues

    words = _film(
        ("The", 133.164, 133.268), ("large", 133.338, 133.593), ("language", 133.652, 134.035),
        ("models", 134.081, 134.395), ("behind", 134.464, 134.859), ("AI", 134.94, 135.254),
        ("chat", 135.3, 135.567), ("apps", 135.625, 135.857), ("split", 135.974, 136.322),
        ("the", 136.368, 136.438), ("work", 136.484, 136.705), ("across", 136.751, 137.088),
        ("thousands", 137.158, 137.622), ("of", 137.657, 137.715), ("chips.", 137.773, 138.504),
    )  # fmt: skip
    # Before: "The large language models behind AI chat" / "apps split the work across thousands", then "of chips."
    cues = caption_cues(words)
    assert [(c.start, c.end, c.lines) for c in cues] == [
        (133.164, 135.974, ("The large language models", "behind AI chat apps")),
        (135.974, 138.704, ("split the work across thousands of chips.",)),
    ]


def test_caption_cues_edge_cases():
    from decktalk.captions import CAPTION_MAX_CHARS, caption_cues

    # A one-word sentence joins a neighbour, and alone in its section it is a cue of its own.
    cues = caption_cues(_spoken("Update your video the way you update a doc. DeckTalk. Open source, and free."))
    assert [c.lines for c in cues] == [
        ("Update your video the way", "you update a doc."),
        ("DeckTalk. Open source, and free.",),
    ]
    assert [c.lines for c in caption_cues(_spoken("Hello."))] == [("Hello.",)]

    # A sentence too long for two lines splits at the clause mark, then away from function words.
    cues = caption_cues(
        _spoken("In a big model, testing knobs one at a time would take billions of tries for every step.")
    )
    assert [c.lines for c in cues] == [
        ("In a big model,", "testing knobs one at a time"),
        ("would take billions", "of tries for every step."),
    ]

    # Words with no punctuation at all still wrap into lines that fit, with no one-word line or cue.
    cues = caption_cues(_spoken(" ".join(["knob"] * 40)))
    assert all(len(line) <= CAPTION_MAX_CHARS and " " in line for c in cues for line in c.lines)
    assert sum(len(line.split()) for c in cues for line in c.lines) == 40

    # A silence of 1.0 s ends the cue, even inside a sentence. A shorter one does not.
    joined = _film(("It", 0.0, 0.3), ("guesses,", 0.4, 0.7), ("and", 1.6, 1.8), ("waits.", 1.9, 2.3))
    assert [c.text for c in caption_cues(joined)] == ["It guesses, and waits."]
    split = _film(("It", 0.0, 0.3), ("guesses,", 0.4, 0.7), ("and", 1.7, 1.9), ("waits.", 2.0, 2.4))
    assert [(c.start, c.end, c.text) for c in caption_cues(split)] == [
        (0.0, 0.9, "It guesses,"),
        (1.7, 2.6, "and waits."),
    ]


def test_caption_cues_split_on_a_long_pause_and_never_cross_sections():
    from decktalk.captions import caption_cues
    from decktalk.stages.assemble import build_captions

    cues = caption_cues(_spoken("one two three four five six", gap_after="three"))
    assert len(cues) == 2 and cues[0].text == "one two three" and cues[1].text == "four five six"
    tl = Timeline(
        narration="n",
        total_seconds=4.0,
        sections={
            "01": TimelineSection("a", 0, 2.0, 2.0, None, _spoken("alpha beta", 0.1)),
            "02": TimelineSection("b", 2.0, 4.0, 2.0, None, _spoken("gamma delta", 2.1)),
        },
    )
    shifted = build_captions(tl, 3.0)  # narration starts three seconds into the final file
    assert [c.text for c in shifted] == ["alpha beta", "gamma delta"]
    assert shifted[0].start == 3.1 and shifted[1].start == 5.1
    assert caption_cues([]) == []


def test_caption_and_chapter_files(tmp_path):
    from decktalk.captions import CaptionCue, Chapter, ffmetadata_escape, write_chapters, write_srt, write_vtt

    cues = [CaptionCue(3.7, 10.5, ("Welcome.", "This is a deck.")), CaptionCue(3661.25, 3662.0, ("Late.",))]
    write_srt(tmp_path / "c.srt", cues)
    write_vtt(tmp_path / "c.vtt", cues)
    srt = (tmp_path / "c.srt").read_text(encoding="utf-8")
    vtt = (tmp_path / "c.vtt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:03,700 --> 00:00:10,500\nWelcome.\nThis is a deck.\n\n2\n01:01:01,250 --> ")
    assert vtt.startswith("WEBVTT\n\n00:00:03.700 --> 00:00:10.500\nWelcome.\nThis is a deck.\n")
    assert ffmetadata_escape("a=b;c#d\\e") == "a\\=b\\;c\\#d\\\\e"
    write_chapters(tmp_path / "ch.txt", [Chapter(0, 3.0, "On camera"), Chapter(3.0, 12.44, "Open; part = 1")])
    text = (tmp_path / "ch.txt").read_text(encoding="utf-8")
    assert text.startswith(";FFMETADATA1\n")
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=On camera\n" in text
    assert "START=3000\nEND=12440\ntitle=Open\\; part \\= 1\n" in text


def test_plan_mix_delays_clip_audio_and_drops_the_limiter(tmp_path):
    from decktalk.stages.assemble import RenderedSection, build_chapters, mix_input_args, plan_mix

    p = Project.load(write_project(tmp_path), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=4.0,
        sections={
            "01": TimelineSection("Open", 0, 2.0, 2.0, 1.8),
            "02": TimelineSection("Close", 2.0, 4.0, 2.0, 3.8),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "00.mp4", 3.0, "clip", audio=tmp_path / "open.mp4"),
        RenderedSection(p.sections[1], tmp_path / "01.mp4", 2.0, "page"),
        RenderedSection(p.sections[2], tmp_path / "02.mp4", 3.5, "page"),
    ]
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert plan.total == 8.5
    assert plan.inputs[0] == ("lavfi", "anullsrc=r=48000:cl=stereo")
    assert mix_input_args(plan)[:5] == ["-f", "lavfi", "-t", "8.500", "-i"]
    assert "alimiter" not in plan.filter
    assert "adelay=3000:all=1[narr]" in plan.filter  # narration starts with the first page section
    assert "atrim=duration=3.000" in plan.filter and "afade=t=in:d=0.02,afade=t=out:st=2.980:d=0.02" in plan.filter
    assert "adelay=0:all=1[clip00]" in plan.filter
    assert plan.filter.endswith("[anchor][narr][clip00]amix=inputs=3:duration=first:normalize=0[a]")
    chapters = build_chapters(rows, p.chapters())
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0.0, 3.0, "Section 0"),
        (3.0, 5.0, "Section 1"),
        (5.0, 8.5, "Section 2"),
    ]
    paths = p.workspace.output_paths()
    assert paths["srt"].name == "t.srt" and paths["vtt"].name == "t.vtt" and paths["chapters"].name == "t.chapters.txt"


MID_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
clip = "media/broll.mp4"

[[section]]
number = 3
page = "deck/index.html"

[[section]]
number = 4
page = "deck/index.html"
"""


def _mid_clip_plan(tmp_path):
    """Pages 1, 3 and 4 around a 3-second clip at 2, with the rows and timeline assemble would build."""
    from decktalk.stages.assemble import RenderedSection

    p = Project.load(write_project(tmp_path, MID_CLIP_TOML), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 2.0, 2.0, 1.6, _spoken("alpha beta", 0.7)),
            "03": TimelineSection("C", 2.0, 4.5, 2.5, 4.1, _spoken("gamma delta", 2.1)),
            "04": TimelineSection("D", 4.5, 6.0, 1.5, 5.8, _spoken("epsilon", 4.6)),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "page"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 3.0, "clip", audio=tmp_path / "broll.mp4"),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.52, "page"),
        RenderedSection(p.sections[3], tmp_path / "04.mp4", 1.48, "page"),
    ]
    return p, tl, rows


def test_narration_runs_pause_for_a_clip_between_page_sections(tmp_path):
    from decktalk.stages.assemble import NarrationRun, narration_offsets, narration_runs, section_starts

    p, tl, rows = _mid_clip_plan(tmp_path)
    starts = section_starts(rows)
    assert starts == {"01": 0.0, "02": 2.0, "03": 5.0, "04": 7.52}
    assert narration_runs(rows, tl, starts) == [
        NarrationRun(keys=("01",), at=0.0, start=0.0, end=2.0),
        NarrationRun(keys=("03", "04"), at=5.0, start=2.0, end=None),
    ]
    # Every section after the clip hears its words one clip later than the track holds them.
    assert narration_offsets(rows, tl, starts) == {"01": 0.0, "03": 3.0, "04": 3.0}
    # Without a clip between page sections there is one run, and every section shares its offset.
    edge = [rows[1], rows[0], rows[2], rows[3]]
    edge_starts = section_starts(edge)
    assert len(narration_runs(edge, tl, edge_starts)) == 1
    assert narration_offsets(edge, tl, edge_starts) == {"01": 3.0, "03": 3.0, "04": 3.0}


def test_plan_mix_places_each_narration_run_at_its_section_start(tmp_path):
    from decktalk.stages.assemble import plan_mix

    p, tl, rows = _mid_clip_plan(tmp_path)
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert plan.total == 9.0
    narration = str(p.narration_dir / "narration.mp3")
    assert [path for mode, path in plan.inputs] == [
        "anullsrc=r=48000:cl=stereo",
        narration,
        narration,
        str(rows[1].audio),
    ]
    assert "atrim=start=0.000:end=2.000,asetpts=PTS-STARTPTS,adelay=0:all=1[narr0]" in plan.filter
    assert "atrim=start=2.000,asetpts=PTS-STARTPTS,adelay=5000:all=1[narr1]" in plan.filter
    assert "adelay=2000:all=1[clip02]" in plan.filter
    assert plan.filter.endswith("[anchor][narr0][narr1][clip02]amix=inputs=4:duration=first:normalize=0[a]")
    # The music ducks under each section where it plays, and under the clip.
    music = tmp_path / "music.mp3"
    music.write_bytes(b"x")
    p.document = replace(p.document, mix=type(p.mix)(music=str(music)))
    ducked = plan_mix(p, rows, tl, soundscape=True).filter
    for a, b in [(0.0, 1.6), (5.0, 7.1), (7.5, 8.8), (2.0, 5.0)]:
        assert f"(t-{a:.3f})" in ducked and f"({b:.3f}-t)" in ducked, (a, b)


def test_captions_and_chapters_skip_over_a_clip_between_page_sections(tmp_path):
    from decktalk.stages.assemble import build_captions, build_chapters, narration_offsets, section_starts

    p, tl, rows = _mid_clip_plan(tmp_path)
    cues = build_captions(tl, narration_offsets(rows, tl, section_starts(rows)))
    assert [(c.text, c.start) for c in cues] == [("alpha beta", 0.7), ("gamma delta", 5.1), ("epsilon", 7.6)]
    assert all(c.end <= 2.0 or c.start >= 5.0 for c in cues)  # nothing is captioned over the clip
    assert cues[0].end <= 2.0
    chapters = build_chapters(rows, p.chapters())
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0.0, 2.0, "Section 1"),
        (2.0, 5.0, "Section 2"),
        (5.0, 7.52, "Section 3"),
        (7.52, 9.0, "Section 4"),
    ]


def test_loudness_problems_report_peaks_and_missed_targets(tmp_path):
    from decktalk.media.audio import Loudness
    from decktalk.stages.assemble import loudness_problems

    p = Project.load(write_project(tmp_path), environ={})
    assert loudness_problems(p, Loudness(i=-16.4, tp=-1.6, lra=5, thresh=-27, offset=0)) == []
    over = loudness_problems(p, Loudness(i=-25.2, tp=-1.0, lra=5, thresh=-27, offset=0))
    assert len(over) == 2 and "true peak -1.0 dBTP" in over[0] and "9.2 LU" in over[1]


def test_video_defaults_match_the_recorder():
    v = Settings().video
    assert v.fps == 25 and v.sample_rate == 48000
    assert not hasattr(Settings().audio, "limiter")


# ---- captions keep the script's punctuation -------------------------------------------------


def test_display_words_restores_punctuation_and_case():
    from decktalk.artifacts import Word
    from decktalk.captions import display_words

    words = [Word("welcome", 0, 1), Word("this", 1, 2), Word("is", 2, 3), Word("two", 3, 4), Word("x", 4, 5)]
    text = "Welcome. This is two x."
    out = display_words(words, text)
    assert [w.word for w in out] == ["Welcome.", "This", "is", "two", "x."]
    assert out[0].start == 0 and out[-1].end == 5
    # An alignment that cannot be made returns the words untouched.
    assert [w.word for w in display_words(words, "completely different text here")] == [w.word for w in words]


# ---- per-machine settings file ------------------------------------------------------------------


def test_user_settings_sit_between_defaults_and_the_project(tmp_path, monkeypatch):
    from decktalk.settings import load_settings, read_user_toml, user_config_path

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


# ---- page errors -------------------------------------------------------------------------------


def test_page_error_text_keeps_the_message_and_the_file_and_line():
    from decktalk.media.browser import page_error_text

    class Err:
        name = "SyntaxError"
        message = "Identifier 'SCENES_TOTAL' has already been declared"
        stack = (
            "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared\n    at file:///p/deck/index.html:120:7"
        )

    assert page_error_text(Err()) == "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared (index.html:120)"

    class Bare:
        message = "boom"
        stack = ""

    assert page_error_text(Bare()) == "boom"


# ---- Tier 1 pipeline: clip placement, silent loudness, verify defaults, unknown cue ids ------------

PAGES_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"

[[section]]
number = 3
page = "deck/index.html"
"""


def test_assemble_skips_loudness_on_an_estimated_timeline(tmp_path, monkeypatch, caplog):
    import importlib

    # decktalk.stages exports a function named assemble, so the module is imported by its full name.
    asm = importlib.import_module("decktalk.stages.assemble")

    root = write_project(tmp_path, PAGES_TOML)
    (root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=2.0,
        sections={"01": TimelineSection("A", 0, 2.0, 2.0, 1.0, [Word("Hi", 0.7, 1.0)])},
        estimated=True,
    )
    tl.save(p.timeline_path)
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    rows = [asm.RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 2.0, "page")]

    def write_last(*args):
        Path(args[-1]).write_bytes(b"x")

    def no_loudness(*args):
        raise AssertionError("a build without voice must not be normalized")

    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: rows)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm, "normalize_loudness", no_loudness)
    monkeypatch.setattr(asm.ffmpeg, "run", write_last)
    monkeypatch.setattr(asm.ffmpeg, "probe_duration", lambda path: 2.0)
    with caplog.at_level("INFO", logger="decktalk.stages.assemble"):
        result = asm.assemble(p, soundscape=False)
    assert result.loudness is None and result.warnings == []
    assert any(
        r.getMessage() == "[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, "
        "and the clicks stay at -24 dBFS for the a/v check"
        for r in caplog.records
    )


def test_seamless_parses_on_any_section_but_the_first(tmp_path, caplog):
    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nseamless = true\n"
        "[[section]]\nnumber = 3\npage = 'a.html'\nseamless = true\n"
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        p = Project.load(write_project(tmp_path, toml), environ={})
    assert [s.seamless for s in p.sections] == [False, True, True] and not caplog.records
    first = toml.replace("number = 1\npage = 'a.html'\n", "number = 1\npage = 'a.html'\nseamless = true\n")
    with pytest.raises(ConfigError, match="number=1: seamless is set on the first section"):
        Project.load(write_project(tmp_path, first), environ={})
    with pytest.raises(ConfigError, match="'seamless' must be bool"):
        Project.load(write_project(tmp_path, toml.replace("seamless = true", "seamless = 1")), environ={})


def test_build_captions_uses_the_take_index_spoken_text(tmp_path):
    from decktalk.stages.assemble import build_captions, caption_texts

    root = write_project(tmp_path, PAGES_TOML)
    p = Project.load(root, environ={})
    words = [Word("hello", 0.5, 0.9), Word("there", 1.0, 1.4)]
    tl = Timeline(narration="n.mp3", total_seconds=2.0, sections={"01": TimelineSection("A", 0, 2.0, 2.0, 1.4, words)})
    m = Takes(script="script.md", model="m", output_format="mp3")
    m.sections["01"] = Take(1, "A", "01-a.mp3", "01-a.words.json", "h", 2, 1.0, 2.0, spoken="Hello, there.")
    m.save(p.takes_path)
    # No script.md exists, so the text can only come from the take_index.
    assert caption_texts(p, tl) == {"01": "Hello, there."}
    assert [c.text for c in build_captions(tl, 0.0, caption_texts(p, tl))] == ["Hello, there."]
    # A take_index written before the field existed falls back to the script.
    raw = json.loads(p.takes_path.read_text(encoding="utf-8"))
    del raw["sections"]["01"]["spoken"]
    p.takes_path.write_text(json.dumps(raw), encoding="utf-8")
    (root / "script.md").write_text(
        "## 1. A\n\nHello there!\n\n## 2. B\n\nTwo.\n\n## 3. C\n\nThree.\n", encoding="utf-8"
    )
    assert caption_texts(p, tl)["01"] == "Hello there!"


def test_provider_errors_never_show_the_voice_id(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from decktalk.errors import ProviderError
    from decktalk.speech import http as _http

    voice_id = "Xb7hH8MSUJpSbSDYk0k2"
    api_key = "sk_test_key_that_must_not_print"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128"
    message = f"A voice with voice_id {voice_id} was not found."
    body = json.dumps({"detail": {"status": "voice_not_found", "message": message}})

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, io.BytesIO(body.encode()))  # type: ignore[arg-type]

    monkeypatch.setattr(_http, "urlopen", refuse)
    with pytest.raises(ProviderError) as info:
        _http.post_json(url, {"text": "hi"}, {"xi-api-key": api_key}, timeout=1)
    message = str(info.value)
    assert voice_id not in message and api_key not in message
    assert "https://api.elevenlabs.io/v1/text-to-speech/<voice id>/with-timestamps" in message
    assert "HTTP 404" in message and "voice_not_found" in message

    def unreachable(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(_http, "urlopen", unreachable)
    with pytest.raises(ProviderError) as info:
        _http.get_json(f"https://api.elevenlabs.io/v1/voices/{voice_id}", {"xi-api-key": api_key}, timeout=1)
    assert voice_id not in str(info.value) and api_key not in str(info.value)
    assert "voices/<voice id>" in str(info.value)


TITLED_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
chapter = "Open"
page = "deck/index.html"

[[section]]
number = 2
chapter = "The edit"
clip = "media/before.mov"
words = "media/before.words.json"

[[section]]
number = 3
chapter = "The edit"
page = "deck/index.html"

[[section]]
number = 4
chapter = "Close"
page = "deck/index.html"
"""


def _titled_clip_rows(tmp_path, *, clip_audio: bool = True):
    from decktalk.stages.assemble import RenderedSection

    p = Project.load(write_project(tmp_path, TITLED_CLIP_TOML), environ={})
    audio = tmp_path / "media" / "before.mov" if clip_audio else None
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "page"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 3.0, "clip", audio=audio),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.52, "page"),
        RenderedSection(p.sections[3], tmp_path / "04.mp4", 1.48, "page"),
    ]
    return p, rows


def test_a_clip_section_reads_its_words_key(tmp_path):
    p, _rows = _titled_clip_rows(tmp_path)
    assert p.sections[1].is_clip and p.sections[1].words == "media/before.words.json"
    plain = Project.load(write_project(tmp_path, MID_CLIP_TOML), environ={})
    assert plain.sections[1].words is None


def test_clip_captions_place_the_clip_speech_at_the_clip_start(tmp_path, caplog):
    from decktalk.artifacts import write_words
    from decktalk.stages.assemble import clip_captions

    p, rows = _titled_clip_rows(tmp_path)
    (tmp_path / "media").mkdir()
    words = [Word("Watch", 0.2, 0.5), Word("it.", 0.6, 0.9), Word("One.", 1.4, 1.7), Word("Late.", 3.1, 3.4)]
    write_words(tmp_path / "media" / "before.words.json", words)
    cues = clip_captions(p, rows)
    assert [(c.text, c.start) for c in cues] == [("Watch it. One.", 2.2)]  # the word after the picture ends is dropped
    assert cues[0].end <= 5.0
    # A slate plays no speech, so it gets no captions.
    _p, slate_rows = _titled_clip_rows(tmp_path, clip_audio=False)
    assert clip_captions(p, slate_rows) == []
    # A missing words file warns and captions nothing.
    (tmp_path / "media" / "before.words.json").unlink()
    assert clip_captions(p, rows) == []
    assert "words file missing" in caplog.text


def test_consecutive_sections_with_the_same_title_share_one_chapter(tmp_path):
    from decktalk.stages.assemble import build_chapters

    p, rows = _titled_clip_rows(tmp_path)
    assert [(c.start, c.end, c.title) for c in build_chapters(rows, p.chapters())] == [
        (0.0, 2.0, "Open"),
        (2.0, 7.52, "The edit"),
        (7.52, 9.0, "Close"),
    ]


# ---- narration tail -------------------------------------------------------------------------


def test_trailing_silence_counts_a_silence_ending_0_0502_s_before_the_end(monkeypatch):
    """The numbers of the demo's 08-the-edit.mp3: every narrate run padded it again by 1.35 s."""
    from decktalk.media import audio, ffmpeg

    detect = (
        "  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, mono, fltp, 128 kb/s\n"
        "[silencedetect @ 0x1] silence_start: 13.914717\n"
        "[silencedetect @ 0x1] silence_end: 13.974331 | silence_duration: 0.0596145\n"
        "[silencedetect @ 0x1] silence_start: 14.380816\n"
        "[silencedetect @ 0x1] silence_end: {end} | silence_duration: 1.320998\n"
    )
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 15.752)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.701814))
    assert audio.trailing_silence(Path("08-the-edit.mp3")) == 1.371
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.5))  # speech after the silence
    assert audio.trailing_silence(Path("08-the-edit.mp3")) == 0.0
    # At 8 kHz one mp3 frame lasts 0.144 s, longer than the fixed tolerance.
    low = detect.replace("44100 Hz", "8000 Hz").format(end=15.62)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: low)
    assert audio.trailing_silence(Path("08-the-edit.mp3")) == 1.371


def test_a_padded_take_within_a_frame_of_min_tail_is_not_padded_again(monkeypatch):
    from decktalk.media import audio
    from decktalk.settings import NarrationConfig
    from decktalk.stages.narrate import ensure_tail

    padded: list[float] = []
    monkeypatch.setattr(audio, "pad_tail", lambda path, seconds, bitrate: padded.append(seconds))
    monkeypatch.setattr(audio, "trailing_silence", lambda path: 1.26)
    cfg = NarrationConfig(min_tail_seconds=1.3)
    assert ensure_tail(Path("a.mp3"), cfg, tolerance=audio.SILENCE_END_TOLERANCE_SECONDS) == 0.0
    assert padded == []
    assert ensure_tail(Path("a.mp3"), cfg) == 0.09  # a take never padded before gets the full tail
    assert padded == [0.09]


# ---- section silence -----------------------------------------------------------------------


def _two_takes(tmp_path: Path, second: str = "") -> tuple[Project, Takes]:
    """Two page sections with a 3 s take each, words at 0.5 s and 1.0 s. `second` adds keys to section 2."""
    from decktalk.artifacts import write_words

    root = write_project(
        tmp_path, f"[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\npage = 'a.html'\n{second}"
    )
    (root / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 2. Close\n\nBye now.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    p.narration_dir.mkdir(parents=True)
    take_index = Takes(script="script.md", model="m", output_format="mp3_44100_128")
    for key, slug in (("01", "open"), ("02", "close")):
        write_words(p.narration_dir / f"{key}-{slug}.words.json", [Word("a", 0.5, 0.9), Word("b", 1.0, 1.6)])
        take_index.sections[key] = Take(
            index=int(key), chapter=slug.title(), file=f"{key}-{slug}.mp3", words_file=f"{key}-{slug}.words.json",
            hash="h", word_count=2, estimated_seconds=1.0, duration_seconds=3.0,
        )  # fmt: skip
    take_index.save(p.takes_path)
    return p, take_index


def test_section_lead_and_tail_keys_parse_on_page_sections_only(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(logging.getLogger("decktalk"), "propagate", True)
    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\nlead_seconds = 1.5\ntail_seconds = 2\n"
        "[[section]]\nnumber = 2\nclip = 'c.mp4'\nlead_seconds = 1\n"
    )
    with caplog.at_level(logging.WARNING, logger="decktalk"):
        p = Project.load(write_project(tmp_path, toml), environ={})
    page = p.page_sections[0]
    assert (page.lead_seconds, page.tail_seconds) == (1.5, 2.0)
    assert p.lead_seconds("01") == 1.5 and p.lead_seconds("02") == 0.0
    assert "ignoring 'lead_seconds', which applies only to a page section" in caplog.text
    assert Project.load(write_project(tmp_path, MINIMAL_TOML), environ={}).page_sections[0].tail_seconds is None


def test_a_hold_between_page_sections_pauses_the_narration(tmp_path):
    from decktalk.stages.assemble import (
        NarrationRun,
        RenderedSection,
        build_captions,
        narration_offsets,
        narration_runs,
        plan_mix,
        section_starts,
    )

    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\nhold_seconds = 2\n[[section]]\nnumber = 2\npage = 'a.html'\n"
        "[[section]]\nnumber = 3\npage = 'a.html'\nhold_seconds = 1\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 2.0, 2.0, 1.6, _spoken("alpha beta", 0.7)),
            "02": TimelineSection("B", 2.0, 4.5, 2.5, 4.1, _spoken("gamma delta", 2.1)),
            "03": TimelineSection("C", 4.5, 6.0, 1.5, 5.8, _spoken("epsilon", 4.6)),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 4.0, "page"),  # 2 s of narration and a 2 s hold
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 2.52, "page"),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.48, "page"),
    ]
    starts = section_starts(rows)
    assert narration_runs(rows, tl, starts) == [
        NarrationRun(keys=("01",), at=0.0, start=0.0, end=2.0),
        NarrationRun(keys=("02", "03"), at=4.0, start=2.0, end=None),
    ]
    offsets = narration_offsets(rows, tl, starts)
    assert offsets == {"01": 0.0, "02": 2.0, "03": 2.0}
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert "atrim=start=0.000:end=2.000,asetpts=PTS-STARTPTS,adelay=0:all=1[narr0]" in plan.filter
    assert "atrim=start=2.000,asetpts=PTS-STARTPTS,adelay=4000:all=1[narr1]" in plan.filter
    assert [(c.text, c.start) for c in build_captions(tl, offsets)] == [
        ("alpha beta", 0.7),
        ("gamma delta", 4.1),
        ("epsilon", 6.6),
    ]


# ---- the take plan and preflight ---------------------------------------------------------


def _planned_scaffold(tmp_path: Path, monkeypatch) -> tuple[Project, dict[str, str]]:
    """The scaffold half voiced by a provider that sends nothing: 01, 02 and 09 on disk, 03 stale, the rest new.

    Returns the project and the path of every file the plan must leave alone, with its bytes' hash.
    """
    import hashlib

    from decktalk.artifacts import write_words
    from decktalk.model.script import PUNCT
    from decktalk.scaffold import init
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import take_name, text_hash, words_name

    class PlanVoice:
        name = "plan-voice"

        def speak(self, request):
            raise AssertionError("a plan sent a request")

        def cache_key(self, request):
            return "plan-voice"

    register_speech_provider("plan-voice", lambda context: PlanVoice())
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    root = init(tmp_path / "proj", name="proj")
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("[voice]\n", "[voice]\nprovider = 'plan-voice'\n", 1))
    p = Project.load(root, environ={})
    cfg = p.settings.narration
    settings = p.voice.api_settings()
    spoken = {s.key: s for s in p.script_sections()[1]}
    p.narration_dir.mkdir(parents=True)
    take_index = Takes(script="script.md", model=cfg.model, output_format=cfg.output_format)
    for key, kind in (("01", "real"), ("02", "real"), ("03", "stale"), ("09", "real")):
        seg = spoken[key]
        digest = text_hash(seg, cfg, "plan-voice", settings) if kind == "real" else "0123456789abcdef"
        (p.narration_dir / take_name(digest)).write_bytes(f"take {key}".encode())
        tokens = [t.strip(PUNCT) for t in seg.spoken.split()]
        write_words(
            p.narration_dir / words_name(digest),
            [Word(t, round(i * 0.4, 3), round(i * 0.4 + 0.3, 3)) for i, t in enumerate(tokens)],
        )
        take_index.sections[key] = Take(
            index=int(key), chapter=seg.title, file=take_name(digest), words_file=words_name(digest),
            hash=digest, word_count=seg.word_count, estimated_seconds=seg.estimated_seconds(cfg),
            duration_seconds=len(tokens) * 0.4 + 1.3, spoken=seg.spoken,
        )  # fmt: skip
    # 03's take sits under a digest its text no longer makes, so a voiced run would voice it again.
    take_index.sections["03"].hash = "0123456789abcdef"
    take_index.save(p.takes_path)
    files = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return p, files


def _unchanged(p: Project, files: dict[str, str]) -> bool:
    import hashlib

    now = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return now == files


def test_plan_frames_follows_cue_mode_and_freezes_just_before_each_reveal():
    from decktalk.stages.preflight import (
        ORDER_NOTE,
        Freeze,
        first_state,
        last_state,
        mounts,
        owner_slide,
        plan_frames,
    )

    slides = {"1.1": ["1.1in", "1.1a", "1.1b"], "1.2": ["1.2a", "1.2b"]}
    cue_times = {"1.1in": 0.0, "1.1a": 1.0, "1.1b": 2.0, "1.2a": 3.0, "1.2b": 4.0, "1.1zz": 4.5, "9x": 5.0}
    assert [owner_slide(c, slides) for c in ("1.2b", "1.2", "1.1zz", "9x")] == ["1.2", "1.2", "1.1", None]
    assert mounts(slides, cue_times) == [("1.1", 0.0), ("1.2", 3.0)]
    plan = [(p.cue, p.before, p.after, p.reason, p.note) for p in plan_frames(slides, cue_times, 25)]
    assert plan == [
        ("1.1in", None, None, SkipReason.AT_SECTION_START, ""),
        ("1.1a", Freeze("1.1", cue="1.1in"), Freeze("1.1", cue="1.1a"), None, ""),
        ("1.1b", Freeze("1.1", cue="1.1a"), Freeze("1.1", cue="1.1b"), None, ""),
        # 1.2a mounts slide 1.2, so the frame before it is slide 1.1 with every cue it fired.
        ("1.2a", Freeze("1.1", cue="1.1b"), Freeze("1.2", cue="1.2a"), None, ""),
        ("1.2b", Freeze("1.2", cue="1.2a"), Freeze("1.2", cue="1.2b"), None, ""),
        ("1.1zz", None, None, SkipReason.NOT_IN_SLIDE_CUES, ""),
        ("9x", None, None, SkipReason.NO_SLIDE, ""),
    ]
    assert last_state(slides, cue_times) == Freeze("1.2", cue="1.2b")
    assert first_state(slides, cue_times, 25) == Freeze("1.1", cue="1.1in")

    # The first slide mounts at 0 even when its first cue comes later, so that cue freezes just before itself.
    late = {"2.1": ["2.1a", "2.1b"]}
    late_cue_times = {"2.1a": 1.5, "2.1b": 3.0}
    assert plan_frames(late, late_cue_times, 25)[0].before == Freeze("2.1", before="2.1a")
    assert first_state(late, late_cue_times, 25) == Freeze("2.1", before="2.1a")
    assert Freeze("2.1", before="2.1a").query() == {"slide": "2.1", "before": "2.1a"}
    assert Freeze("2.1", cue="2.1aloud").label == "slide-2.1-after-2.1aloud" and Freeze("2.1").query() == {
        "slide": "2.1"
    }

    # A freeze fires in preview order, so cue times in another order get a note.
    swapped = plan_frames({"3.1": ["3.1b", "3.1a"]}, {"3.1a": 1.0, "3.1b": 2.0}, 25)
    assert [(p.before, p.note) for p in swapped] == [
        (Freeze("3.1", cue="3.1b"), ORDER_NOTE),
        (Freeze("3.1", before="3.1b"), ORDER_NOTE),
    ]


def test_slide_cues_reads_a_catalog_scene_and_names_the_reason_when_it_cannot():
    from decktalk.stages.preflight import slide_cues

    catalog = [
        {"scene": "1", "name": "One", "slides": ["1.1", "1.2"], "cues": {"1.1": ["1.1a"], "1.2": []}},
        {"scene": "2", "name": "Two", "slides": ["2.1"]},
    ]
    assert slide_cues(catalog, "1") == ({"1.1": ["1.1a"], "1.2": []}, "")
    assert slide_cues(catalog, "3") == (None, "registers no scene 3")
    assert slide_cues([], "1") == (None, "is missing or has no runtime catalog")
    assert slide_cues(None, "1") == (None, "is missing or has no runtime catalog")
    # A hand-written page may register a scene without the runtime's cue map, and preflight says so
    # rather than raising, because docs/guides/page-by-hand.mdx invites exactly such a page.
    assert slide_cues(catalog, "2") == (None, "registers scene 2 without a slides list and a cues map")


def test_preflight_verdicts_and_findings():
    from decktalk.stages.align import AlignResult
    from decktalk.stages.preflight import CueEstimate, PreflightResult, SeamEstimate, cue_verdict

    cfg = Settings().verify
    assert [cue_verdict(x, cfg) for x in (0.05, 0.2, 0.5)] == [
        Verdict.NO_CHANGE,
        Verdict.THIN_CHANGE,
        Verdict.CHANGED,
    ]
    result = PreflightResult(
        voice={}, narration=Settings().narration, rate=0.0, takes=[], note=None, estimated=[],
        align=AlignResult(cue_times=CueTimes(), sections=[], unresolved=1, estimated=True, unknown=2),
        cues=[CueEstimate("1:a", 1.0, "1.1", 0.2, Verdict.THIN_CHANGE),
              CueEstimate("1:b", 2.0, "1.1", 0.0, Verdict.NO_CHANGE),
              CueEstimate("1:c", 3.0, "1.1", 4.0, Verdict.CHANGED),
              CueEstimate("1:d", 0.0, None, None, Verdict.SKIPPED)],
        seams=[SeamEstimate("02", 3.1, Verdict.POP_AT_CUT), SeamEstimate("03", 0.0, Verdict.OK)],
    )  # fmt: skip
    assert result.findings == Findings(certain=5, uncertain=1)
    assert replace(result, allow_unknown_cues=True).findings == Findings(certain=3, uncertain=1)


def test_preflight_resolves_cues_on_the_words_each_section_will_have(tmp_path, monkeypatch, capsys):
    from decktalk.artifacts import read_words
    from decktalk.model.cues import find_phrase
    from decktalk.stages.preflight import preflight

    p, files = _planned_scaffold(tmp_path, monkeypatch)
    result = preflight(p, frames=False)
    assert {t.segment.key: t.status for t in result.takes} == {
        "01": "cached", "02": "cached", "03": "synthesize", "04": "synthesize",
        "06": "synthesize", "08": "synthesize", "09": "cached",
    }  # fmt: skip
    assert result.estimated == ["03", "04", "06", "08"]
    assert result.align.unresolved == 0 and result.align.unknown == 0
    resolved = {s.key: {r.cue: r.at for r in s.resolved} for s in result.align.sections}
    # A take that is already on disk resolves on its own words, which the fixture spaced 0.4 s apart,
    # moved by the section's lead, which for the first spoken section is the opening silence.
    index = p.takes()
    assert index is not None
    open_words = read_words(p.narration_dir / index.sections["01"].words_file)
    assert open_words[find_phrase(open_words, "bowl")].start == 0.4
    assert resolved["01"]["1.1bowl"] == round(0.4 + p.settings.narration.opening_silence_seconds, 2)
    close_words = read_words(p.narration_dir / index.sections["09"].words_file)
    assert resolved["09"]["5.1url"] == round(close_words[find_phrase(close_words, "decktalk dot AI")].start, 2)
    # A section that would be voiced resolves on estimated words, inside its estimated length.
    assert all(0 < t < 60 for t in resolved["04"].values()) and len(resolved["04"]) == 5
    assert result.cues == [] and result.seams == [] and result.frames is None
    assert _unchanged(p, files) and not p.cue_times_path.exists() and not (p.build / "preflight").exists()

    assert main(["preflight", "--no-frames", "--json", "-p", str(p.root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "preflight" and doc["ok"] is True
    payload = doc["preflight"]
    assert set(payload) == {"voice", "note", "placeholders", "takes", "totals", "cue_times", "cues", "seams", "frames"}
    assert (
        payload["cue_times"]["estimated_sections"] == ["03", "04", "06", "08"] and payload["totals"]["synthesize"] == 4
    )
    assert {t["key"]: t["hash"] for t in payload["takes"]}["09"] == index.sections["09"].hash
    assert main(["preflight", "--no-frames", "-p", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert "3 cached." in out and "frames skipped (--no-frames)" in out

    # A phrase that is not in the script, under an id the page never names, is two certain findings.
    cues_path = p.root / "cues.json"
    cues = json.loads(cues_path.read_text(encoding="utf-8"))
    cues["sections"]["1"]["cues"].append({"cue": "1.1nope", "on": "not in the script"})
    cues_path.write_text(json.dumps(cues), encoding="utf-8")
    script = p.root / "script.md"
    script.write_text(script.read_text(encoding="utf-8").replace("## 9. Close\n", "## 9. Close\n\n[CLIENT_NAME]\n", 1))
    assert main(["preflight", "--no-frames", "--json", "-p", str(p.root)]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 3, "uncertain": 0} and doc["preflight"]["placeholders"] == ["CLIENT_NAME"]
    assert main(["preflight", "--no-frames", "--json", "--allow-unknown-cues", "-p", str(p.root)]) == 1
    assert json.loads(capsys.readouterr().out)["findings"] == {"certain": 2, "uncertain": 0}
    assert main(["preflight", "--no-frames", "--exit-zero", "-p", str(p.root)]) == 0
    assert _unchanged(p, files)


# ---- words and clip -------------------------------------------------------------------


def _words_project(tmp_path: Path) -> Project:
    """Page sections 1 (with a 0.5 s lead) and 2, a clip section 3, a timeline, and a take_index entry for 1 only."""
    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\nchapter = 'Open'\npage = 'a.html'\nlead_seconds = 0.5\n"
        "[[section]]\nnumber = 2\nchapter = 'Close'\npage = 'a.html'\n"
        "[[section]]\nnumber = 3\nclip = 'media/c.mp4'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    Timeline(
        narration="narration.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("Open", 0.0, 3.0, 3.0, 1.4, [Word("Hello", 0.6, 0.9), Word("there", 1.0, 1.4)], 0.5),
            "02": TimelineSection("Close", 3.0, 6.0, 3.0, 4.1, [Word("Bye", 3.2, 3.5), Word("now", 3.6, 4.1)]),
        },
    ).save(p.timeline_path)
    take_index = Takes(script="script.md", model="m", output_format="mp3_44100_128")
    take_index.sections["01"] = Take(
        index=1, chapter="Open", file="01-open.mp3", words_file="01-open.words.json", hash="h",
        word_count=2, estimated_seconds=1.0, duration_seconds=2.5, spoken="Hello, there.",
    )  # fmt: skip
    take_index.save(p.takes_path)
    return p


def test_words_are_relative_to_each_section_with_the_scripts_spelling(tmp_path):
    from decktalk.stages.clip import words

    p = _words_project(tmp_path)
    first, second = words(p).sections
    assert (first.key, first.title, first.lead_seconds, first.duration) == ("01", "Open", 0.5, 3.0)
    assert first.estimated is False
    assert first.words == [Word("Hello", 0.6, 0.9), Word("there", 1.0, 1.4)]
    assert first.texts == ["Hello,", "there."]
    # Section 2 starts at 3.0 s in narration.mp3, and it has no take_index entry, so it keeps the voice's spelling.
    assert second.words == [Word("Bye", 0.2, 0.5), Word("now", 0.6, 1.1)]
    assert second.texts == ["Bye", "now"]
    assert [s.key for s in words(p, only=[2]).sections] == ["02"]
    no_words = r"section\(s\) \[3\] have no words in timeline.json; spoken sections are \[1, 2\]"
    with pytest.raises(ConfigError, match=no_words):
        words(p, only=[3])


def test_spoken_words_needs_a_timeline(tmp_path):
    from decktalk.errors import MissingInputError
    from decktalk.stages.clip import words

    (tmp_path / "decktalk.toml").write_text("[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8")
    with pytest.raises(MissingInputError, match="Run `decktalk narrate` first"):
        words(Project.load(tmp_path, environ={}))


def test_cli_words_prints_a_table_and_json(tmp_path, capsys):
    _words_project(tmp_path)
    assert main(["-p", str(tmp_path), "words", "--only", "1"]) == 0
    table = capsys.readouterr().out
    assert "== 01 Open  (3.00s, lead 0.5s)" in table
    assert "  0.600   0.900  Hello," in table
    assert "Close" not in table
    assert main(["-p", str(tmp_path), "words", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert (doc["command"], doc["ok"], doc["findings"]) == ("words", True, {"certain": 0, "uncertain": 0})
    first, second = doc["words"]["sections"]
    assert first["section"] == 1 and first["lead_seconds"] == 0.5
    assert first["words"][0] == {"word": "Hello", "text": "Hello,", "start": 0.6, "end": 0.9}
    assert second["words"][1] == {"word": "now", "text": "now", "start": 0.6, "end": 1.1}


def test_clip_words_keep_only_whole_words_shifted_to_the_clip(tmp_path):
    from decktalk.stages.clip import _clip_words

    p = _words_project(tmp_path)
    assert _clip_words(p, "01", 0.55, 1.2) == ([Word("Hello,", 0.05, 0.35)], ["there."])
    assert _clip_words(p, "01", 0.6, 1.4) == ([Word("Hello,", 0.0, 0.3), Word("there.", 0.4, 0.8)], [])
    assert _clip_words(p, "01", 1.5, 2.0) == ([], [])


def test_clip_refuses_a_bad_span_before_it_runs_ffmpeg(tmp_path):
    from decktalk.errors import MissingInputError
    from decktalk.stages.clip import clip

    p = _words_project(tmp_path)
    with pytest.raises(ConfigError, match="section 3 is a clip section"):
        clip(p, 3, start=0, end=1, out="media/x.mp4")
    with pytest.raises(ConfigError, match="section 7 is not in decktalk.toml"):
        clip(p, 7, start=0, end=1, out="media/x.mp4")
    with pytest.raises(ConfigError, match="end after it starts, got 2 to 1"):
        clip(p, 1, start=2, end=1, out="media/x.mp4")
    with pytest.raises(ConfigError, match="--hold must be 0 or more"):
        clip(p, 1, start=0, end=1, out="media/x.mp4", hold_seconds=-1)
    with pytest.raises(MissingInputError, match="no section video at .*sections/01.mp4. Run `decktalk assemble` first"):
        clip(p, 1, start=0, end=1, out="media/x.mp4")
    p.sections_dir.mkdir(parents=True)
    p.section_video(p.sections[1]).write_bytes(b"")
    with pytest.raises(MissingInputError, match="section 2 has no narration yet"):
        clip(p, 2, start=0, end=1, out="media/x.mp4")


def test_cli_clip_parses_its_span_and_defaults():
    args = build_parser().parse_args(["clip", "1", "--start", "1.5", "--end", "4", "--out", "media/a.mp4"])
    assert (args.section, args.start, args.end, args.out) == (1, 1.5, 4.0, "media/a.mp4")
    assert (args.words, args.gain, args.hold, args.preset, args.crf) == (None, 0.0, 0.0, None, None)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["clip", "1", "--start", "1.5", "--out", "media/a.mp4"])
