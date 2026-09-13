"""Unit tests that need neither ffmpeg nor Chromium nor an API key."""

from __future__ import annotations

import json
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


def test_caption_cues_keep_lines_short_and_break_on_punctuation():
    from decktalk.artifacts import CAPTION_MAX_CHARS, caption_cues

    text = (
        "Welcome. This is a narrated deck, cut to the word. Every visual you see lands on the word "
        "that names it, and nothing drifts. Short."
    )
    cues = caption_cues(_spoken(text))
    assert cues, "words produce cues"
    for cue in cues:
        assert 1 <= len(cue.lines) <= 2
        assert all(len(line) <= CAPTION_MAX_CHARS for line in cue.lines)
    assert cues[0].lines[0].endswith(",")  # the overflow backed up to the clause break
    assert cues[-1].lines[-1].endswith("Short.")
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start  # a cue never overlaps the next one
    assert cues[-1].end > cues[-1].start


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
    srt = (tmp_path / "c.srt").read_text()
    vtt = (tmp_path / "c.vtt").read_text()
    assert srt.startswith("1\n00:00:03,700 --> 00:00:10,500\nWelcome.\nThis is a deck.\n\n2\n01:01:01,250 --> ")
    assert vtt.startswith("WEBVTT\n\n00:00:03.700 --> 00:00:10.500\nWelcome.\nThis is a deck.\n")
    assert ffmetadata_escape("a=b;c#d\\e") == "a\\=b\\;c\\#d\\\\e"
    write_chapters(tmp_path / "ch.txt", [Chapter(0, 3.0, "On camera"), Chapter(3.0, 12.44, "Open; part = 1")])
    text = (tmp_path / "ch.txt").read_text()
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
    assert Settings().verify.max_offset_frames == 2 and Settings().verify.onset_percent == 0.002


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
    user_file.write_text('[video]\npreset = "veryfast"\ncrf = 22\n[record]\nsettle_seconds = 0.9\n')
    monkeypatch.setenv("DECKTALK_CONFIG", str(user_file))
    assert user_config_path() == user_file
    s = load_settings(toml={"video": {"crf": 20}}, environ={"DECKTALK_RECORD_SETTLE_SECONDS": "1.2"})
    assert s.video.preset == "veryfast"  # from the user file
    assert s.video.crf == 20  # the project wins over the user file
    assert s.record.settle_seconds == 1.2  # the environment wins over both
    user_file.write_text("[project]\nname = 'x'\n")
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
        (exe_dir / name).write_text("")
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
