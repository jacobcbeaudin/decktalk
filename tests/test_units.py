"""Unit tests that need neither ffmpeg nor Chromium nor an API key."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

import decktalk
from decktalk import ConfigError, Project, load_settings
from decktalk.artifacts import (
    CueTime,
    CueTimes,
    RecordingLog,
    Take,
    Takes,
    Timeline,
    TimelineSection,
    Word,
    gap_time,
)
from decktalk.cli import build_parser, main
from decktalk.model.script import parse_script, strip_markdown
from decktalk.settings import Settings
from decktalk.stages.align import Cue, find_phrase, resolve_cue
from decktalk.stages.assemble import cut_summary, fade_flags, timeline_targets
from decktalk.stages.narrate import estimated_words
from decktalk.verdicts import Findings, Verdict

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
    assert p.env.get("ELEVENLABS_API_KEY") == ""
    assert p.env.get("ELEVENLABS_VOICE_ID") == "abc"
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        p.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")


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
    assert [r.getMessage() for r in caplog.records] == [
        "decktalk.toml: [project]: ignoring unknown key 'scirpt' (did you mean 'script'?)",
        "decktalk.toml: [[section]] number=1: ignoring unknown key 'scnee' (did you mean 'scene'?)",
        "decktalk.toml: [[section]] number=1: ignoring 'slate_seconds', which applies only to a clip section",
        "decktalk.toml: [[section]] number=2: ignoring unknown key 'zebra'",
        "decktalk.toml: [voice]: ignoring unknown key 'stabilty' (did you mean 'stability'?)",
        "decktalk.toml: [mix.loudness]: ignoring unknown key 'range_luu' (did you mean 'range_lu'?)",
        "decktalk.toml: [soundscape.music]: ignoring unknown key 'second' (did you mean 'seconds'?)",
        "decktalk.toml: [video]: ignoring unknown key 'presett' (did you mean 'preset'?)",
    ]
    # A warning, not an error: the load succeeds and every misspelled key keeps its default.
    assert p.voice.stability == 0.55 and p.settings.video.preset == "medium" and p.soundscape.music.seconds == 360


def test_user_settings_file_warns_about_unknown_keys(tmp_path, caplog):
    from decktalk.settings import read_user_toml

    path = tmp_path / "decktalk.toml"
    path.write_text("[record]\nsettle_second = 0.8\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="decktalk"):
        assert read_user_toml(path) == {"record": {"settle_second": 0.8}}
    assert [r.getMessage() for r in caplog.records] == [
        f"{path}: [record]: ignoring unknown key 'settle_second' (did you mean 'settle_seconds'?)"
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
    # Sections 4 to 8 share the title "The edit", so the video shows them as one chapter.
    titles = ["Open", "How it works", "How AI learns", *["The edit"] * 5, "Close"]
    assert [s.chapter for s in p.sections] == titles


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


def test_recording_log_trim_prefers_measured(tmp_path):
    s = RecordingLog(url="u", requested_seconds=5, settle_seconds=0.5, load_seconds=0.1, clock_start_seconds=0.7)
    assert s.trim_seconds == 0.7
    s.t0_seconds = 0.2
    assert s.trim_seconds == 0.2


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


def test_estimated_words_span_the_duration():
    seg = parse_script("## 1. A\n\none two three four five")[0]
    seg.first_spoken = True
    words = estimated_words(seg, 5.0, Settings().narration)
    assert [w.word for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0].start == 0.7 and words[-1].end < 5.0 - 0.35


# ---- align ---------------------------------------------------------------------------------


def test_find_phrase_and_resolve():
    words = [
        Word("Hello", 0.0, 0.3),
        Word("one", 0.4, 0.6),
        Word("in", 0.7, 0.8),
        Word("ten", 0.9, 1.2),
        Word("one", 1.5, 1.7),
    ]
    assert find_phrase(words, "one in ten") == 1
    assert find_phrase(words, "one", occurrence=2) == 4
    assert find_phrase(words, "missing") is None
    assert resolve_cue(Cue("a", "in ten", offset=0.1), words) == 0.8
    assert resolve_cue(Cue("a", "$end"), words) == 1.7
    assert resolve_cue(Cue("a", "$start", offset=2), words) == 2.0


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
    flags = fade_flags(p)
    assert flags["00"] == (False, True)  # clip fades out into the dip
    assert flags["01"] == (False, False)  # page fades itself in; no dip after
    assert flags["02"] == (False, False)
    p2 = Project.load(write_project(tmp_path, MINIMAL_TOML), environ={})  # no dips key: every cut dips
    assert fade_flags(p2)["01"] == (False, True)
    # The assemble log names what the cuts do rather than always saying "straight cuts".
    assert cut_summary(p) == "dips at 1 cut"
    assert cut_summary(p2) == "dips at every cut"
    p3 = Project.load(write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = []\n"), environ={})
    assert cut_summary(p3) == "straight cuts"


# ---- package and cli -----------------------------------------------------------------------


def test_package_exports_every_public_name():
    for name in decktalk.__all__:
        assert hasattr(decktalk, name), name
    for name in ("AssembleResult", "BuildResult", "Voice", "SpeechProvider", "register_speech_provider", "__version__"):
        assert name in decktalk.__all__


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


def test_recording_log_warnings_default_and_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            {"url": "u", "requested_seconds": 1, "settle_seconds": 0, "load_seconds": 0, "clock_start_seconds": 0}
        ),
        encoding="utf-8",
    )
    old = RecordingLog.load(p)
    assert old is not None and old.warnings == []
    old.warnings = ["KaTeX did not load within 5 s, so [data-tex] elements stay plain text"]
    old.save(p)
    again = RecordingLog.load(p)
    assert again is not None and again.warnings == old.warnings


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
    """Every cue id in cues.json is named in the page, and every phrase is in its section."""
    from decktalk.scaffold import init
    from decktalk.stages.align import unknown_cue_ids

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    project = Project.load(root, environ={})
    specs = project.cue_specs()
    assert unknown_cue_ids(project, specs) == []
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
    from decktalk.stages.assemble import frame_dip

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
    chapters = build_chapters(rows)
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
    chapters = build_chapters(rows)
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0.0, 2.0, "Section 1"),
        (2.0, 5.0, "Section 2"),
        (5.0, 7.52, "Section 3"),
        (7.52, 9.0, "Section 4"),
    ]


def test_click_search_stays_inside_the_section(monkeypatch):
    from decktalk.media import audio as audio_module
    from decktalk.stages.verify import click_offset_ms

    calls: list[tuple[float, float]] = []

    def fake_span(path, start, seconds, *, sample_rate=48000):
        calls.append((round(start, 3), round(seconds, 3)))
        return [0] * 100 + [2000] + [0] * 100

    monkeypatch.setattr(audio_module, "pcm_span", fake_span)
    assert click_offset_ms(Path("f.mp4"), 10.0, 0.25) is not None
    assert click_offset_ms(Path("f.mp4"), 5.1, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 8.9, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 9.5, 0.25, floor=5.0, ceiling=9.0) is None
    assert calls == [(9.75, 0.5), (5.0, 0.35), (8.65, 0.35)]


def test_loudness_problems_report_peaks_and_missed_targets(tmp_path):
    from decktalk.media.audio import Loudness
    from decktalk.stages.assemble import loudness_problems

    p = Project.load(write_project(tmp_path), environ={})
    assert loudness_problems(p, Loudness(i=-16.4, tp=-1.6, lra=5, thresh=-27, offset=0)) == []
    over = loudness_problems(p, Loudness(i=-25.2, tp=-1.0, lra=5, thresh=-27, offset=0))
    assert len(over) == 2 and "true peak -1.0 dBTP" in over[0] and "9.2 LU" in over[1]


def test_onset_offset_finds_the_jump_and_falls_back_to_the_floor():
    from decktalk.stages.verify import onset_offset_ms

    # A fade of a small element, as measured on the scaffold: change begins 60 ms after the cue
    # but only crosses a tenth of the picture 220 ms after it.
    fade = [(9.2, 0.0), (9.24, 0.0), (9.28, 0.0), (9.32, 0.0), (9.36, 0.018), (9.4, 0.038), (9.52, 0.103)]
    assert onset_offset_ms(fade, before=9.2, cue_at=9.3, onset=0.01) == 60
    assert onset_offset_ms(fade, before=9.2, cue_at=9.3, onset=0.1) == 220
    # A camera push is a slope: the share grows a little every frame and never jumps, so
    # the onset is the first real jump, even though the slope crosses the threshold earlier.
    push = [(9.2, 0.0), (9.24, 0.006), (9.28, 0.012), (9.32, 0.018), (9.36, 0.06), (9.4, 0.07)]
    assert onset_offset_ms(push, before=9.2, cue_at=9.3, onset=0.02) == 60
    # A reveal that lands a frame early reports a negative offset rather than being hidden.
    early = [(9.2, 0.0), (9.24, 0.0), (9.28, 0.3), (9.32, 0.3), (9.36, 0.3)]
    assert onset_offset_ms(early, before=9.2, cue_at=9.3, onset=0.02) == -20
    assert onset_offset_ms([(9.2, 0.0), (9.24, 0.0)], before=9.2, cue_at=9.3, onset=0.01) is None
    # When the reference time falls between frames, the series starts on the frame after it,
    # which is the reference itself, and a reveal on the very next frame is still the onset.
    off_grid = [(9.24, 0.0), (9.28, 0.23), (9.32, 0.26), (9.36, 0.26)]
    assert onset_offset_ms(off_grid, before=9.21, cue_at=9.31, onset=0.002) == -30
    # Encoder ringing, as measured on the scaffold's 3:3.1again: two frames before the reveal change
    # a few pixels, but no block changes, so the onset is the reveal itself, not 100 ms early.
    ringing = [(9.16, 0.0), (9.2, 0.1065), (9.24, 0.0455), (9.28, 1.6088), (9.32, 2.2168)]
    blocks = {9.16: 0.0, 9.2: 0.0, 9.24: 0.0, 9.28: 1.926, 9.32: 2.793}
    assert onset_offset_ms(ringing, before=9.16, cue_at=9.3, onset=0.01) == -100
    assert onset_offset_ms(ringing, before=9.16, cue_at=9.3, onset=0.01, blocks=blocks) == -20
    assert Settings().verify.max_offset_frames == 2 and Settings().verify.onset_percent == 0.01


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


def test_recording_log_verdicts_flag_page_errors_and_bad_tex(tmp_path):
    from decktalk.artifacts import RecordingLog
    from decktalk.settings import RecordConfig
    from decktalk.stages.measure import log_verdicts

    recording_log = RecordingLog(url="x", requested_seconds=1, settle_seconds=0, load_seconds=0, clock_start_seconds=0)
    assert log_verdicts(recording_log, RecordConfig()) == []
    assert log_verdicts(None, RecordConfig()) == []
    recording_log.page_errors = ["ReferenceError: nope is not defined (index.html:5)"]
    recording_log.warnings = [
        'data-tex could not be parsed: "\\frac{1}" (write \\\\ for every backslash inside a template literal)'
    ]
    recording_log.frame_gaps = [(1.0, 400)]
    assert log_verdicts(recording_log, RecordConfig()) == [
        Verdict.PAGE_ERROR,
        Verdict.KATEX_UNSURE,
        Verdict.STALLED,
    ]
    recording_log.save(tmp_path / "s.json")
    again = RecordingLog.load(tmp_path / "s.json")
    assert again is not None and again.page_errors == recording_log.page_errors


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


def test_reference_time_skips_the_fade_and_keeps_the_lead():
    from decktalk.settings import VerifyConfig
    from decktalk.stages.verify import reference_time

    cfg = VerifyConfig()  # lead_seconds 0.1
    # The lead clears a reveal that lands max_offset_frames (2) early: (2 + 1.5) / 25 = 0.14 s.
    assert reference_time(10.0, 2.0, False, 0.16, cfg, 25) == 11.86
    assert reference_time(10.0, 0.05, False, 0.16, cfg, 25) == 10.0  # the section's first frame, a frame early
    assert reference_time(10.0, 0.2, True, 0.16, cfg, 25) == 10.16  # the first frame after the fade-in
    assert reference_time(10.0, 0.15, True, 0.16, cfg, 25) is None  # the cue sits inside the fade-in
    assert reference_time(10.0, 0.0, False, 0.16, cfg, 25) is None  # a $start cue has no frame before it


def _cue_row(item: str) -> dict[str, object]:
    """One cue-times row from the shorthand "cue@seconds" the verify tests are written in."""
    cue, _, at = item.rpartition("@")
    return {"cue": cue, "on": cue, "at": float(at), "word_at": float(at)}


def _verify_project(
    tmp_path,
    monkeypatch,
    cue_times: dict[str, str],
    cues: dict | None = None,
    change: float = 0.0,
    toml: str = PAGES_TOML,
):
    """A project with sections 01 and 02 assembled and every ffmpeg measurement replaced."""
    from decktalk.media import ffmpeg as ffmpeg_module
    from decktalk.media import frames as frames_module

    root = write_project(tmp_path, toml)
    p = Project.load(root, environ={})
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    for key in ("01", "02"):
        (p.sections_dir / f"{key}.mp4").write_bytes(b"x")
    p.final.write_bytes(b"x")
    p.narration_dir.mkdir(parents=True)
    sections = {k: [_cue_row(item) for item in v.split(",") if item] for k, v in cue_times.items()}
    p.cue_times_path.write_text(json.dumps({"sections": sections}), encoding="utf-8")
    if cues is not None:
        (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    monkeypatch.setattr(ffmpeg_module, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(frames_module, "luma_at", lambda path, t, crop=None: (100.0, 200.0))
    monkeypatch.setattr(frames_module, "changed_pixels_percent", lambda path, t1, t2, **kw: change)
    monkeypatch.setattr(frames_module, "changed_series", lambda *a, **kw: [])
    return p


def test_verify_default_checks_come_from_cue_times_json(tmp_path, monkeypatch):
    from decktalk.stages.verify import verify

    p = _verify_project(tmp_path, monkeypatch, {"02": "c@2.0", "01": "b@3.0,a@1.0"})
    result = verify(p)
    assert [c.check for c in result.cues] == ["1:a", "1:b", "2:c"]  # section order, then cue time
    assert all(c.verdict == "NO CHANGE" for c in result.cues) and not result.ok
    assert [c.check for c in verify(p, only=[2]).cues] == ["2:c"]
    assert verify(p, checks=[]).cues == [] and verify(p, checks=[]).ok
    assert [c.check for c in verify(p, checks=["1:b", "2:c"], only=[1]).cues] == ["1:b"]
    missing = verify(p, checks=["1:nope"]).cues[0]
    assert missing.verdict == "UNRESOLVED" and not missing.ok and missing.cue_seconds is None


def test_verify_skips_clamped_start_cue(tmp_path, monkeypatch):
    from decktalk.stages.verify import verify

    p = _verify_project(tmp_path, monkeypatch, {"01": "start@0.0", "03": "later@1.0"}, change=5.0)
    result = verify(p)
    start, later = result.cues
    assert (start.verdict, start.reason) == ("skipped", "REFERENCE_CLAMPED")
    assert start.cue_seconds is None and start.note.startswith("skipped REFERENCE_CLAMPED")
    assert (later.verdict, later.reason) == ("skipped", "SECTION_NOT_ASSEMBLED")
    assert result.ok  # Skipped rows never fail.


def test_verify_opted_out_cue_is_skipped(tmp_path, monkeypatch):
    from decktalk.stages.verify import verify

    cues = {"1": {"cues": [{"cue": "a", "on": "hello", "verify": False}, {"cue": "b", "on": "there"}]}}
    p = _verify_project(tmp_path, monkeypatch, {"01": "a@1.0,b@2.0"}, cues=cues)
    assert [c.verify for c in p.cue_specs()[0].cues] == [False, True]
    a, b = verify(p).cues
    assert (a.verdict, a.reason) == ("skipped", "OPTED_OUT") and b.verdict == "NO CHANGE"
    (named,) = verify(p, checks=["1:a"]).cues  # A cue named on purpose is measured anyway.
    assert named.verdict == "NO CHANGE" and named.reason is None
    bad = {"sections": {"1": {"cues": [{"cue": "a", "on": "x", "verify": 0}]}}}
    (p.root / "cues.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError, match="'verify' must be bool, got int"):
        p.cue_specs()


def test_verify_marks_a_thin_change_as_uncertain(tmp_path, monkeypatch, capsys):
    import dataclasses
    import importlib

    from decktalk.stages.verify import thin_change, verify

    verify_module = importlib.import_module("decktalk.stages.verify")

    cfg = Settings().verify
    assert cfg.thin_change_factor == 3.0
    assert thin_change(0.11, 0.11, cfg) and thin_change(0.29, 5.0, cfg) and thin_change(5.0, 0.29, cfg)
    assert not thin_change(0.3, 0.3, cfg) and not thin_change(6.56, 6.56, cfg)
    assert not thin_change(0.11, 0.11, dataclasses.replace(cfg, thin_change_factor=1.0))

    p = _verify_project(tmp_path, monkeypatch, {"01": "a@1.0"}, change=0.11)
    (row,) = verify(p).cues
    assert (row.verdict, row.ok, row.reason) == ("THIN CHANGE?", True, None)
    assert verify(p).ok and row.to_dict()["verdict"] == "THIN CHANGE?"
    # The onset branch keeps the thin verdict when on time, and OFF CUE still wins when late.
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: 0)
    assert [c.verdict for c in verify(p).cues] == ["THIN CHANGE?"]
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: 400)
    assert [c.verdict for c in verify(p).cues] == ["OFF CUE"]
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: None)

    # An uncertain finding: exit 0, and 1 only with --strict. The table and the JSON both show it.
    assert main(["-p", str(p.root), "verify"]) == 0
    assert "THIN CHANGE?" in capsys.readouterr().out
    assert main(["-p", str(p.root), "verify", "--strict"]) == 1
    capsys.readouterr()
    assert main(["-p", str(p.root), "verify", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 0, "uncertain": 1} and doc["verify"]["cues"][0]["verdict"] == "THIN CHANGE?"

    # A clear change reads changed, and the factor can turn the warning off.
    monkeypatch.setattr("decktalk.media.frames.changed_pixels_percent", lambda path, t1, t2, **kw: 0.5)
    assert [c.verdict for c in verify(p).cues] == ["changed"]
    monkeypatch.setattr("decktalk.media.frames.changed_pixels_percent", lambda path, t1, t2, **kw: 0.11)
    monkeypatch.setenv("DECKTALK_VERIFY_THIN_CHANGE_FACTOR", "1")
    assert [c.verdict for c in verify(Project.load(p.root)).cues] == ["changed"]


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


def test_verify_flags_a_pop_at_the_cut_into_a_seamless_section(tmp_path, monkeypatch, capsys):
    from decktalk.media import frames as frames_module
    from decktalk.stages.verify import verify

    seamless_toml = PAGES_TOML.replace(
        'number = 2\npage = "deck/index.html"\n', 'number = 2\npage = "deck/index.html"\nseamless = true\n'
    )
    seamless_toml = seamless_toml.replace(
        'number = 3\npage = "deck/index.html"\n', 'number = 3\npage = "deck/index.html"\nseamless = true\n'
    )
    p = _verify_project(tmp_path, monkeypatch, {}, toml=seamless_toml)
    calls: list[tuple[float, float, dict]] = []
    share = [0.05]

    def changed(path, t1, t2, **kw):
        calls.append((round(t1, 3), round(t2, 3), kw))
        return share[0]

    monkeypatch.setattr(frames_module, "changed_pixels_percent", changed)
    result = verify(p)
    # Section 1 dips out over 0.16 s, so the last frame compared sits before the dip. Section 3 is not assembled.
    assert calls == [(4.78, 5.0, {"level": 40, "width": 480, "height": 270})]
    (row,) = result.seams
    assert (row.key, row.cut_at, row.verdict, row.ok) == ("02", 5.0, "ok", True) and result.ok

    share[0] = 0.5
    result = verify(p)
    assert [c.verdict for c in result.seams] == ["POP AT CUT"] and not result.ok
    assert result.to_dict(p.root)["seams"] == [
        {"key": "02", "cut_at": 5.0, "last_at": 4.78, "first_at": 5.0, "changed_percent": 0.5, "verdict": "POP AT CUT"}
    ]
    assert main(["-p", str(p.root), "verify"]) == 1
    assert "POP AT CUT" in capsys.readouterr().out
    assert main(["-p", str(p.root), "verify", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 1, "uncertain": 0} and doc["verify"]["seams"][0]["verdict"] == "POP AT CUT"

    # A straight cut compares the frame just before the cut, and a section without the key gets no row.
    calls.clear()
    (p.root / "decktalk.toml").write_text(seamless_toml + "\n[transition]\ndips = []\n", encoding="utf-8")
    assert [c.last_at for c in verify(Project.load(p.root, environ={})).seams] == [4.94]
    (p.root / "decktalk.toml").write_text(PAGES_TOML, encoding="utf-8")
    assert verify(Project.load(p.root, environ={})).seams == []


def test_verify_to_dict_is_json_serialisable_and_relative(tmp_path, monkeypatch):
    from decktalk.stages.verify import CueCheck, verify

    p = _verify_project(tmp_path, monkeypatch, {"01": "start@0.0,a@1.23456"})
    d = json.loads(json.dumps(verify(p).to_dict(p.root)))
    assert d["final"] == "build/out/t.mp4" and d["total_seconds"] == 10.0 and d["silent"] is False
    first = {"key": "01", "start": 0.0, "probe_at": 0.2, "yavg": 100.0, "ymax": 200.0, "verdict": "ok"}
    assert d["starts"][0] == first and d["cuts"] == []
    start, a = d["cues"]
    assert start["section"] == 1 and start["cue"] == "start" and start["reason"] == "REFERENCE_CLAMPED"
    assert a["cue_seconds"] == 1.235 and a["verdict"] == "NO CHANGE" and a["reason"] is None
    row = CueCheck("3:3.1eq", 15.6612, 72.38123, 0.29444, 0.0, True, "", -20, -5).to_dict()
    assert row == {
        "section": 3,
        "cue": "3.1eq",
        "cue_seconds": 15.661,
        "final_seconds": 72.381,
        "changed_percent": 0.29,
        "control_percent": 0.0,
        "offset_ms": -20,
        "av_ms": -5,
        "verdict": "changed",
        "reason": None,
    }


def test_check_row_carries_its_verdict_codes_and_its_stall(tmp_path):
    from decktalk.stages.measure import RecordingCheck

    file = tmp_path / "build" / "recordings" / "01.webm"
    verdicts = (Verdict.NO_COVER, Verdict.STALLED)
    row = RecordingCheck("01", 10.04, 10.3, 50.0, 60.0, 70.0, 80.0, verdicts, 140, ["boom"], file=file)
    d = json.loads(json.dumps(row.to_dict(tmp_path)))
    assert d["file"] == "build/recordings/01.webm" and d["duration"] == 10.04 and d["max50"] == 80.0
    assert d["verdicts"] == ["NO_COVER", "STALLED"] and d["stall_ms"] == 140 and d["page_errors"] == ["boom"]
    assert row.label == "NO COVER STALLED 140ms" and not row.ok
    assert RecordingCheck("02", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0).label == "ok"


def test_page_mentions_finds_quoted_ids_and_data_cue():
    from decktalk.stages.align import page_mentions

    html = """<div data-cue="4.1answer"></div>
    <script>DeckTalk.scene({ preview: { "4.1x": 1 }, on: { '4.1y': () => {} }, tpl: `4.1z` });</script>"""
    for cue in ("4.1answer", "4.1x", "4.1y", "4.1z"):
        assert page_mentions(html, cue), cue
    assert not page_mentions(html, "4.1")  # A prefix of a quoted id is not a mention.
    assert not page_mentions(html, "4.1ans")
    assert not page_mentions("<p>\"4.1x'</p>", "4.1x")  # The quotes must match.


def _align_project(tmp_path, html: str, cues: dict) -> Project:
    """Sections 0 (a clip) and 1 (a page), with narration words for section 1."""
    from decktalk.artifacts import write_words

    toml = "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"
    root = write_project(tmp_path, toml)
    (root / "deck").mkdir()
    (root / "deck" / "index.html").write_text(html, encoding="utf-8")
    (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    p = Project.load(root, environ={})
    m = Takes(script="script.md", model="m", output_format="mp3")
    m.sections["01"] = Take(1, "A", "01-a.mp3", "01-a.words.json", "h", 2, 1.0, 3.0)
    m.save(p.takes_path)
    write_words(p.narration_dir / "01-a.words.json", [Word("hello", 0.5, 0.9), Word("there", 1.0, 1.4)])
    return p


def test_align_reports_a_cue_id_missing_from_the_page(tmp_path):
    from decktalk.stages.align import UnknownCueError, align, unknown_cue_ids

    cues = {
        "0": {"cues": [{"cue": "0.clip", "on": "$start"}]},
        "1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]},
    }
    p = _align_project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    assert unknown_cue_ids(p, p.cue_specs()) == [("01", "4.1answer", "deck/index.html")]
    with pytest.raises(UnknownCueError) as caught:
        align(p)
    assert str(caught.value).startswith(
        "1 cue id(s) in cues.json appear nowhere in the page that plays them, so the page would never reveal "
        'them. Add data-cue="4.1answer" to the slide in deck/index.html, fix the id in cues.json, or pass '
        "--allow-unknown-cues:\n  section 01: 4.1answer: not in deck/index.html"
    )
    assert isinstance(caught.value, ConfigError) and caught.value.result.unknown == 1
    assert json.loads(p.cue_times_path.read_text(encoding="utf-8")) == {
        "estimated": False,
        "sections": {
            "01": [
                {"cue": "1.1a", "on": "hello", "at": 0.5, "word_at": 0.5},
                {"cue": "4.1answer", "on": "there", "at": 1.0, "word_at": 1.0},
            ]
        },
    }  # written before the stop
    result = align(p, allow_unknown_cues=True)
    assert result.unknown == 1 and result.unresolved == 0
    assert result.sections[1].notes == ["4.1answer: not in deck/index.html"]


def test_cli_align_reports_unknown_cue_ids_as_a_finding(tmp_path, capsys):
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]}}
    p = _align_project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    # Without --allow-unknown-cues the command still prints its JSON and exits 1, instead of stopping on the error.
    assert main(["-p", str(p.root), "align", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"]["certain"] == 1
    assert doc["align"]["unknown"] == 1
    assert main(["-p", str(p.root), "align", "--json", "--allow-unknown-cues"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_align_to_dict_counts_unresolved(tmp_path):
    from decktalk.stages.align import align

    cues = {"1": {"min_seconds": 9, "cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "1.1b", "on": "missing phrase"}]}}
    p = _align_project(tmp_path, "<b data-cue='1.1a'></b><b data-cue='1.1b'></b>", cues)
    d = json.loads(json.dumps(align(p).to_dict(p.root)))
    assert d["estimated"] is False and d["cue_times_file"] == "build/cue-times.json"
    assert d["unresolved"] == 1 and d["unknown"] == 0
    (section,) = d["sections"]
    assert section["key"] == "01" and section["speech_end"] == 1.4 and section["min_seconds"] == 9.0
    assert section["skipped"] is None and section["cues"] == {"1.1a": 0.5}
    assert section["notes"] == [
        {"cue": "1.1b", "verdict": "UNRESOLVED", "detail": "phrase not found: 'missing phrase'"},
        {"cue": None, "verdict": None, "detail": "speech 1.4s is 7.6s shorter than the visuals need"},
    ]
    assert sum(n["verdict"] == "UNRESOLVED" for s in d["sections"] for n in s["notes"]) == d["unresolved"]


def test_align_warns_about_a_repeated_phrase_unless_the_cue_names_its_occurrence(tmp_path, capsys):
    from decktalk.artifacts import write_words
    from decktalk.stages.align import align

    cues = {
        "1": {
            "cues": [
                {"cue": "1.1step", "on": "every step"},
                {"cue": "1.1later", "on": "every step", "occurrence": 1},
                {"cue": "1.1once", "on": "there"},
                {"cue": "1.1case", "on": "Every", "case_sensitive": True},
                {"cue": "1.1end", "on": "$end"},
            ]
        }
    }
    ids = "".join(f"<b data-cue='{c}'></b>" for c in ("1.1step", "1.1later", "1.1once", "1.1case", "1.1end"))
    p = _align_project(tmp_path, ids, cues)
    words = [
        Word("Every", 0.5, 0.8), Word("step", 0.9, 1.2), Word("there.", 1.3, 1.6),
        Word("For", 36.0, 36.2), Word("every", 36.3, 36.6), Word("step!", 36.7, 37.1),
    ]  # fmt: skip
    write_words(p.narration_dir / "01-a.words.json", words)
    take_index = p.takes()
    assert take_index is not None
    take_index.sections["01"].duration_seconds = 38.0
    take_index.save(p.takes_path)
    result = align(p)
    (section,) = [s for s in result.sections if s.key == "01"]
    detail = (
        "'every step' occurs 2 times in this section, at 0.50s, 36.30s. The cue uses the first. "
        'Set "occurrence" to choose one.'
    )
    # Only the cue that names no occurrence is ambiguous. A case-sensitive phrase counts only its own case.
    assert [(n.cue, n.verdict, n.message) for n in section.findings] == [("1.1step", None, detail)]
    assert section.resolved[0].cue == "1.1step" and section.resolved[0].at == 0.5 and result.unresolved == 0
    assert main(["-p", str(p.root), "align", "--strict"]) == 0  # A warning, not a finding.
    assert f"! 1.1step: {detail}" in capsys.readouterr().out


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


def test_scene_params_adds_cues_unless_the_section_sets_them(tmp_path):
    from decktalk.model import PageSection
    from decktalk.stages.record import scene_params
    from decktalk.stages.screenshots import screenshot_slides

    cue_times = CueTimes({"01": [CueTime("a", "x", 1.5), CueTime("b", "y", 2.0)]})
    own = PageSection(1, "deck/index.html", "1", params={"theme": "dark"})
    assert scene_params(own, cue_times) == {"theme": "dark", "cues": "a@1.5,b@2.0"}
    assert scene_params(own, None) == {"theme": "dark"}
    fixed = PageSection(1, "deck/index.html", "1", params={"cues": "x@1"})
    assert scene_params(fixed, cue_times) == {"cues": "x@1"}
    assert scene_params(PageSection(2, "deck/index.html", "2"), cue_times) == {}
    p = Project.load(write_project(tmp_path, PAGES_TOML), environ={})
    with pytest.raises(ConfigError, match="exactly one slide"):
        screenshot_slides(p, slides=["1.1", "2.1"], cues=["1.1a"])


def test_scene_url_passes_the_previous_sections_words(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from decktalk.model import PageSection
    from decktalk.stages.record import prev_words_query, scene_url, words_query

    p = Project.load(write_project(tmp_path, PAGES_TOML), environ={})
    (p.root / "deck").mkdir(exist_ok=True)
    (p.root / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    tl = Timeline(
        narration="n.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 3.0, 3.0, 2.5, [Word("one,", 0.7, 1.0), Word("two", 1.5, 1.8)]),
            "02": TimelineSection("B", 3.0, 6.0, 3.0, 5.5, [Word("three", 3.4, 3.8)]),
        },
    )
    tl.save(p.timeline_path)
    first, second = PageSection(1, "deck/index.html", "1"), PageSection(2, "deck/index.html", "2")
    assert prev_words_query(p, first) is None
    assert prev_words_query(p, second) == words_query(p, first) == "one@0.70,two@1.50"
    query = parse_qs(urlsplit(scene_url(p, second, {})).query)
    assert query["words"] == ["three@0.40"] and query["prevwords"] == ["one@0.70,two@1.50"]
    assert "prevwords" not in parse_qs(urlsplit(scene_url(p, first, {})).query)


def test_worst_stall_counts_only_what_a_viewer_sees():
    from decktalk.artifacts import RecordingLog

    recording_log = RecordingLog.__new__(RecordingLog)
    recording_log.frame_gaps = [(None, 900), (0.05, 216), (0.4, 120), (12.8, 132)]
    # The first gap ended under the cover. The second began there and shows for 50 ms.
    assert recording_log.worst_stall_ms == 132
    recording_log.frame_gaps = [(None, 900), (0.05, 216)]
    assert recording_log.worst_stall_ms == 50
    recording_log.frame_gaps = []
    assert recording_log.worst_stall_ms == 0


def test_recording_log_writes_null_for_a_gap_before_the_clock(tmp_path):
    p = tmp_path / "s.json"
    recording_log = RecordingLog(url="u", requested_seconds=1, settle_seconds=0, load_seconds=0, clock_start_seconds=0)
    recording_log.frame_gaps = [(float("-inf"), 900), (None, 400), (0.05, 216)]
    recording_log.save(p)
    text = p.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    # Standard JSON parsers such as JSON.parse and jq reject the -Infinity token.
    data = json.loads(text, parse_constant=lambda token: pytest.fail(f"non-standard JSON token {token}"))
    assert data["frame_gaps"] == [[None, 900], [None, 400], [0.05, 216]]
    again = RecordingLog.load(p)
    assert again is not None and again.frame_gaps == [(None, 900), (None, 400), (0.05, 216)]
    assert again.worst_stall_ms == 50


def test_gap_time_turns_the_page_value_for_a_gap_before_the_clock_into_none():
    # The page reports a gap that ended before narration t=0 at negative infinity, and the recorder
    # is the boundary where that becomes None.
    assert gap_time(float("-inf")) is None
    assert gap_time(float("nan")) is None
    assert gap_time(None) is None
    assert gap_time(0.4) == 0.4
    assert gap_time(0) == 0


def test_worst_stall_treats_a_null_time_as_a_gap_under_the_cover():
    recording_log = RecordingLog(url="u", requested_seconds=1, settle_seconds=0, load_seconds=0, clock_start_seconds=0)
    recording_log.frame_gaps = [(None, 900)]
    assert recording_log.worst_stall_ms == 0
    recording_log.frame_gaps = [(None, 900), (0.05, 216), (12.8, 132)]
    assert recording_log.worst_stall_ms == 132


def test_provider_errors_never_show_the_voice_id(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from decktalk.errors import ProviderError
    from decktalk.providers import _http

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


def test_probe_plan_fits_the_gap_between_close_cues_and_keeps_well_spaced_cues():
    from decktalk.stages.verify import probe_plan, reference_time

    cfg = Settings().verify
    fps = 25
    # The demo's section 1: 1.1four fires 0.56 s after 1.1three, in a run of counting cues.
    cues = {"bowl": 0.86, "ball": 1.76, "count": 3.58, "one": 4.42, "two": 5.11, "three": 5.62, "four": 6.18}
    cues["word"] = 10.52

    def plan(cue: str, end: float = 30.0) -> tuple[list[float], bool]:
        before = reference_time(0.0, cues[cue], False, 0.0, cfg, fps)
        assert before is not None
        others = [t for c, t in cues.items() if c != cue]
        return probe_plan(cues[cue], before, 0.0, end, others, cfg, fps)

    # Both control spans of 1.1four's probes hold an earlier count, so its probe and control fit after 1.1three.
    assert plan("four") == ([0.14], True)
    # The later probe of 1.1count would reach 1.1one's reveal, so it stops where that reveal can begin.
    assert plan("count") == ([0.7], True)
    # A well-spaced cue keeps probe_delays exactly, and so does every cue with no neighbor.
    assert plan("word") == ([0.7, 1.5], False)
    assert plan("word", end=11.5) == ([0.7], False)
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [], cfg, fps) == ([0.7, 1.5], False)
    # A cue within the reference lead is the same reveal, and a gap too short for any probe keeps probe_delays.
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [6.1, 6.3], cfg, fps) == ([0.7, 1.5], False)
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [6.4], cfg, fps) == ([0.7, 1.5], False)


def test_reference_sits_before_an_early_reveal_the_offset_limit_allows():
    import dataclasses

    from decktalk.stages.verify import reference_time

    cfg = Settings().verify
    fps = 25
    # A reveal may land max_offset_frames early, so the reference must sit before that window.
    earliest_allowed = 10.0 - cfg.max_offset_frames / fps
    ref = reference_time(0.0, 10.0, False, 0.0, cfg, fps)
    assert ref is not None and ref <= earliest_allowed - 1.0 / fps
    # A wider limit pushes the reference further back.
    wide = dataclasses.replace(cfg, max_offset_frames=4)
    ref_wide = reference_time(0.0, 10.0, False, 0.0, wide, fps)
    assert ref_wide is not None and ref_wide <= 10.0 - 4 / fps - 1.0 / fps
    # A cue close to the section start still clamps to one frame before the cue.
    assert reference_time(0.0, 0.08, False, 0.0, cfg, fps) is not None


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

    _p, rows = _titled_clip_rows(tmp_path)
    assert [(c.start, c.end, c.title) for c in build_chapters(rows)] == [
        (0.0, 2.0, "Open"),
        (2.0, 7.52, "The edit"),
        (7.52, 9.0, "Close"),
    ]


def test_a_renumbered_take_is_found_by_its_hash_and_moves_without_clobbering(tmp_path):
    from decktalk.model.script import Segment
    from decktalk.stages.narrate import is_cached, reusable_entry, reuse_takes

    audio = tmp_path
    for name, body in [("01-open.mp3", "open"), ("01-open.words.json", "[]"), ("05-x.mp3", "five"),
                       ("05-x.words.json", "[5]"), ("06-x.mp3", "six"), ("06-x.words.json", "[6]")]:  # fmt: skip
        (audio / name).write_text(body, encoding="utf-8")

    def entry(index: int, digest: str) -> Take:
        key = f"{index:02d}"
        slug = "open" if index == 1 else "x"
        return Take(index, "X", f"{key}-{slug}.mp3", f"{key}-{slug}.words.json", digest, 1, 1.0, 1.0)

    previous = Takes("s", "m", "mp3")
    previous.sections = {"01": entry(1, "h1"), "05": entry(5, "h5"), "06": entry(6, "h6")}
    to_four = Segment(4, "X", "x", "five text")
    to_five = Segment(5, "X", "x", "six text")
    assert not is_cached(previous.sections["05"], to_five, "h6", audio)
    assert reusable_entry(previous, to_four, "h5", audio) == ("05", previous.sections["05"])
    assert reusable_entry(previous, to_five, "h6", audio) == ("06", previous.sections["06"])
    assert reusable_entry(previous, to_four, "nope", audio) is None
    # The first spoken section carries the opening silence, so its take never moves in or out.
    assert reusable_entry(previous, Segment(2, "X", "x", "t", first_spoken=True), "h5", audio) is None
    assert reusable_entry(previous, to_four, "h1", audio) is None
    # 05 moves to 04 while 06 moves onto 05's old name, and each file keeps its own take.
    reuse_takes([(previous.sections["05"], to_four), (previous.sections["06"], to_five)], audio)
    assert (audio / "04-x.mp3").read_text(encoding="utf-8") == "five"
    assert (audio / "05-x.mp3").read_text(encoding="utf-8") == "six"
    assert (audio / "05-x.words.json").read_text(encoding="utf-8") == "[6]"
    assert not list(audio.glob(".reuse-*"))


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


# ---- stale measurement ------------------------------------------------------------------------


def _recorded(tmp_path: Path) -> tuple[Project, Path]:
    """A one-page project with a recording and the recording log that `record` writes, not yet measured."""
    root = write_project(tmp_path, "[[section]]\nnumber = 1\npage = 'a.html'\n")
    (root / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    p.recordings_dir.mkdir(parents=True)
    webm = p.recordings_dir / "01.webm"
    webm.write_bytes(b"take one")
    RecordingLog(url="u", requested_seconds=2, settle_seconds=0.5, load_seconds=0.1, clock_start_seconds=1.506).save(
        webm.with_suffix(".json")
    )
    return p, webm


def test_stale_measure_ties_the_measurement_to_one_recording(tmp_path, monkeypatch):
    from decktalk.media import frames
    from decktalk.stages.measure import measure, recording_hash, stale_measure

    p, webm = _recorded(tmp_path)
    log_path = webm.with_suffix(".json")
    name = "build/recordings/01.webm"
    assert stale_measure(webm, RecordingLog.load(log_path), p.root) == (
        f"{name} was never measured, so the cut would trim the recorder's wall-clock estimate of 1.506s"
    )
    assert (
        stale_measure(webm, None, p.root) == f"{name} has no recording log, so `measure` never found its narration t=0"
    )

    monkeypatch.setattr(frames, "frame_stats", lambda path, seconds: [])
    (row,) = measure(p)
    recording_log = RecordingLog.load(log_path)
    assert recording_log is not None and recording_log.t0_seconds == row.t0_seconds
    assert recording_log.t0_hash == recording_hash(webm) and len(recording_log.t0_hash) == 16
    assert stale_measure(webm, recording_log, p.root) is None

    webm.write_bytes(b"take two")  # a new take over the measured one
    assert stale_measure(webm, recording_log, p.root) == f"{name} changed after `measure` read it"


def test_assemble_refuses_a_stale_measurement_with_strict_and_warns_without(tmp_path, monkeypatch, caplog):
    import importlib

    from decktalk.errors import MissingInputError
    from decktalk.stages.measure import recording_hash

    asm = importlib.import_module("decktalk.stages.assemble")
    p, webm = _recorded(tmp_path)
    Timeline(
        narration="narration.mp3",
        total_seconds=2.0,
        sections={"01": TimelineSection("A", 0, 2.0, 2.0, 1.0, [Word("Hi", 0.7, 1.0)])},
        estimated=True,
    ).save(p.timeline_path)
    timeline = p.timeline()
    assert timeline is not None
    render = asm.render_sections
    ran: list[tuple] = []
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: ran.append(args))
    monkeypatch.setattr(asm.ffmpeg, "probe_duration", lambda path: 2.0)

    with pytest.raises(MissingInputError) as err:
        asm.render_sections(p, timeline, strict=True)
    assert str(err.value) == (
        "section 1: STALE MEASUREMENT: build/recordings/01.webm was never measured, so the cut would trim the "
        "recorder's wall-clock estimate of 1.506s. Run `decktalk measure --only 1`, then `decktalk assemble` again."
    )
    assert ran == []

    expected = (
        "section 01: STALE MEASUREMENT: build/recordings/01.webm was never measured, so the cut would trim the "
        "recorder's wall-clock estimate of 1.506s. Every reveal in the section may play early or late. "
        "Run `decktalk measure --only 1`, or pass --strict to stop on this."
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        (row,) = asm.render_sections(p, timeline, strict=False)
    assert row.warning == expected
    assert [r.getMessage() for r in caplog.records] == [expected]

    # The warning reaches the result, next to the mix warnings.
    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: [row])
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
    assert asm.assemble(p, soundscape=False).warnings == [expected]

    log_path = webm.with_suffix(".json")
    recording_log = RecordingLog.load(log_path)
    assert recording_log is not None
    recording_log.t0_seconds, recording_log.t0_hash = 1.44, recording_hash(webm)
    recording_log.save(log_path)
    (measured,) = render(p, timeline, strict=True)
    assert measured.warning is None and "t0 1.44s trimmed" in measured.note


def test_verify_and_assemble_ignore_a_leftover_section_video(tmp_path, monkeypatch, caplog):
    """A sections/04.mp4 left after sections were renumbered is not counted as a section."""
    import importlib

    from decktalk.stages.verify import verify

    asm = importlib.import_module("decktalk.stages.assemble")
    p = _verify_project(tmp_path, monkeypatch, {"01": "a@1.0"})
    (p.sections_dir / "04.mp4").write_bytes(b"x")
    (p.out_dir / "t-20260101-0000.mp4").write_bytes(b"x")  # files outside build/sections are not section videos
    assert p.stray_section_videos() == [p.sections_dir / "04.mp4"]

    with caplog.at_level("WARNING", logger="decktalk"):
        result = verify(p, checks=[])
    assert [s.key for s in result.starts] == ["01", "02"] and result.total_seconds == 10.0
    assert [r.getMessage() for r in caplog.records] == [
        "build/sections/04.mp4 is not a section in decktalk.toml, so verify ignores it. "
        "Delete the file if an earlier build left it."
    ]

    (p.root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    Timeline(
        narration="narration.mp3",
        total_seconds=5.0,
        sections={"01": TimelineSection("A", 0, 5.0, 5.0, 1.0, [Word("Hi", 0.7, 1.0)])},
        estimated=True,
    ).save(p.timeline_path)
    rows = [asm.RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 5.0, "page")]
    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: rows)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
    assert asm.assemble(p, soundscape=False).warnings == [
        "build/sections/04.mp4 is not a section in decktalk.toml, so assemble ignores it. "
        "Delete the file if an earlier build left it."
    ]


# ---- silent runs over voiced takes ------------------------------------------------------------


def _voiced(tmp_path: Path) -> tuple[Project, Path, Path]:
    """A project whose build/narration holds one voiced take."""
    root = write_project(tmp_path, "[[section]]\nnumber = 1\npage = 'a.html'\n")
    (root / "script.md").write_text("## 1. Open\n\nHello there.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    p.narration_dir.mkdir(parents=True)
    take = p.narration_dir / "01-open.mp3"
    take.write_bytes(b"voiced take")
    take_index = Takes(script="script.md", model="eleven_v3", output_format="mp3_44100_128")
    take_index.sections["01"] = Take(
        index=1, chapter="Open", file=take.name, words_file="01-open.words.json", hash="3f2a9c0d1e2b4a5f",
        word_count=2, estimated_seconds=1.0, duration_seconds=2.3,
    )  # fmt: skip
    take_index.save(p.takes_path)
    return p, take, p.takes_path


VOICED_REFUSAL = (
    "build/narration/takes.json holds voiced takes for sections 01. A build without voice writes click tracks over "
    "those mp3 files and replaces the take index, so the next voiced build voices every section again and spends "
    "credits on all of them. Rehearse the build without voice in a copy of the project, or pass --force to replace "
    "the voiced takes."
)


def test_narrate_without_voice_refuses_voiced_takes_unless_forced(tmp_path, monkeypatch):
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import narrate

    p, take, takes_path = _voiced(tmp_path)
    before = takes_path.read_bytes()
    with pytest.raises(ConfigError) as err:
        narrate(p, silent=True)
    assert str(err.value) == VOICED_REFUSAL
    assert take.read_bytes() == b"voiced take" and takes_path.read_bytes() == before

    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    result = narrate(p, silent=True, force=True)
    assert result.synthesized == ["01"] and take.read_bytes() == b"clicks"
    take_index = Takes.load(takes_path)
    assert take_index is not None and take_index.estimated and take_index.sections["01"].hash == "silent"
    assert narrate(p, silent=True).synthesized == ["01"]  # a silent take index is never refused


@pytest.mark.parametrize("command", [["build", "--no-voice"], ["narrate", "--no-voice"]])
def test_cli_run_without_voice_over_voiced_takes_exits_1_with_the_risk(tmp_path, capsys, command):
    p, take, _manifest = _voiced(tmp_path)
    assert main([*command, "-p", str(p.root)]) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == f"error: {VOICED_REFUSAL}"
    assert take.read_bytes() == b"voiced take"
    assert build_parser().parse_args(["build", "--no-voice", "--force"]).force


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


def test_timeline_joins_each_section_lead_before_its_take(tmp_path, monkeypatch):
    """lead_seconds is silence in narration.mp3 before the take. The words and cues move, and the take does not."""
    from decktalk.media import audio, ffmpeg
    from decktalk.model.markers import Marker
    from decktalk.stages.align import align
    from decktalk.stages.assemble import resolve_marker_time
    from decktalk.stages.narrate import build_timeline

    p, take_index = _two_takes(tmp_path, "lead_seconds = 1.25\n")
    joined: dict = {}
    monkeypatch.setattr(
        audio, "concat_audio", lambda files, out, **kw: joined.update(files=[f.name for f in files], leads=kw["leads"])
    )
    monkeypatch.setattr(
        ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 7.25 if path.name == "narration.mp3" else 3.0
    )
    tl = build_timeline(p, take_index, p.script_sections()[1])
    assert joined == {"files": ["01-open.mp3", "02-close.mp3"], "leads": [0.0, 1.25]}
    one, two = tl.sections["01"], tl.sections["02"]
    assert (one.start, one.end, one.duration, one.lead_seconds) == (0.0, 3.0, 3.0, 0.0)
    assert (two.start, two.end, two.duration, two.lead_seconds) == (3.0, 7.25, 4.25, 1.25)
    assert [(w.start, w.end) for w in two.words] == [(4.75, 5.15), (5.25, 5.85)]
    assert two.speech_end == 5.85 and tl.total_seconds == 7.25
    saved = Timeline.load(p.timeline_path)
    assert saved is not None and saved.sections["02"].lead_seconds == 1.25

    # Cue times count from the section start, so the lead moves each word cue, and $start stays at 0.
    cues = [{"cue": "2.0", "on": "$start"}, {"cue": "2.1", "on": "b"}, {"cue": "2.2", "on": "$end"}]
    (p.root / "cues.json").write_text(json.dumps({"sections": {"2": {"cues": cues}}}), encoding="utf-8")
    result = align(p)
    assert result.cue_times.times("02") == {"2.0": 0.0, "2.1": 2.25, "2.2": 2.85}
    assert result.sections[0].speech_end == 2.85
    marker = Marker(name="turn", section=2, on="b")
    assert resolve_marker_time(marker, {"02": 10.0}, take_index, p.narration_dir, {"02": 1.25}) == pytest.approx(12.25)
    assert resolve_marker_time(marker, {"02": 10.0}, take_index, p.narration_dir) == pytest.approx(11.0)


def test_a_section_tail_seconds_replaces_min_tail_seconds(tmp_path, monkeypatch):
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import ensure_tail, narrate, section_config

    root = write_project(
        tmp_path,
        "[narration]\nmin_tail_seconds = 0.7\nopening_silence_seconds = 0\n[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\npage = 'a.html'\ntail_seconds = 2.5\n",
    )
    (root / "script.md").write_text(
        "## 1. One\n\nSame words here.\n\n## 2. Two\n\nSame words here.\n", encoding="utf-8"
    )
    p = Project.load(root, environ={})
    lengths: dict[str, float] = {}

    def clicks(path, duration, times, **kw):
        lengths[Path(path).name] = duration
        Path(path).write_bytes(b"clicks")

    monkeypatch.setattr(audio, "write_clicks", clicks)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: lengths[Path(path).name])
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: lengths.get(Path(path).name, 0.0))
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: None)
    narrate(p, silent=True)
    # The same words take the same time, so the click tracks differ by the tails alone.
    assert lengths["02-two.mp3"] - lengths["01-one.mp3"] == pytest.approx(1.8)

    padded: list[tuple[str, float]] = []
    monkeypatch.setattr(audio, "trailing_silence", lambda path: 1.0)
    monkeypatch.setattr(audio, "pad_tail", lambda path, seconds, bitrate: padded.append((Path(path).name, seconds)))
    for seg in p.script_sections()[1]:
        ensure_tail(p.narration_dir / seg.filename, section_config(p, seg))
    assert padded == [("02-two.mp3", 1.55)]  # a 1.0 s tail passes 0.7 but not 2.5, which it reaches plus the slack


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
    """The scaffold voiced by a fake provider: 01 and 02 cached, 03 changed, 09's take under old key 07, the rest new.

    Returns the project and the path of every file the plan must leave alone, with its bytes' hash.
    """
    import hashlib

    from decktalk.artifacts import write_words
    from decktalk.model.script import PUNCT
    from decktalk.providers.speech import register_speech_provider
    from decktalk.scaffold import init
    from decktalk.stages.narrate import text_hash

    class PlanVoice:
        name = "plan-voice"

        def speak(self, request):
            raise AssertionError("a plan sent a request")

        def cache_key(self, request):
            return "plan-voice"

    register_speech_provider("plan-voice", lambda project: PlanVoice())
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
    takes = {"01": ("01", "real"), "02": ("02", "real"), "03": ("03", "stale"), "07": ("09", "real")}
    for key, (source, kind) in takes.items():
        seg = spoken[source]
        name = f"{key}-{seg.slug}"
        (p.narration_dir / f"{name}.mp3").write_bytes(f"take {key}".encode())
        tokens = [t.strip(PUNCT) for t in seg.spoken.split()]
        write_words(
            p.narration_dir / f"{name}.words.json",
            [Word(t, round(i * 0.4, 3), round(i * 0.4 + 0.3, 3)) for i, t in enumerate(tokens)],
        )
        take_index.sections[key] = Take(
            index=int(key), chapter=seg.title, file=f"{name}.mp3", words_file=f"{name}.words.json",
            hash=text_hash(seg, cfg, "plan-voice", settings) if kind == "real" else "0123456789abcdef",
            word_count=seg.word_count, estimated_seconds=seg.estimated_seconds(cfg),
            duration_seconds=len(tokens) * 0.4 + 1.3,
            spoken=seg.spoken,
        )  # fmt: skip
    take_index.save(p.takes_path)
    files = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return p, files


def _unchanged(p: Project, files: dict[str, str]) -> bool:
    import hashlib

    now = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return now == files


def test_narrate_dry_run_json_lists_each_take_with_its_characters_and_moves(tmp_path, monkeypatch, capsys):

    p, files = _planned_scaffold(tmp_path, monkeypatch)
    cfg = p.settings.narration
    assert main(["narrate", "--dry-run", "--json", "-p", str(p.root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "narrate" and doc["ok"] is True and doc["findings"] == {"certain": 0, "uncertain": 0}
    plan = doc["narrate"]
    assert plan["voice"]["provider"] == "plan-voice" and plan["note"] is None
    rows = {r["key"]: r for r in plan["sections"]}
    assert {k: (r["status"], r["moved_from"]) for k, r in rows.items()} == {
        "01": ("cached", None),
        "02": ("cached", None),
        "03": ("synthesize", None),
        "04": ("synthesize", None),
        "06": ("synthesize", None),
        "08": ("synthesize", None),
        "09": ("moved", "07"),
    }
    assert rows["03"]["reason"] == "the text, voice, model, or voice settings changed"
    assert rows["04"]["reason"] == "no take yet"
    assert rows["09"]["reason"] == "the same text as section 7"
    segs = {s.key: s for s in p.script_sections()[1]}
    for key, row in rows.items():
        assert row["characters_sent"] == len(segs[key].tts_text(cfg)) == len(row["text"])
        assert row["characters_spoken"] == len(segs[key].spoken)
    voiced = ["03", "04", "06", "08"]
    assert plan["totals"] == {
        "synthesize": 4,
        "cached": 2,
        "moved": 1,
        "unknown": 0,
        "characters_sent": sum(len(segs[k].tts_text(cfg)) for k in voiced),
        "characters_spoken": sum(len(segs[k].spoken) for k in voiced),
    }
    assert _unchanged(p, files), "a dry run wrote to build/narration"

    assert main(["narrate", "--dry-run", "-p", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert f"voice 4 section(s): {plan['totals']['characters_sent']} characters sent" in out
    assert "2 cached, 1 moved." in out
    with pytest.raises(SystemExit) as exc:
        main(["narrate", "--json", "-p", str(p.root)])
    assert exc.value.code == 2


def test_narrate_dry_run_without_a_voice_key_still_plans_what_it_can(tmp_path, monkeypatch, capsys):
    p, _files = _planned_scaffold(tmp_path, monkeypatch)
    toml = p.root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("provider = 'plan-voice'\n", ""), encoding="utf-8")
    assert main(["narrate", "--dry-run", "--json", "-p", str(p.root)]) == 0
    plan = json.loads(capsys.readouterr().out)["narrate"]
    assert "ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID not set" in plan["note"]
    # Without a key, a section with a voiced take cannot be checked, and a section with no take still needs one.
    voiced = p.takes().sections
    keys = [r["key"] for r in plan["sections"]]
    assert any(k in voiced for k in keys) and any(k not in voiced for k in keys), "the scaffold needs both kinds"
    for r in plan["sections"]:
        expected = ("unknown",) if r["key"] in voiced else ("synthesize", "no take yet")
        assert (r["status"], r["reason"])[: len(expected)] == expected, r
    assert plan["totals"]["synthesize"] == sum(k not in voiced for k in keys)
    sent = sum(r["characters_sent"] for r in plan["sections"] if r["key"] not in voiced)
    assert plan["totals"]["characters_sent"] == sent
    p.takes_path.unlink()
    assert main(["narrate", "--dry-run", "--json", "-p", str(p.root)]) == 0
    plan = json.loads(capsys.readouterr().out)["narrate"]
    assert {(r["status"], r["reason"]) for r in plan["sections"]} == {("synthesize", "no take yet")}
    assert plan["totals"]["synthesize"] == 7


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
    from decktalk.verdicts import SkipReason

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
        voice={}, narration=Settings().narration, takes=[], note=None, estimated=[],
        align=AlignResult(cue_times=CueTimes(), sections=[], unresolved=1, estimated=True, unknown=2),
        cues=[CueEstimate("1:a", 1.0, "1.1", 0.2, Verdict.THIN_CHANGE),
              CueEstimate("1:b", 2.0, "1.1", 0.0, Verdict.NO_CHANGE),
              CueEstimate("1:c", 3.0, "1.1", 4.0, Verdict.CHANGED),
              CueEstimate("1:d", 0.0, None, None, Verdict.SKIPPED)],
        seams=[SeamEstimate("02", 3.1, Verdict.POP_AT_CUT), SeamEstimate("03", 0.0, Verdict.OK)],
    )  # fmt: skip
    assert result.findings() == Findings(certain=5, uncertain=1)
    assert result.findings(allow_unknown_cues=True) == Findings(certain=3, uncertain=1)


def test_preflight_resolves_cues_on_the_words_each_section_will_have(tmp_path, monkeypatch, capsys):
    from decktalk.artifacts import read_words
    from decktalk.stages.align import find_phrase
    from decktalk.stages.preflight import preflight

    p, files = _planned_scaffold(tmp_path, monkeypatch)
    result = preflight(p, frames=False)
    assert {t.segment.key: t.status for t in result.takes} == {
        "01": "cached", "02": "cached", "03": "synthesize", "04": "synthesize",
        "06": "synthesize", "08": "synthesize", "09": "moved",
    }  # fmt: skip
    assert result.estimated == ["03", "04", "06", "08"]
    assert result.align.unresolved == 0 and result.align.unknown == 0
    resolved = {s.key: {r.cue: r.at for r in s.resolved} for s in result.align.sections}
    # A cached take and a moved take resolve on their own words, which the fixture spaced 0.4 s apart.
    open_words = read_words(p.narration_dir / "01-open.words.json")
    assert resolved["01"]["1.1bowl"] == open_words[find_phrase(open_words, "bowl")].start == 0.4
    close_words = read_words(p.narration_dir / "07-close.words.json")
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
    assert [t["moved_from"] for t in payload["takes"] if t["status"] == "moved"] == ["07"]
    assert main(["preflight", "--no-frames", "-p", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert "2 cached, 1 moved." in out and "frames skipped (--no-frames)" in out

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
    first, second = words(p)
    assert (first.key, first.title, first.lead_seconds, first.duration) == ("01", "Open", 0.5, 3.0)
    assert first.estimated is False
    assert first.words == [Word("Hello", 0.6, 0.9), Word("there", 1.0, 1.4)]
    assert first.texts == ["Hello,", "there."]
    # Section 2 starts at 3.0 s in narration.mp3, and it has no take_index entry, so it keeps the voice's spelling.
    assert second.words == [Word("Bye", 0.2, 0.5), Word("now", 0.6, 1.1)]
    assert second.texts == ["Bye", "now"]
    assert [s.key for s in words(p, only=[2])] == ["02"]
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
