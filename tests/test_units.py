"""Unit tests that need neither ffmpeg nor Chromium nor an API key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from decktalk.config import Settings
from decktalk.stages.assemble import fade_flags, timeline_targets
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
    (tmp_path / "decktalk.toml").write_text(toml)
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
    assert s.video.fps == 30  # default


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
    ],
)
def test_project_validation_messages(tmp_path, toml, message):
    with pytest.raises(ConfigError, match=message):
        Project.load(write_project(tmp_path, toml), environ={})


def test_project_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="decktalk init"):
        Project.load(tmp_path, environ={})


def test_project_tuning_tables_reach_settings(tmp_path):
    p = Project.load(write_project(tmp_path, MINIMAL_TOML + "\n[video]\npreset = 'ultrafast'\n"), environ={})
    assert p.settings.video.preset == "ultrafast"


def test_project_env_reads_dotenv_and_ignores_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    root = write_project(tmp_path)
    (root / ".env").write_text("ELEVENLABS_API_KEY=<fill me>\nELEVENLABS_VOICE_ID='abc' # comment\n")
    p = Project.load(root, environ={})
    assert p.env("ELEVENLABS_API_KEY") == ""
    assert p.env("ELEVENLABS_VOICE_ID") == "abc"
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        p.require_env("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")


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
    assert json.loads((tmp_path / "b.json").read_text()) == {"01": "a@1.5,panel:bought@2.0"}
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
    assert one.text.count("<break") == 1  # a direction between paragraphs becomes one pause
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


# ---- scaffold lesson: pauses, cue keys, KaTeX vendoring, runtime warnings ----------------------
# Appended for the lesson-shaped scaffold. Everything above this line belongs to earlier work.


def test_pause_direction_yields_a_timed_break():
    from decktalk.stages.narrate import BREAK_RE

    seg = parse_script("## 1. A\n\nThink about it.\n\n[pause 3]\n\nOnly two x is left. [beat] Done.")[0]
    assert BREAK_RE.findall(seg.text) == ["3", "0.7"]
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
        json.dumps({"sections": {"1": {"cues": [{"cue": "1.1a", "on": "$start"}, {"step": "1.1b", "on": "hello"}]}}})
    )
    project = Project.load(root, environ={})
    (section,) = load_cues(project)
    assert [c.step for c in section.cues] == ["1.1a", "1.1b"]
    assert [c.on for c in section.cues] == ["$start", "hello"]


def test_sidecar_warnings_default_and_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"url": "u", "requested_seconds": 1, "settle_seconds": 0, "load_seconds": 0, "lead_seconds": 0})
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
    (d / "katex.min.js").write_text("window.katex = {};")
    (d / "katex.min.css").write_text(".katex{}")
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
    html = (root / "deck" / "index.html").read_text()
    assert "./katex/katex.min.css" in html and "./katex/katex.min.js" in html
    assert "cdnjs" not in html and "__KATEX__" not in html


def test_init_falls_back_to_cdn_with_a_warning(tmp_path, monkeypatch, caplog):
    from decktalk.scaffold import init, katex_cached

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    assert katex_cached() is None
    with caplog.at_level("WARNING", logger="decktalk.scaffold"):
        root = init(tmp_path / "proj", name="proj")
    assert not (root / "deck" / "katex").exists()
    html = (root / "deck" / "index.html").read_text()
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
    assert (dest / "fonts" / "KaTeX_Main-Regular.woff2").read_text() == "font"
    assert scaffold.katex_cached() == dest


def test_template_ids_agree_across_page_cues_and_script(tmp_path, monkeypatch):
    """Every cue id in cues.json has an owning step in the page, and every phrase is in its section."""
    import re

    from decktalk.scaffold import init
    from decktalk.stages.beats import load_cues

    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = init(tmp_path / "proj", name="proj")
    project = Project.load(root, environ={})
    html = (root / "deck" / "index.html").read_text()
    step_ids = re.findall(r'id: "([0-9.]+)"', html)
    listed = set(re.findall(r'"([0-9.]+[a-z0-9]*)": [0-9.]+', html))
    segments = {s.index: s for s in parse_script((root / "script.md").read_text())}
    for section in load_cues(project):
        words = [Word(w, i, i + 1) for i, w in enumerate(segments[section.number].spoken.split())]
        for cue in section.cues:
            owner = cue.step in step_ids or cue.step in listed or any(cue.step.startswith(s) for s in step_ids)
            assert owner, f"cue {cue.step} has no owning step"
            if not cue.on.startswith("$"):
                assert find_phrase(words, cue.on) is not None, (
                    f"cue {cue.step}: {cue.on!r} is not in section {section.number}"
                )
