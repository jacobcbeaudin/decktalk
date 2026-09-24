"""The assemble stage in order, with every ffmpeg call replaced."""

from __future__ import annotations

import importlib
from pathlib import Path

from decktalk.artifacts import Word
from decktalk.model import Project
from decktalk.stages.assemble.cut import RenderedSection
from decktalk.verdicts import Verdict


def test_assemble_skips_loudness_on_an_estimated_take(
    tmp_path, monkeypatch, caplog, write_project, pages_toml, take_index
):

    # decktalk.stages exports a function named assemble, so the module is imported by its full name.
    asm = importlib.import_module("decktalk.stages.assemble")
    publish_module = importlib.import_module("decktalk.stages.assemble.publish")
    cut_module = importlib.import_module("decktalk.stages.assemble.cut")

    root = write_project(tmp_path, pages_toml)
    (root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    takes = take_index(p, {"01": ("A", 2.0, 1.0, [Word("Hi", 0.7, 1.0)])})
    takes.sections["01"].voiced = False  # a run without voice, whose times are guesses
    takes.save(p.takes_path)
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    rows = [RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 2.0, "01.webm")]

    def write_last(*args):
        Path(args[-1]).write_bytes(b"x")

    def no_loudness(*args):
        raise AssertionError("a build without voice must not be normalized")

    monkeypatch.setattr(asm, "render_sections", lambda project, takes, strict: rows)
    monkeypatch.setattr(publish_module, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(publish_module, "mux_chapters", lambda src, chapters, dst, language: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm, "normalize_loudness", no_loudness)
    for module in (asm, cut_module, publish_module):
        monkeypatch.setattr(module.ffmpeg, "run", write_last)
        monkeypatch.setattr(module.ffmpeg, "probe_duration", lambda path: 2.0)
    with caplog.at_level("INFO", logger="decktalk.stages.assemble"):
        result = asm.assemble(p, soundscape=False)
    assert result.loudness is None and result.warnings == []
    assert any(
        r.getMessage() == "[loud] skipped: the narration is a silent placeholder, so the soundtrack is "
        "encoded as it was mixed"
        for r in caplog.records
    )


def test_the_soundtrack_meets_the_delivery_encoder_exactly_once(
    tmp_path, monkeypatch, write_project, pages_toml, take_index
):
    """AAC twice is heard on the loudest word, so the mix is carried in float until one encode."""
    from decktalk.media.audio import Loudness

    asm = importlib.import_module("decktalk.stages.assemble")
    publish_module = importlib.import_module("decktalk.stages.assemble.publish")
    cut_module = importlib.import_module("decktalk.stages.assemble.cut")
    mix_module = importlib.import_module("decktalk.stages.assemble.mix")
    loudness_module = importlib.import_module("decktalk.stages.assemble.loudness")

    root = write_project(tmp_path, pages_toml)
    (root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    take_index(p, {"01": ("A", 2.0, 1.8, [Word("Hi", 0.7, 1.0)])}).save(p.takes_path)
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    rows = [RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 2.0, "01.webm")]
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> None:
        calls.append(args)
        Path(args[-1]).write_bytes(b"x")

    measured = Loudness(i=-20.0, tp=-3.0, lra=5, thresh=-27, offset=0)
    monkeypatch.setattr(asm, "render_sections", lambda project, takes, strict: rows)
    monkeypatch.setattr(asm, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(publish_module, "mux_chapters", lambda src, chapters, dst, language: dst.write_bytes(b"x"))
    monkeypatch.setattr(loudness_module.audio, "measure_loudness", lambda path, **kw: measured)
    for module in (asm, cut_module, publish_module, mix_module, loudness_module):
        monkeypatch.setattr(module.ffmpeg, "run", run)
        monkeypatch.setattr(module.ffmpeg, "probe_duration", lambda path: 2.0)
    asm.assemble(p, soundscape=False)
    encodes = [args for args in calls if "-c:a" in args and args[args.index("-c:a") + 1] == "aac"]
    assert len(encodes) == 1, "the soundtrack meets the AAC encoder once and no more"
    mixes = [args for args in calls if "pcm_f32le" in args]
    assert len(mixes) == 1, "the mix is written in float, so a sum past 0 dBFS reaches the limiter intact"


def test_a_cued_sound_with_no_caption_reaches_the_result_and_its_payload(
    tmp_path, monkeypatch, write_project, pages_toml, take_index
):
    """The stage wires its own findings into the result, which nothing but this test drives."""
    asm = importlib.import_module("decktalk.stages.assemble")
    publish_module = importlib.import_module("decktalk.stages.assemble.publish")
    cut_module = importlib.import_module("decktalk.stages.assemble.cut")

    sfx = "\n[[mix.sfx]]\nfile = 'media/hum.mp3'\nsection = 1\ncue = '1.1a'\n"
    root = write_project(tmp_path, pages_toml + sfx)
    (root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    p = Project.load(root, environ={})
    takes = take_index(p, {"01": ("A", 2.0, 1.0, [Word("Hi", 0.7, 1.0)])})
    takes.sections["01"].voiced = False  # a run without voice, so no loudness pass runs
    takes.save(p.takes_path)
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    rows = [RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 2.0, "01.webm")]

    monkeypatch.setattr(asm, "render_sections", lambda project, takes, strict: rows)
    monkeypatch.setattr(publish_module, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(publish_module, "mux_chapters", lambda src, chapters, dst, language: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm, "normalize_loudness", lambda *a, **kw: None)
    for module in (asm, cut_module, publish_module):
        monkeypatch.setattr(module.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
        monkeypatch.setattr(module.ffmpeg, "probe_duration", lambda path: 2.0)

    result = asm.assemble(p, soundscape=False)
    # The row the stage built, the tally it feeds, and the payload key a reader dispatches on.
    assert [row.verdict for row in result.rows] == [Verdict.NO_CAPTION]
    assert result.findings.uncertain == 1
    assert result.to_dict(p.root)["uncaptioned"] == [row.to_dict() for row in result.rows]
