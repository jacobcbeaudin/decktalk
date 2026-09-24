"""`words` and `clip`: the script's spelling on each section's clock, and the span cut out of the film."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Word, read_words, write_words
from decktalk.cli import main
from decktalk.errors import ConfigError, MissingInputError
from decktalk.media import audio, ffmpeg, frames
from decktalk.model import Project
from decktalk.stages.clip import _clip_words, clip, words


def _words_project(tmp_path: Path) -> Project:
    """Page sections 1 (with a 0.5 s lead) and 2 (with none), a clip section 3, and a take for each spoken section.

    Only section 1's take records the script's spelling, so section 2 keeps the voice's.
    """
    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\nchapter = 'Open'\npage = 'a.html'\nlead_seconds = 0.5\n"
        "[[section]]\nnumber = 2\nchapter = 'Close'\npage = 'a.html'\nlead_seconds = 0\n"
        "[[section]]\nnumber = 3\nclip = 'media/c.mp4'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    p.takes_dir.mkdir(parents=True)
    write_words(p.takes_dir / "01-open.words.json", [Word("Hello", 0.1, 0.4), Word("there", 0.5, 0.9)])
    write_words(p.takes_dir / "02-close.words.json", [Word("Bye", 0.2, 0.5), Word("now", 0.6, 1.1)])
    take_index = Takes(script="script.md", model="m", output_format="mp3_44100_128")
    take_index.sections["01"] = Take(
        index=1, chapter="Open", file="01-open.mp3", words_file="01-open.words.json", hash="h",
        word_count=2, estimated_seconds=1.0, duration_seconds=2.5, lead_seconds=0.5, spoken="Hello, there.",
    )  # fmt: skip
    take_index.sections["02"] = Take(
        index=2, chapter="Close", file="02-close.mp3", words_file="02-close.words.json", hash="h2",
        word_count=2, estimated_seconds=1.0, duration_seconds=3.0,
    )  # fmt: skip
    take_index.save(p.takes_path)
    return p


def test_words_are_relative_to_each_section_with_the_scripts_spelling(tmp_path):
    p = _words_project(tmp_path)
    first, second = words(p).sections
    assert (first.key, first.title, first.lead_seconds, first.duration) == ("01", "Open", 0.5, 3.0)
    assert first.estimated is False
    assert first.words == [Word("Hello", 0.6, 0.9), Word("there", 1.0, 1.4)]
    assert first.texts == ["Hello,", "there."]
    # Section 2's take records no spoken text, so it keeps the voice's spelling.
    assert second.words == [Word("Bye", 0.2, 0.5), Word("now", 0.6, 1.1)]
    assert second.texts == ["Bye", "now"]
    assert [s.key for s in words(p, only=[2]).sections] == ["02"]
    no_words = r"section\(s\) \[3\] have no take in takes.json"
    with pytest.raises(ConfigError, match=no_words) as no_take:
        words(p, only=[3])
    # The sections that do have a take are the next action, so they are the hint.
    assert no_take.value.hint is not None and "[1, 2]" in no_take.value.hint


def test_spoken_words_needs_a_take_index(tmp_path):
    (tmp_path / "decktalk.toml").write_text("[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8")
    with pytest.raises(MissingInputError, match="The take index is not there") as raised:
        words(Project.load(tmp_path, environ={}))
    # The next action is the hint and the file is the path, so the message stays one sentence.
    assert raised.value.path == tmp_path / "build" / "narration" / "takes.json"
    assert "decktalk narrate" in (raised.value.hint or "")


def test_clip_words_keep_only_whole_words_shifted_to_the_clip(tmp_path):
    p = _words_project(tmp_path)
    assert _clip_words(p, "01", 0.55, 1.2) == ([Word("Hello,", 0.05, 0.35)], ["there."])
    assert _clip_words(p, "01", 0.6, 1.4) == ([Word("Hello,", 0.0, 0.3), Word("there.", 0.4, 0.8)], [])
    assert _clip_words(p, "01", 1.5, 2.0) == ([], [])


def test_clip_refuses_a_bad_span_before_it_runs_ffmpeg(tmp_path):
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
    index = p.takes()
    assert index is not None
    del index.sections["02"]  # a section the voice has not reached yet
    index.save(p.takes_path)
    with pytest.raises(MissingInputError, match="section 2 has no narration yet"):
        clip(p, 2, start=0, end=1, out="media/x.mp4")


def test_cli_words_prints_a_table_and_json(tmp_path, capsys):
    from decktalk.cli.schema import SpokenWord, read_envelope

    _words_project(tmp_path)
    assert main(["-p", str(tmp_path), "words", "--only", "1"]) == 0
    table = capsys.readouterr().out
    assert "== 01 Open  (3.00s, lead 0.5s)" in table
    assert "  0.600   0.900  Hello," in table
    assert "Close" not in table
    assert main(["-p", str(tmp_path), "words", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert (doc.command, doc.ok) == ("words", True)
    assert (doc.findings.certain, doc.findings.uncertain, doc.findings.items) == (0, 0, []) and doc.written == []
    first, second = doc.payload.sections
    assert first.section == 1 and first.lead_seconds == 0.5
    assert first.words[0] == SpokenWord(word="Hello", text="Hello,", start=0.6, end=0.9)
    assert second.words[1] == SpokenWord(word="now", text="now", start=0.6, end=1.1)


@pytest.mark.media
def test_clip_cuts_a_section_span_with_its_take_and_its_words(tmp_path, capsys):
    """The picture, the take over the same span after the section's lead, the gain, the hold, and the words file."""
    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\nchapter = 'Open'\npage = 'a.html'\nlead_seconds = 0.5\n", encoding="utf-8"
    )
    p = Project.load(tmp_path, environ={})
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    p.narration_dir.mkdir(parents=True)
    # A 4 s section: red until 2.0 s, then blue.
    ffmpeg.run(
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=2", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(p.section_video(p.sections[0])),
    )  # fmt: skip
    # The take has a tone from 1.5 to 2.0 s, which is 2.0 to 2.5 s in the section after its 0.5 s lead.
    ffmpeg.run(
        "-f", "lavfi", "-i", "sine=f=440:r=44100:d=0.5", "-af", "adelay=1500:all=1,apad=whole_dur=3.5",
        "-c:a", "libmp3lame", "-b:a", "128k", str(p.narration_dir / "01-open.mp3"),
    )  # fmt: skip
    # The take's own words, which the section's 0.5 s lead moves to 0.9, 2.0, 2.25 and 3.0 on its clock.
    write_words(
        p.takes_dir / "01-open.words.json",
        [Word("go", 0.4, 0.6), Word("Watch", 1.5, 1.7), Word("it", 1.75, 2.0), Word("now", 2.5, 2.9)],
    )
    take_index = Takes(script="script.md", model="m", output_format="mp3_44100_128")
    take_index.sections["01"] = Take(
        index=1, chapter="Open", file="01-open.mp3", words_file="01-open.words.json", hash="h",
        word_count=4, estimated_seconds=3.0, duration_seconds=3.5, speech_end_seconds=2.9,
        lead_seconds=0.5, spoken="Go. Watch it, now.",
    )  # fmt: skip
    take_index.save(p.takes_path)

    result = clip(p, 1, start=1.0, end=3.0, out="media/x.mp4", gain_db=-6, hold_seconds=0.4)
    clip_file = tmp_path / "media" / "x.mp4"
    assert (result.video, result.words_file) == (clip_file, tmp_path / "media" / "x.words.json")
    assert (result.first_frame, result.last_frame, result.start, result.end) == (25, 74, 1.0, 3.0)
    assert (result.hold_seconds, result.duration) == (0.4, 2.4)
    assert ffmpeg.probe_duration(clip_file) == pytest.approx(2.4, abs=0.05)
    red, blue = frames.luma_at(clip_file, 0.5)[0], frames.luma_at(clip_file, 1.5)[0]
    assert red > blue + 20, (red, blue)
    assert frames.luma_at(clip_file, 2.2)[0] == pytest.approx(blue, abs=3), "the hold does not show the last frame"
    loud = audio.rms_db(clip_file, 1.05, 0.4)
    assert loud > -40, "the take's tone is not at 1.0 s in the clip"  # The tone is about -24 dB before the -6 dB gain.
    assert audio.rms_db(clip_file, 0.1, 0.8) < -50, "sound before the tone"
    assert audio.rms_db(clip_file, 1.6, 0.7) < -50, "sound after the tone or in the hold"
    # Words wholly inside the span keep the script's spelling, shifted to the clip. "go" crosses the start.
    assert result.words == [Word("Watch", 1.0, 1.2), Word("it,", 1.25, 1.5)]
    assert result.cut_words == ["Go."]
    assert read_words(result.words_file) == result.words

    assert main(["-p", str(tmp_path), "clip", "1", "--start", "1", "--end", "3", "--out", "media/y.mp4"]) == 0
    out = capsys.readouterr().out
    assert "wrote media/y.mp4  (2.00s: frames 25 to 74 of sections/01.mp4, 1.00 to 3.00s, hold 0s, gain +0 dB)" in out
    assert "wrote media/y.words.json  (2 words)" in out
    assert audio.rms_db(tmp_path / "media" / "y.mp4", 1.05, 0.4) == pytest.approx(loud + 6, abs=1)

    # A span inside the lead is silent, and still has its full length.
    early = clip(p, 1, start=0.0, end=0.4, out="media/z.mp4")
    assert early.words == [] and ffmpeg.has_audio(early.video)
    assert ffmpeg.probe_duration(early.video) == pytest.approx(0.4, abs=0.05)
