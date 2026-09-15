"""Unit tests that need neither ffmpeg nor Chromium nor an API key."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import decktalk
from decktalk import ConfigError, Project, load_settings
from decktalk.artifacts import (
    Beats,
    Manifest,
    ManifestSegment,
    Sidecar,
    Timeline,
    TimelineSection,
    Word,
    parse_beats_string,
)
from decktalk.cli import build_parser, main
from decktalk.config import Settings
from decktalk.stages.assemble import cut_summary, fade_flags, timeline_targets
from decktalk.stages.beats import Cue, find_phrase, resolve_cue
from decktalk.stages.narrate import estimated_words, parse_script, strip_markdown

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
    assert s.video.crf == 23  # env beats toml
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
        (
            "[[section]]\nnumber = 1\npage = 'a.html'\nhold_seconds = 2\n[[section]]\nnumber = 2\npage = 'a.html'\n",
            "hold_seconds",
        ),
        ("[[section]]\nnumber = 1\npage = 'a.html'\nextra_seconds = 'lots'\n", "must be"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\n[transition]\ndips = [[1, 9]]\n", "does not exist"),
        ("[[section]]\nnumber = 1\npage = 'a.html'\n[bogus]\nx = 1\n", "unknown table"),
        (
            "[[section]]\nnumber = 1\npage = 'a.html'\n[[mix.sfx]]\nfile = 'x.mp3'\nsection = 1\n",
            "missing required key 'cue'",
        ),
        (
            "[[section]]\nnumber = 1\npage = 'a.html'\nhold_seconds = 2\n[[section]]\nnumber = 2\nclip = 'c.mp4'\n"
            "[[section]]\nnumber = 3\npage = 'a.html'\n",
            r"hold_seconds is allowed only on the last page section \(3\)",
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
    assert p.env("ELEVENLABS_API_KEY") == ""
    assert p.env("ELEVENLABS_VOICE_ID") == "abc"
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        p.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")


def test_project_warns_about_unknown_keys_and_suggests_the_closest(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    toml = (
        "[project]\nname = 't'\nscirpt = 'script.md'\n"
        "[voice]\nstabilty = 0.4\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\nscnee = 2\nslate_seconds = 3\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nzebra = 1\n"
        "[mix.loudnorm]\nLRAA = 9\n"
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
        "decktalk.toml: [mix.loudnorm]: ignoring unknown key 'LRAA' (did you mean 'LRA'?)",
        "decktalk.toml: [soundscape.music]: ignoring unknown key 'second' (did you mean 'seconds'?)",
        "decktalk.toml: [video]: ignoring unknown key 'presett' (did you mean 'preset'?)",
    ]
    # A warning, not an error: the load succeeds and every misspelled key keeps its default.
    assert p.voice.stability == 0.55 and p.settings.video.preset == "medium" and p.soundscape.music.seconds == 360


def test_user_settings_file_warns_about_unknown_keys(tmp_path, caplog):
    from decktalk.config import read_user_toml

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
    assert [s.title for s in p.sections] == titles


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
    enc = asm._Encoder(p.settings.video)
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


def test_manifest_roundtrip_sorts_and_totals(tmp_path):
    m = Manifest(script="s.md", model="m", output_format="mp3")
    m.segments["02"] = ManifestSegment(2, "B", "02-b.mp3", "02-b.words.json", "h", 3, 1.0, 2.5)
    m.segments["01"] = ManifestSegment(1, "A", "01-a.mp3", "01-a.words.json", "h", 3, 1.0, 1.5)
    path = tmp_path / "manifest.json"
    m.save(path)
    back = Manifest.load(path)
    assert back is not None and list(back.segments) == ["01", "02"] and back.total_seconds == 4.0
    assert not list(tmp_path.glob(".*.tmp"))  # atomic write left nothing behind


def test_timeline_and_beats_roundtrip(tmp_path):
    tl = Timeline(
        narration="n.mp3",
        total_seconds=3.0,
        sections={"01": TimelineSection("A", 0, 3.0, 3.0, 2.8, [Word("hi", 0.1, 0.4)])},
    )
    tl.save(tmp_path / "t.json")
    back = Timeline.load(tmp_path / "t.json")
    assert back is not None and back.span("01") == 3.0 and back.sections["01"].words[0].word == "hi"
    b = Beats({"01": {"a": 1.5, "panel:bought": 2.0}})
    b.save(tmp_path / "b.json")
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {"01": "a@1.5,panel:bought@2.0"}
    assert Beats.load(tmp_path / "b.json").get("01", "panel:bought") == 2.0
    assert parse_beats_string("a@1.5,bad,x@y") == {"a": 1.5}


def test_sidecar_trim_prefers_measured(tmp_path):
    s = Sidecar(url="u", requested_seconds=5, settle_seconds=0.5, load_seconds=0.1, lead_seconds=0.7)
    assert s.trim_seconds == 0.7
    s.lead_in_seconds = 0.2
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
    assert "[VENUE]" in strip_markdown("At [VENUE] tonight. [not spoken]", direction_break_seconds=0.7)


def test_estimated_words_span_the_duration():
    seg = parse_script("## 1. A\n\none two three four five")[0]
    seg.lead_break = True
    words = estimated_words(seg, 5.0, Settings().narration)
    assert [w.word for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0].start == 0.7 and words[-1].end < 5.0 - 0.35


# ---- beats ---------------------------------------------------------------------------------


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
    for name in ("AssembleResult", "BuildResult", "Voice", "SpeechProvider", "register", "__version__"):
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
    from decktalk.stages.narrate import BREAK_RE

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


def test_cues_load_with_cue_or_step_keys(tmp_path):
    from decktalk.stages.beats import load_cues

    root = write_project(tmp_path)
    (root / "cues.json").write_text(
        json.dumps({"sections": {"1": {"cues": [{"cue": "1.1a", "on": "$start"}, {"step": "1.1b", "on": "hello"}]}}}),
        encoding="utf-8",
    )
    project = Project.load(root, environ={})
    (section,) = load_cues(project)
    assert [c.step for c in section.cues] == ["1.1a", "1.1b"]
    assert [c.on for c in section.cues] == ["$start", "hello"]


def test_sidecar_warnings_default_and_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"url": "u", "requested_seconds": 1, "settle_seconds": 0, "load_seconds": 0, "lead_seconds": 0}),
        encoding="utf-8",
    )
    old = Sidecar.load(p)
    assert old is not None and old.warnings == []
    old.warnings = ["KaTeX did not load within 5 s, so [data-tex] elements stay plain text"]
    old.save(p)
    again = Sidecar.load(p)
    assert again is not None and again.warnings == old.warnings


def _fake_katex_cache(cache_root: Path) -> Path:
    from decktalk.scaffold import KATEX_VERSION

    d = cache_root / "katex" / KATEX_VERSION
    (d / "fonts").mkdir(parents=True)
    (d / "katex.min.js").write_text("window.katex = {};", encoding="utf-8")
    (d / "katex.min.css").write_text(".katex{}", encoding="utf-8")
    (d / "fonts" / "KaTeX_Main-Regular.woff2").write_bytes(b"\0")
    return d


def test_init_vendors_cached_katex(tmp_path, monkeypatch):
    from decktalk.scaffold import init, katex_cached

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "cache"))
    _fake_katex_cache(tmp_path / "cache")
    assert katex_cached() is not None
    root = init(tmp_path / "proj", name="proj")
    assert (root / "deck" / "katex" / "katex.min.js").exists()
    assert (root / "deck" / "katex" / "fonts" / "KaTeX_Main-Regular.woff2").exists()
    html = (root / "deck" / "index.html").read_text(encoding="utf-8")
    assert "./katex/katex.min.css" in html and "./katex/katex.min.js" in html
    assert "cdnjs" not in html and "__KATEX__" not in html


def test_init_falls_back_to_cdn_with_a_warning(tmp_path, monkeypatch, caplog):
    from decktalk.scaffold import init, katex_cached

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    assert katex_cached() is None
    with caplog.at_level("WARNING", logger="decktalk.scaffold"):
        root = init(tmp_path / "proj", name="proj")
    assert not (root / "deck" / "katex").exists()
    html = (root / "deck" / "index.html").read_text(encoding="utf-8")
    assert "cdnjs.cloudflare.com/ajax/libs/KaTeX" in html and "__KATEX__" not in html
    assert any("KaTeX is not cached" in r.getMessage() for r in caplog.records)


def test_fetch_katex_unpacks_only_what_the_deck_needs(tmp_path, monkeypatch):
    import io
    import zipfile

    from decktalk import scaffold

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("katex/katex.min.js", "js")
        zf.writestr("katex/katex.min.css", "css")
        zf.writestr("katex/katex.mjs", "not needed")
        zf.writestr("katex/contrib/auto-render.min.js", "not needed")
        zf.writestr("katex/fonts/KaTeX_Main-Regular.woff2", "font")
        zf.writestr("katex/fonts/", "")

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(scaffold.urllib.request, "urlopen", lambda url, timeout: Response(buf.getvalue()))
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "cache"))
    dest = scaffold.fetch_katex()
    assert dest == scaffold.katex_cache_dir()
    assert sorted(p.name for p in dest.iterdir()) == ["fonts", "katex.min.css", "katex.min.js"]
    assert (dest / "fonts" / "KaTeX_Main-Regular.woff2").read_text(encoding="utf-8") == "font"
    assert scaffold.katex_cached() == dest


def test_init_copies_every_file_of_the_template_deck(tmp_path, monkeypatch):
    """Every page and asset under the template's deck/ arrives, with placeholders filled only in HTML."""
    from decktalk.scaffold import init, package_file

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    src = package_file("template/deck")
    wanted = {f.relative_to(src) for f in src.rglob("*") if f.is_file() and not f.name.startswith(".")}
    assert Path("index.html") in wanted
    got = {f.relative_to(root / "deck") for f in (root / "deck").rglob("*") if f.is_file()}
    assert wanted <= got and got - wanted == {Path("decktalk-runtime.js")}
    for rel in wanted:
        if rel.suffix == ".html":
            html = (root / "deck" / rel).read_text(encoding="utf-8")
            assert "__NAME__" not in html and "__KATEX__" not in html, rel
        else:
            assert (root / "deck" / rel).read_bytes() == (src / rel).read_bytes(), rel
    assert not (root / "deck" / "vendor").exists()


def test_template_ids_agree_across_page_cues_and_script(tmp_path, monkeypatch):
    """Every cue id in cues.json is named in the page, and every phrase is in its section."""
    from decktalk.scaffold import init
    from decktalk.stages.beats import load_cues, unknown_cue_ids

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    project = Project.load(root, environ={})
    specs = load_cues(project)
    assert unknown_cue_ids(project, specs) == []
    segments = {s.index: s for s in parse_script((root / "script.md").read_text(encoding="utf-8"))}
    for section in specs:
        words = [Word(w, i, i + 1) for i, w in enumerate(segments[section.number].spoken.split())]
        for cue in section.cues:
            if not cue.on.startswith("$"):
                assert find_phrase(words, cue.on) is not None, (
                    f"cue {cue.step}: {cue.on!r} is not in section {section.number}"
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
    from decktalk.artifacts import CAPTION_MAX_CHARS, caption_cues

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
    """The demo film's words. The old breaks put "step." at the start of a line after "for every"."""
    from decktalk.artifacts import caption_cues

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
    """The demo film's words. The old breaks left "of chips." as a cue of its own."""
    from decktalk.artifacts import caption_cues

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
    from decktalk.artifacts import CAPTION_MAX_CHARS, caption_cues

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
    from decktalk.artifacts import caption_cues
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
    from decktalk.artifacts import CaptionCue, Chapter, ffmetadata_escape, write_chapters, write_srt, write_vtt

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
    from decktalk.stages.assemble import RenderedSection, build_chapters, mix_input_args, output_paths, plan_mix

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
    plan = plan_mix(p, rows, tl, nomix=True)
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
    paths = output_paths(p)
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
    plan = plan_mix(p, rows, tl, nomix=True)
    assert plan.total == 9.0
    narration = str(p.audio_dir / "narration.mp3")
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
    # The underscore ducks under each section where it plays, and under the clip.
    music = tmp_path / "music.mp3"
    music.write_bytes(b"x")
    p.mix = type(p.mix)(underscore=str(music))
    ducked = plan_mix(p, rows, tl, nomix=False).filter
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
    from decktalk.media import ffmpeg as ffmpeg_module
    from decktalk.stages.verify import click_offset_ms

    calls: list[tuple[float, float]] = []

    def fake_span(path, start, seconds, *, sample_rate=48000):
        calls.append((round(start, 3), round(seconds, 3)))
        return [0] * 100 + [2000] + [0] * 100

    monkeypatch.setattr(ffmpeg_module, "pcm_span", fake_span)
    assert click_offset_ms(Path("f.mp4"), 10.0, 0.25) is not None
    assert click_offset_ms(Path("f.mp4"), 5.1, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 8.9, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 9.5, 0.25, floor=5.0, ceiling=9.0) is None
    assert calls == [(9.75, 0.5), (5.0, 0.35), (8.65, 0.35)]


def test_loudness_problems_report_peaks_and_missed_targets(tmp_path):
    from decktalk.media.ffmpeg import Loudness
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
    from decktalk.artifacts import Word, display_words

    words = [Word("welcome", 0, 1), Word("this", 1, 2), Word("is", 2, 3), Word("two", 3, 4), Word("x", 4, 5)]
    text = "Welcome. This is two x."
    out = display_words(words, text)
    assert [w.word for w in out] == ["Welcome.", "This", "is", "two", "x."]
    assert out[0].start == 0 and out[-1].end == 5
    # An alignment that cannot be made returns the words untouched.
    assert [w.word for w in display_words(words, "completely different text here")] == [w.word for w in words]


# ---- per-machine settings file ------------------------------------------------------------------


def test_user_settings_sit_between_defaults_and_the_project(tmp_path, monkeypatch):
    from decktalk.config import load_settings, read_user_toml, user_config_path

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


# ---- doctor and runtime ------------------------------------------------------------------------


def test_doctor_reports_missing_ffmpeg_without_fetching(tmp_path, monkeypatch):
    import sys

    from static_ffmpeg import run as static_run

    from decktalk import scaffold
    from decktalk.media import ffmpeg as ffmpeg_module

    def fetch(*args, **kwargs):
        raise AssertionError("doctor must not download ffmpeg")

    monkeypatch.setattr(static_run, "get_or_fetch_platform_executables_else_raise", fetch)
    monkeypatch.setattr(static_run, "get_platform_dir", lambda: str(tmp_path / "bin" / "nowhere"))
    monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda name: None)
    monkeypatch.delenv("DECKTALK_FFMPEG", raising=False)
    monkeypatch.delenv("DECKTALK_FFPROBE", raising=False)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # keeps the test free of Chromium
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    rows = {name: (ok, detail) for name, ok, detail in scaffold.doctor()}
    assert rows["ffmpeg"] == (False, "not fetched yet and none on PATH  -> run `decktalk setup`")
    assert "ffprobe" not in rows
    # Binaries already on disk are reported without asking static-ffmpeg for them either.
    exe_dir = tmp_path / "bin" / "nowhere"
    exe_dir.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe", "installed.crumb"):
        (exe_dir / name).write_text("", encoding="utf-8")
    rows = {name: (ok, detail) for name, ok, detail in scaffold.doctor()}
    assert rows["ffmpeg"] == (True, str(exe_dir / "ffmpeg"))
    assert rows["ffprobe"] == (True, str(exe_dir / "ffprobe"))


def test_runtime_says_wrote_on_first_copy_and_updated_after(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    from decktalk.scaffold import RUNTIME_FILE, init

    root = init(tmp_path / "p", name="p")
    (root / "deck" / RUNTIME_FILE).unlink()
    assert main(["runtime", "-p", str(root)]) == 0
    assert capsys.readouterr().out == f"wrote {root / 'deck' / RUNTIME_FILE}\n"
    assert main(["runtime", "-p", str(root)]) == 0
    assert capsys.readouterr().out == f"updated {root / 'deck' / RUNTIME_FILE}\n"


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


def test_sidecar_verdicts_flag_page_errors_and_bad_tex(tmp_path):
    from decktalk.artifacts import Sidecar
    from decktalk.config import AlignConfig
    from decktalk.stages.measure import sidecar_verdicts

    side = Sidecar(url="x", requested_seconds=1, settle_seconds=0, load_seconds=0, lead_seconds=0)
    assert sidecar_verdicts(side, AlignConfig()) == []
    assert sidecar_verdicts(None, AlignConfig()) == []
    side.page_errors = ["ReferenceError: nope is not defined (index.html:5)"]
    side.warnings = [
        'data-tex could not be parsed: "\\frac{1}" (write \\\\ for every backslash inside a template literal)'
    ]
    side.frame_gaps = [(1.0, 400)]
    assert sidecar_verdicts(side, AlignConfig()) == ["PAGE ERROR", "KATEX?", "STALLED 400ms"]
    side.save(tmp_path / "s.json")
    again = Sidecar.load(tmp_path / "s.json")
    assert again is not None and again.page_errors == side.page_errors


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


def test_assemble_skips_loudnorm_on_an_estimated_timeline(tmp_path, monkeypatch, caplog):
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
    rows = [asm.RenderedSection(p.sections[0], p.out_dir / "01-section.mp4", 2.0, "page")]

    def write_last(*args):
        Path(args[-1]).write_bytes(b"x")

    def no_loudness(*args):
        raise AssertionError("a silent build must not be normalized")

    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: rows)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm, "normalize_loudness", no_loudness)
    monkeypatch.setattr(asm.ffmpeg, "run", write_last)
    monkeypatch.setattr(asm.ffmpeg, "probe_duration", lambda path: 2.0)
    with caplog.at_level("INFO", logger="decktalk.stages.assemble"):
        result = asm.assemble(p, nomix=True)
    assert result.loudness is None and result.warnings == []
    assert any(
        r.getMessage() == "[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, "
        "and the clicks stay at -24 dBFS for the a/v check"
        for r in caplog.records
    )


def test_reference_time_skips_the_fade_and_keeps_the_lead():
    from decktalk.config import VerifyConfig
    from decktalk.stages.verify import reference_time

    cfg = VerifyConfig()  # lead_seconds 0.1
    # The lead clears a reveal that lands max_offset_frames (2) early: (2 + 1.5) / 25 = 0.14 s.
    assert reference_time(10.0, 2.0, False, 0.16, cfg, 25) == 11.86
    assert reference_time(10.0, 0.05, False, 0.16, cfg, 25) == 10.0  # the section's first frame, a frame early
    assert reference_time(10.0, 0.2, True, 0.16, cfg, 25) == 10.16  # the first frame after the fade-in
    assert reference_time(10.0, 0.15, True, 0.16, cfg, 25) is None  # the cue sits inside the fade-in
    assert reference_time(10.0, 0.0, False, 0.16, cfg, 25) is None  # a $start cue has no frame before it


def _verify_project(tmp_path, monkeypatch, beats: dict[str, str], cues: dict | None = None, change: float = 0.0):
    """A project with sections 01 and 02 assembled and every ffmpeg measurement replaced."""
    from decktalk.media import ffmpeg as ffmpeg_module

    root = write_project(tmp_path, PAGES_TOML)
    p = Project.load(root, environ={})
    p.out_dir.mkdir(parents=True)
    for key in ("01", "02"):
        (p.out_dir / f"{key}-section.mp4").write_bytes(b"x")
    p.final.write_bytes(b"x")
    p.audio_dir.mkdir(parents=True)
    p.beats_path.write_text(json.dumps(beats), encoding="utf-8")
    if cues is not None:
        (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    monkeypatch.setattr(ffmpeg_module, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(ffmpeg_module, "luma_at", lambda path, t, crop=None: (100.0, 200.0))
    monkeypatch.setattr(ffmpeg_module, "changed_pixels_percent", lambda path, t1, t2, **kw: change)
    monkeypatch.setattr(ffmpeg_module, "changed_series", lambda *a, **kw: [])
    return p


def test_verify_default_checks_come_from_beats_json(tmp_path, monkeypatch):
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
    from decktalk.stages.beats import load_cues
    from decktalk.stages.verify import verify

    cues = {"1": {"cues": [{"cue": "a", "on": "hello", "verify": False}, {"cue": "b", "on": "there"}]}}
    p = _verify_project(tmp_path, monkeypatch, {"01": "a@1.0,b@2.0"}, cues=cues)
    assert [c.verify for c in load_cues(p)[0].cues] == [False, True]
    a, b = verify(p).cues
    assert (a.verdict, a.reason) == ("skipped", "OPTED_OUT") and b.verdict == "NO CHANGE"
    (named,) = verify(p, checks=["1:a"]).cues  # A cue named on purpose is measured anyway.
    assert named.verdict == "NO CHANGE" and named.reason is None
    bad = {"sections": {"1": {"cues": [{"cue": "a", "on": "x", "verify": 0}]}}}
    (p.root / "cues.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError, match="'verify' must be true or false"):
        load_cues(p)


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
    monkeypatch.setattr("decktalk.media.ffmpeg.changed_pixels_percent", lambda path, t1, t2, **kw: 0.5)
    assert [c.verdict for c in verify(p).cues] == ["changed"]
    monkeypatch.setattr("decktalk.media.ffmpeg.changed_pixels_percent", lambda path, t1, t2, **kw: 0.11)
    monkeypatch.setenv("DECKTALK_VERIFY_THIN_CHANGE_FACTOR", "1")
    assert [c.verdict for c in verify(Project.load(p.root)).cues] == ["changed"]


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


def test_check_to_dict_splits_verdicts(tmp_path):
    from decktalk.stages.measure import RecordingCheck, split_verdicts

    file = tmp_path / "build" / "rec" / "01-scene.webm"
    row = RecordingCheck("01", 10.04, 10.3, 50.0, 60.0, 70.0, 80.0, "NO COVER STALLED 140ms", ["boom"], file=file)
    d = json.loads(json.dumps(row.to_dict(tmp_path)))
    assert d["file"] == "build/rec/01-scene.webm" and d["duration"] == 10.04 and d["max50"] == 80.0
    assert d["verdicts"] == ["NO COVER", "STALLED"] and d["stall_ms"] == 140 and d["page_errors"] == ["boom"]
    assert split_verdicts("ok") == ([], None)
    codes = ["BLACK?", "TRUNCATED", "PAGE ERROR", "KATEX?"]
    assert split_verdicts(" ".join(codes)) == (codes, None)


def test_page_mentions_finds_quoted_ids_and_data_cue():
    from decktalk.stages.beats import page_mentions

    html = """<div data-cue="4.1answer"></div>
    <script>DeckTalk.scene({ cues: { "4.1x": 1 }, on: { '4.1y': () => {} }, tpl: `4.1z` });</script>"""
    for cue in ("4.1answer", "4.1x", "4.1y", "4.1z"):
        assert page_mentions(html, cue), cue
    assert not page_mentions(html, "4.1")  # A prefix of a quoted id is not a mention.
    assert not page_mentions(html, "4.1ans")
    assert not page_mentions("<p>\"4.1x'</p>", "4.1x")  # The quotes must match.


def _beats_project(tmp_path, html: str, cues: dict) -> Project:
    """Sections 0 (a clip) and 1 (a page), with narration words for section 1."""
    from decktalk.artifacts import write_words

    toml = "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"
    root = write_project(tmp_path, toml)
    (root / "deck").mkdir()
    (root / "deck" / "index.html").write_text(html, encoding="utf-8")
    (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    p = Project.load(root, environ={})
    m = Manifest(script="script.md", model="m", output_format="mp3")
    m.segments["01"] = ManifestSegment(1, "A", "01-a.mp3", "01-a.words.json", "h", 2, 1.0, 3.0)
    m.save(p.manifest_path)
    write_words(p.audio_dir / "01-a.words.json", [Word("hello", 0.5, 0.9), Word("there", 1.0, 1.4)])
    return p


def test_beats_reports_a_cue_id_missing_from_the_page(tmp_path):
    from decktalk.stages.beats import UnknownCueError, load_cues, resolve_beats, unknown_cue_ids

    cues = {
        "0": {"cues": [{"cue": "0.clip", "on": "$start"}]},
        "1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]},
    }
    p = _beats_project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    assert unknown_cue_ids(p, load_cues(p)) == [("01", "4.1answer", "deck/index.html")]
    with pytest.raises(UnknownCueError) as caught:
        resolve_beats(p)
    assert str(caught.value).startswith(
        "1 cue id(s) in cues.json appear nowhere in the page that plays them, so the page would never reveal "
        'them. Add data-cue="4.1answer" to the step in deck/index.html, fix the id in cues.json, or pass '
        "--allow-unknown:\n  section 01: 4.1answer: not in deck/index.html"
    )
    assert isinstance(caught.value, ConfigError) and caught.value.result.unknown == 1
    assert json.loads(p.beats_path.read_text(encoding="utf-8")) == {
        "01": "1.1a@0.5,4.1answer@1.0"
    }  # written before the stop
    result = resolve_beats(p, allow_unknown=True)
    assert result.unknown == 1 and result.unresolved == 0
    assert result.sections[1].notes == ["4.1answer: not in deck/index.html"]


def test_cli_beats_reports_unknown_cue_ids_as_a_finding(tmp_path, capsys):
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]}}
    p = _beats_project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    # Without --allow-unknown the command still prints its JSON and exits 1, instead of stopping on the error.
    assert main(["-p", str(p.root), "beats", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"]["certain"] == 1
    assert doc["beats"]["unknown"] == 1
    assert main(["-p", str(p.root), "beats", "--json", "--allow-unknown"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_beats_to_dict_counts_unresolved(tmp_path):
    from decktalk.stages.beats import resolve_beats

    cues = {"1": {"min_seconds": 9, "cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "1.1b", "on": "missing phrase"}]}}
    p = _beats_project(tmp_path, "<b data-cue='1.1a'></b><b data-cue='1.1b'></b>", cues)
    d = json.loads(json.dumps(resolve_beats(p).to_dict(p.root)))
    assert d["estimated"] is False and d["beats_file"] == "build/audio/beats.json"
    assert d["unresolved"] == 1 and d["unknown"] == 0
    (section,) = d["sections"]
    assert section["key"] == "01" and section["speech_end"] == 1.4 and section["min_seconds"] == 9.0
    assert section["skipped"] is None and section["cues"] == {"1.1a": 0.5}
    assert section["notes"] == [
        {"cue": "1.1b", "verdict": "UNRESOLVED", "detail": "phrase not found: 'missing phrase'"},
        {"cue": None, "verdict": None, "detail": "speech 1.4s is 7.6s shorter than the visuals need"},
    ]
    assert sum(n["verdict"] == "UNRESOLVED" for s in d["sections"] for n in s["notes"]) == d["unresolved"]


def test_beats_warns_about_a_repeated_phrase_unless_the_cue_names_its_occurrence(tmp_path, capsys):
    from decktalk.artifacts import write_words
    from decktalk.stages.beats import resolve_beats

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
    p = _beats_project(tmp_path, ids, cues)
    words = [
        Word("Every", 0.5, 0.8), Word("step", 0.9, 1.2), Word("there.", 1.3, 1.6),
        Word("For", 36.0, 36.2), Word("every", 36.3, 36.6), Word("step!", 36.7, 37.1),
    ]  # fmt: skip
    write_words(p.audio_dir / "01-a.words.json", words)
    manifest = p.manifest()
    assert manifest is not None
    manifest.segments["01"].duration_seconds = 38.0
    manifest.save(p.manifest_path)
    result = resolve_beats(p)
    (section,) = [s for s in result.sections if s.key == "01"]
    detail = (
        "'every step' occurs 2 times in this section, at 0.50s, 36.30s. The cue uses the first. "
        'Set "occurrence" to choose one.'
    )
    # Only the cue that names no occurrence is ambiguous. A case-sensitive phrase counts only its own case.
    assert [(n.cue, n.verdict, n.detail) for n in section.findings] == [("1.1step", None, detail)]
    assert section.resolved["1.1step"] == 0.5 and result.unresolved == 0
    assert main(["-p", str(p.root), "beats", "--strict"]) == 0  # A warning, not a finding.
    assert f"! 1.1step: {detail}" in capsys.readouterr().out


def test_build_captions_uses_manifest_spoken_text(tmp_path):
    from decktalk.stages.assemble import build_captions, caption_texts

    root = write_project(tmp_path, PAGES_TOML)
    p = Project.load(root, environ={})
    words = [Word("hello", 0.5, 0.9), Word("there", 1.0, 1.4)]
    tl = Timeline(narration="n.mp3", total_seconds=2.0, sections={"01": TimelineSection("A", 0, 2.0, 2.0, 1.4, words)})
    m = Manifest(script="script.md", model="m", output_format="mp3")
    m.segments["01"] = ManifestSegment(1, "A", "01-a.mp3", "01-a.words.json", "h", 2, 1.0, 2.0, spoken="Hello, there.")
    m.save(p.manifest_path)
    # No script.md exists, so the text can only come from the manifest.
    assert caption_texts(p, tl) == {"01": "Hello, there."}
    assert [c.text for c in build_captions(tl, 0.0, caption_texts(p, tl))] == ["Hello, there."]
    # A manifest written before the field existed falls back to the script.
    raw = json.loads(p.manifest_path.read_text(encoding="utf-8"))
    del raw["segments"]["01"]["spoken"]
    p.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    (root / "script.md").write_text(
        "## 1. A\n\nHello there!\n\n## 2. B\n\nTwo.\n\n## 3. C\n\nThree.\n", encoding="utf-8"
    )
    assert caption_texts(p, tl)["01"] == "Hello there!"


def test_scene_params_adds_beats_unless_the_section_sets_them(tmp_path):
    from decktalk.project import PageSection
    from decktalk.stages.record import scene_params
    from decktalk.stages.shots import shoot_steps

    beats = Beats({"01": {"a": 1.5, "b": 2.0}})
    own = PageSection(1, "deck/index.html", "1", params={"theme": "dark"})
    assert scene_params(own, beats) == {"theme": "dark", "beats": "a@1.5,b@2.0"}
    assert scene_params(own, None) == {"theme": "dark"}
    fixed = PageSection(1, "deck/index.html", "1", params={"beats": "x@1"})
    assert scene_params(fixed, beats) == {"beats": "x@1"}
    assert scene_params(PageSection(2, "deck/index.html", "2"), beats) == {}
    p = Project.load(write_project(tmp_path, PAGES_TOML), environ={})
    with pytest.raises(ConfigError, match="exactly one step"):
        shoot_steps(p, steps=["1.1", "2.1"], cues=["1.1a"])


def test_scene_url_passes_the_previous_sections_words(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from decktalk.project import PageSection
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
    from decktalk.artifacts import Sidecar

    side = Sidecar.__new__(Sidecar)
    side.frame_gaps = [(None, 900), (0.05, 216), (0.4, 120), (12.8, 132)]
    # The first gap ended under the cover. The second began there and shows for 50 ms.
    assert side.worst_stall_ms == 132
    side.frame_gaps = [(None, 900), (0.05, 216)]
    assert side.worst_stall_ms == 50
    side.frame_gaps = []
    assert side.worst_stall_ms == 0


def test_sidecar_writes_null_for_a_gap_before_the_clock(tmp_path):
    p = tmp_path / "s.json"
    side = Sidecar(url="u", requested_seconds=1, settle_seconds=0, load_seconds=0, lead_seconds=0)
    side.frame_gaps = [(float("-inf"), 900), (None, 400), (0.05, 216)]
    side.save(p)
    text = p.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    # Standard JSON parsers such as JSON.parse and jq reject the -Infinity token.
    data = json.loads(text, parse_constant=lambda token: pytest.fail(f"non-standard JSON token {token}"))
    assert data["frame_gaps"] == [[None, 900], [None, 400], [0.05, 216]]
    again = Sidecar.load(p)
    assert again is not None and again.frame_gaps == [(None, 900), (None, 400), (0.05, 216)]
    assert again.worst_stall_ms == 50


def test_sidecar_reads_an_older_file_that_holds_negative_infinity(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        '{"url": "u", "requested_seconds": 1, "settle_seconds": 0, "load_seconds": 0, "lead_seconds": 0,'
        ' "frame_gaps": [[-Infinity, 900], [0.4, 120]]}',
        encoding="utf-8",
    )
    side = Sidecar.load(p)
    assert side is not None and side.frame_gaps == [(None, 900), (0.4, 120)]
    assert side.worst_stall_ms == 120
    side.save(p)
    assert "Infinity" not in p.read_text(encoding="utf-8")


def test_worst_stall_treats_a_null_time_as_a_gap_under_the_cover():
    side = Sidecar(url="u", requested_seconds=1, settle_seconds=0, load_seconds=0, lead_seconds=0)
    side.frame_gaps = [(None, 900)]
    assert side.worst_stall_ms == 0
    side.frame_gaps = [(None, 900), (0.05, 216), (12.8, 132)]
    assert side.worst_stall_ms == 132


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

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(ProviderError) as info:
        _http.post_json(url, {"text": "hi"}, {"xi-api-key": api_key}, timeout=1)
    message = str(info.value)
    assert voice_id not in message and api_key not in message
    assert "https://api.elevenlabs.io/v1/text-to-speech/<voice id>/with-timestamps" in message
    assert "HTTP 404" in message and "voice_not_found" in message

    def unreachable(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(ProviderError) as info:
        _http.get_json(f"https://api.elevenlabs.io/v1/voices/{voice_id}", {"xi-api-key": api_key}, timeout=1)
    assert voice_id not in str(info.value) and api_key not in str(info.value)
    assert "voices/<voice id>" in str(info.value)


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
title = "Open"
page = "deck/index.html"

[[section]]
number = 2
title = "The edit"
clip = "media/before.mov"
words = "media/before.words.json"

[[section]]
number = 3
title = "The edit"
page = "deck/index.html"

[[section]]
number = 4
title = "Close"
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
    from decktalk.stages.narrate import Segment, is_cached, reusable_entry, reuse_takes

    audio = tmp_path
    for name, body in [("01-open.mp3", "open"), ("01-open.words.json", "[]"), ("05-x.mp3", "five"),
                       ("05-x.words.json", "[5]"), ("06-x.mp3", "six"), ("06-x.words.json", "[6]")]:  # fmt: skip
        (audio / name).write_text(body, encoding="utf-8")

    def entry(index: int, digest: str) -> ManifestSegment:
        key = f"{index:02d}"
        slug = "open" if index == 1 else "x"
        return ManifestSegment(index, "X", f"{key}-{slug}.mp3", f"{key}-{slug}.words.json", digest, 1, 1.0, 1.0)

    previous = Manifest("s", "m", "mp3")
    previous.segments = {"01": entry(1, "h1"), "05": entry(5, "h5"), "06": entry(6, "h6")}
    to_four = Segment(4, "X", "x", "five text")
    to_five = Segment(5, "X", "x", "six text")
    assert not is_cached(previous.segments["05"], to_five, "h6", audio)
    assert reusable_entry(previous, to_four, "h5", audio) == ("05", previous.segments["05"])
    assert reusable_entry(previous, to_five, "h6", audio) == ("06", previous.segments["06"])
    assert reusable_entry(previous, to_four, "nope", audio) is None
    # The first spoken section carries the lead-in silence, so its take never moves in or out.
    assert reusable_entry(previous, Segment(2, "X", "x", "t", lead_break=True), "h5", audio) is None
    assert reusable_entry(previous, to_four, "h1", audio) is None
    # 05 moves to 04 while 06 moves onto 05's old name, and each file keeps its own take.
    reuse_takes([(previous.segments["05"], to_four), (previous.segments["06"], to_five)], audio)
    assert (audio / "04-x.mp3").read_text(encoding="utf-8") == "five"
    assert (audio / "05-x.mp3").read_text(encoding="utf-8") == "six"
    assert (audio / "05-x.words.json").read_text(encoding="utf-8") == "[6]"
    assert not list(audio.glob(".reuse-*"))


# ---- narration tail -------------------------------------------------------------------------


def test_trailing_silence_counts_a_silence_ending_0_0502_s_before_the_end(monkeypatch):
    """The numbers of the demo's 08-the-edit.mp3: every narrate run padded it again by 1.35 s."""
    from decktalk.media import ffmpeg

    detect = (
        "  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, mono, fltp, 128 kb/s\n"
        "[silencedetect @ 0x1] silence_start: 13.914717\n"
        "[silencedetect @ 0x1] silence_end: 13.974331 | silence_duration: 0.0596145\n"
        "[silencedetect @ 0x1] silence_start: 14.380816\n"
        "[silencedetect @ 0x1] silence_end: {end} | silence_duration: 1.320998\n"
    )
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 15.752)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.701814))
    assert ffmpeg.trailing_silence(Path("08-the-edit.mp3")) == 1.371
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.5))  # speech after the silence
    assert ffmpeg.trailing_silence(Path("08-the-edit.mp3")) == 0.0
    # At 8 kHz one mp3 frame lasts 0.144 s, longer than the fixed tolerance.
    low = detect.replace("44100 Hz", "8000 Hz").format(end=15.62)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: low)
    assert ffmpeg.trailing_silence(Path("08-the-edit.mp3")) == 1.371


def test_a_padded_take_within_a_frame_of_min_tail_is_not_padded_again(monkeypatch):
    from decktalk.config import NarrationConfig
    from decktalk.media import ffmpeg
    from decktalk.stages.narrate import ensure_tail

    padded: list[float] = []
    monkeypatch.setattr(ffmpeg, "pad_tail", lambda path, seconds, bitrate: padded.append(seconds))
    monkeypatch.setattr(ffmpeg, "trailing_silence", lambda path: 1.26)
    cfg = NarrationConfig(min_tail_seconds=1.3)
    assert ensure_tail(Path("a.mp3"), cfg, tolerance=ffmpeg.SILENCE_END_TOLERANCE_SECONDS) == 0.0
    assert padded == []
    assert ensure_tail(Path("a.mp3"), cfg) == 0.09  # a take never padded before gets the full tail
    assert padded == [0.09]


# ---- stale measurement ------------------------------------------------------------------------


def _recorded(tmp_path: Path) -> tuple[Project, Path]:
    """A one-page project with a recording and the sidecar that `record` writes, not yet measured."""
    root = write_project(tmp_path, "[[section]]\nnumber = 1\npage = 'a.html'\n")
    (root / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    p.rec_dir.mkdir(parents=True)
    webm = p.rec_dir / "01-scene.webm"
    webm.write_bytes(b"take one")
    Sidecar(url="u", requested_seconds=2, settle_seconds=0.5, load_seconds=0.1, lead_seconds=1.506).save(
        webm.with_suffix(".json")
    )
    return p, webm


def test_stale_measure_ties_the_measurement_to_one_recording(tmp_path, monkeypatch):
    import os

    from decktalk.media import ffmpeg
    from decktalk.stages.measure import measure, recording_hash, stale_measure

    p, webm = _recorded(tmp_path)
    side_path = webm.with_suffix(".json")
    name = "build/rec/01-scene.webm"
    assert stale_measure(webm, Sidecar.load(side_path), p.root) == (
        f"{name} was never measured, so the cut would trim the recorder's wall-clock estimate of 1.506s"
    )
    assert stale_measure(webm, None, p.root) == f"{name} has no sidecar, so `measure` never found its narration t=0"

    monkeypatch.setattr(ffmpeg, "frame_stats", lambda path, seconds: [])
    (row,) = measure(p)
    side = Sidecar.load(side_path)
    assert side is not None and side.lead_in_seconds == row.lead_in_seconds
    assert side.lead_in_hash == recording_hash(webm) and len(side.lead_in_hash) == 16
    assert stale_measure(webm, side, p.root) is None

    webm.write_bytes(b"take two")  # a new take over the measured one
    assert stale_measure(webm, side, p.root) == f"{name} changed after `measure` read it"

    # A sidecar that an older measure wrote has no hash, so the file times decide.
    side.lead_in_hash = None
    side.save(side_path)
    os.utime(side_path, (1_000_000, 1_000_000))
    os.utime(webm, (2_000_000, 2_000_000))
    assert stale_measure(webm, side, p.root) == f"{name} is newer than its measurement"
    os.utime(side_path, (3_000_000, 3_000_000))
    assert stale_measure(webm, side, p.root) is None


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
        "section 1: STALE MEASUREMENT: build/rec/01-scene.webm was never measured, so the cut would trim the "
        "recorder's wall-clock estimate of 1.506s. Run `decktalk measure --only 1`, then `decktalk assemble` again."
    )
    assert ran == []

    expected = (
        "section 01: STALE MEASUREMENT: build/rec/01-scene.webm was never measured, so the cut would trim the "
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
    assert asm.assemble(p, nomix=True).warnings == [expected]

    side_path = webm.with_suffix(".json")
    side = Sidecar.load(side_path)
    assert side is not None
    side.lead_in_seconds, side.lead_in_hash = 1.44, recording_hash(webm)
    side.save(side_path)
    (measured,) = render(p, timeline, strict=True)
    assert measured.warning is None and "lead 1.44s trimmed" in measured.note


def test_verify_and_assemble_ignore_a_leftover_section_video(tmp_path, monkeypatch, caplog):
    """A 04-section.mp4 left after sections were renumbered is not counted as a section."""
    import importlib

    from decktalk.stages.verify import verify

    asm = importlib.import_module("decktalk.stages.assemble")
    p = _verify_project(tmp_path, monkeypatch, {"01": "a@1.0"})
    (p.out_dir / "04-section.mp4").write_bytes(b"x")
    (p.out_dir / "t-20260101-0000.mp4").write_bytes(b"x")  # other files in build/out are not section videos
    assert p.stray_section_videos() == [p.out_dir / "04-section.mp4"]

    with caplog.at_level("WARNING", logger="decktalk"):
        result = verify(p, checks=[])
    assert [s.key for s in result.starts] == ["01", "02"] and result.total_seconds == 10.0
    assert [r.getMessage() for r in caplog.records] == [
        "build/out/04-section.mp4 is not a section in decktalk.toml, so verify ignores it. "
        "Delete the file if an earlier build left it."
    ]

    (p.root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    Timeline(
        narration="narration.mp3",
        total_seconds=5.0,
        sections={"01": TimelineSection("A", 0, 5.0, 5.0, 1.0, [Word("Hi", 0.7, 1.0)])},
        estimated=True,
    ).save(p.timeline_path)
    rows = [asm.RenderedSection(p.sections[0], p.out_dir / "01-section.mp4", 5.0, "page")]
    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: rows)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
    assert asm.assemble(p, nomix=True).warnings == [
        "build/out/04-section.mp4 is not a section in decktalk.toml, so assemble ignores it. "
        "Delete the file if an earlier build left it."
    ]


# ---- silent runs over voiced takes ------------------------------------------------------------


def _voiced(tmp_path: Path) -> tuple[Project, Path, Path]:
    """A project whose build/audio holds one voiced take."""
    root = write_project(tmp_path, "[[section]]\nnumber = 1\npage = 'a.html'\n")
    (root / "script.md").write_text("## 1. Open\n\nHello there.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    p.audio_dir.mkdir(parents=True)
    take = p.audio_dir / "01-open.mp3"
    take.write_bytes(b"voiced take")
    manifest = Manifest(script="script.md", model="eleven_v3", output_format="mp3_44100_128")
    manifest.segments["01"] = ManifestSegment(
        index=1, title="Open", file=take.name, words_file="01-open.words.json", hash="3f2a9c0d1e2b4a5f",
        words=2, est_seconds=1.0, duration_seconds=2.3,
    )  # fmt: skip
    manifest.save(p.manifest_path)
    return p, take, p.manifest_path


VOICED_REFUSAL = (
    "build/audio/manifest.json holds voiced takes for sections 01. A silent run writes click tracks over those "
    "mp3 files and replaces the manifest, so the next voiced build voices every section again and spends credits "
    "on all of them. Rehearse the silent build in a copy of the project, or pass --force to replace the voiced takes."
)


def test_a_silent_narrate_refuses_voiced_takes_unless_forced(tmp_path, monkeypatch):
    from decktalk.media import ffmpeg
    from decktalk.stages.narrate import narrate

    p, take, manifest_path = _voiced(tmp_path)
    before = manifest_path.read_bytes()
    with pytest.raises(ConfigError) as err:
        narrate(p, silent=True)
    assert str(err.value) == VOICED_REFUSAL
    assert take.read_bytes() == b"voiced take" and manifest_path.read_bytes() == before

    monkeypatch.setattr(ffmpeg, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    monkeypatch.setattr(ffmpeg, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    result = narrate(p, silent=True, force=True)
    assert result.synthesized == ["01"] and take.read_bytes() == b"clicks"
    manifest = Manifest.load(manifest_path)
    assert manifest is not None and manifest.estimated and manifest.segments["01"].hash == "silent"
    assert narrate(p, silent=True).synthesized == ["01"]  # a silent manifest is never refused


@pytest.mark.parametrize("command", [["build", "--silent"], ["narrate", "--silent"]])
def test_cli_silent_run_over_voiced_takes_exits_1_with_the_risk(tmp_path, capsys, command):
    p, take, _manifest = _voiced(tmp_path)
    assert main([*command, "-p", str(p.root)]) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == f"error: {VOICED_REFUSAL}"
    assert take.read_bytes() == b"voiced take"
    assert build_parser().parse_args(["build", "--silent", "--force"]).force
