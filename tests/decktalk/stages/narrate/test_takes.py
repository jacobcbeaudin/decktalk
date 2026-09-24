"""Writing one take, placing it by its own section's lead and tail, and joining every take into one track."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.media import audio, ffmpeg
from decktalk.stages.align import align
from decktalk.stages.narrate import estimated_words, join_takes, placed
from decktalk.stages.narrate.plan import silent_hash, take_name, words_name
from decktalk.stages.narrate.takes import index_cached_take, write_silent_take, write_voiced_take

from decktalk.model.script import Segment  # isort: skip


def _segment(text: str = "one two three four five", index: int = 1) -> Segment:
    return Segment(index=index, title="T", slug="t", text=text)


def test_estimated_words_span_the_whole_take():
    """A take carries neither its lead nor its tail, so the estimated words fill it from zero to its end."""
    words = estimated_words(_segment(), 5.0)
    assert [w.word for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0].start == 0.0 and words[-1].end == pytest.approx(4.98)


def test_a_silent_take_is_written_under_its_content_hash(project, monkeypatch):
    seg = project.script_sections()[1][0]
    digest = silent_hash(seg, project.settings.narration)
    project.narration_dir.mkdir(parents=True)
    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.6)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    row = write_silent_take(project, seg, "Open", digest)
    assert row.file == take_name(digest) and row.words_file == words_name(digest)
    assert row.voiced is False and row.duration_seconds == 2.0 and row.index == 1
    # The lead and the tail are placement, so they are on the row and never in the take.
    assert (row.lead_seconds, row.sound_end_seconds, row.tail_seconds, row.span_seconds) == (0.5, 1.6, 0.7, 2.8)
    assert (project.narration_dir / take_name(digest)).read_bytes() == b"clicks"


def test_a_voiced_take_is_the_voice_bytes_and_nothing_else(project, voice, monkeypatch):
    from decktalk.speech import SpeechRequest

    seg = project.script_sections()[1][0]
    project.narration_dir.mkdir(parents=True)
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 2.9)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    request = SpeechRequest(text=seg.text, model="m", voice_settings={}, output_format="mp3")
    row = write_voiced_take(project, voice, seg, "Open", "abc123", request)
    assert voice.sent == [request] and row.voiced is True and row.hash == "abc123"
    assert (project.narration_dir / "abc123.mp3").read_bytes() == b"mp3 " + seg.text.encode()
    assert (row.sound_end_seconds, row.tail_seconds, row.span_seconds) == (2.9, 0.7, 4.1)
    assert row.spoken == seg.spoken


def test_a_take_is_placed_the_same_whether_it_was_voiced_or_found_cached(project, voice, monkeypatch):
    """The path that wrote the row reaches none of its numbers, and neither does the row that was there before."""
    from decktalk.speech import SpeechRequest

    seg = project.script_sections()[1][0]
    project.narration_dir.mkdir(parents=True)
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 2.9)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    request = SpeechRequest(text=seg.text, model="m", voice_settings={}, output_format="mp3")
    voiced = write_voiced_take(project, voice, seg, "Open", "abc123", request)
    data = (project.narration_dir / "abc123.mp3").read_bytes()
    cached = index_cached_take(project, seg, "Open", "abc123", voiced=True)
    assert cached == voiced and placed(project, "01", cached) == voiced
    assert (project.narration_dir / "abc123.mp3").read_bytes() == data, "a cached take was rewritten"


def test_a_sections_own_lead_and_tail_replace_the_narration_defaults(project, monkeypatch):
    from decktalk.model import Project

    assert (project.lead_seconds("02"), project.tail_seconds("02")) == (0.5, 0.7)
    toml = (project.root / "decktalk.toml").read_text(encoding="utf-8")
    (project.root / "decktalk.toml").write_text(
        toml.replace("number = 2\n", "number = 2\nlead_seconds = 0\ntail_seconds = 2.5\n")
    )
    reloaded = Project.load(project.root, environ={})
    assert (reloaded.lead_seconds("02"), reloaded.tail_seconds("02")) == (0.0, 2.5)
    # The first section is placed like every other: being first gives it no silence of its own.
    assert (reloaded.lead_seconds("01"), reloaded.tail_seconds("01")) == (0.5, 0.7)


def _index(project, digest: str, key: str, *, lead: float = 0.5, end: float = 3.0, tail: float = 0.0) -> Take:
    write_words(project.takes_dir / words_name(digest), [Word("a", 0.5, 0.9), Word("b", 1.0, 1.6)])
    return Take(
        int(key), key, take_name(digest), words_name(digest), digest, 2, 1.0, 3.0,
        sound_end_seconds=end, lead_seconds=lead, tail_seconds=tail,
    )  # fmt: skip


def test_the_join_puts_each_sections_silence_before_its_take(project, monkeypatch):
    """A lead is silence in narration.mp3 before the take, so the words move and the take does not."""
    toml = (project.root / "decktalk.toml").read_text(encoding="utf-8")
    (project.root / "decktalk.toml").write_text(toml.replace("number = 2\n", "number = 2\nlead_seconds = 1.25\n"))
    (project.root / "cues.json").write_text(
        json.dumps({"sections": {"2": {"cues": [{"cue": "2.0", "on": "$start"}, {"cue": "2.1", "on": "b"}]}}})
    )
    from decktalk.model import Project

    p = Project.load(project.root, environ={})
    p.narration_dir.mkdir(parents=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.sections = {"01": _index(p, "h1", "01"), "02": _index(p, "h2", "02", lead=1.25), "03": _index(p, "h3", "03")}
    index.save(p.takes_path)
    joined: dict = {}
    monkeypatch.setattr(
        audio,
        "concat_audio",
        lambda parts, out, **kw: joined.update(files=[p.path.name for p in parts], leads=[p.lead for p in parts]),
    )
    join_takes(p, index, p.script_sections()[1])
    # Every section carries [narration] lead_seconds, and section 2 its own lead_seconds in its place.
    assert joined == {"files": ["h1.mp3", "h2.mp3", "h3.mp3"], "leads": [0.5, 1.25, 0.5]}
    assert (index.start("01"), index.end("01")) == (0.0, 3.5)
    assert (index.start("02"), index.end("02"), index.span("02")) == (3.5, 7.75, 4.25)
    two_words = p.narration_words("02", index.sections["02"].words_file, at=index.start("02"))
    assert [(w.start, w.end) for w in two_words] == [(5.25, 5.65), (5.75, 6.35)]
    saved = Takes.load(p.takes_path)
    assert saved is not None and saved.sections["02"].lead_seconds == 1.25
    # Cue times count from the section start, so the lead moves each word cue and $start stays at 0.
    assert align(p).cue_times.times("02") == {"2.0": 0.0, "2.1": 2.25}


def test_a_shared_cache_dir_holds_the_takes_and_never_one_project_index(make_project, base, tmp_path, monkeypatch):
    """A take is named by content and safe to share, while the index and the joined track are one project's own."""
    from decktalk.stages.narrate import narrate

    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.8)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    toml, script = base
    shared = tmp_path / "takes"
    toml = toml.replace("[narration]\n", f'[narration]\ncache_dir = "{shared}"\n')
    one = make_project(toml=toml, script=script, name="one")
    narrate(one, silent=True)
    assert one.takes_dir == shared and one.takes_path.parent == one.root / "build" / "narration"
    assert sorted(p.suffix for p in shared.iterdir()) == [".json", ".json", ".json", ".mp3", ".mp3", ".mp3"]
    assert not (shared / "takes.json").exists() and not (shared / "narration.mp3").exists()

    # A second project shares the takes and keeps its own index, so neither run replaces the other's.
    two = make_project(toml=toml, script=script.replace("## 3. Close", "## 3. A different close"), name="two")
    result = narrate(two, silent=True)
    assert result.cached == ["01", "02", "03"] and Takes.load(one.takes_path) != Takes.load(two.takes_path)


def test_a_take_in_a_shared_cache_is_placed_like_any_other_and_never_rewritten(
    make_project, base, tmp_path, monkeypatch
):
    """No stage rewrites a take, so a shared one needs no rule of its own: its silence is placed around it."""
    toml, script = base
    shared = tmp_path / "shared"
    shared.mkdir()
    project = make_project(toml=toml.replace("[narration]\n", f'[narration]\ncache_dir = "{shared}"\n'), name="one")
    seg = project.script_sections()[1][0]
    (shared / "abc123.mp3").write_bytes(b"mp3")
    write_words(shared / "abc123.words.json", [Word("a", 0.1, 0.4)])
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 2.4)
    row = index_cached_take(project, seg, "Open", "abc123", voiced=True)
    assert (row.lead_seconds, row.sound_end_seconds, row.tail_seconds, row.span_seconds) == (0.5, 2.4, 0.7, 3.6)
    assert (shared / "abc123.mp3").read_bytes() == b"mp3"


def test_the_tail_reaches_the_join_the_clock_and_the_total(make_project, base, monkeypatch):
    """The tail is silence after the take's last sound, so every reader counts it and the join ends the take there."""
    toml, script = base
    project = make_project(toml=toml, name="tailed")
    project.narration_dir.mkdir(parents=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    for key in ("01", "02", "03"):
        index.sections[key] = _index(project, f"h{key}", key, end=2.5, tail=0.7)
    index.save(project.takes_path)
    # Every take sounds for 2.5 s of its 3.0 s, after a 0.5 s lead and before a 0.7 s tail.
    assert index.total_seconds == pytest.approx(3 * 3.7)
    joined: dict = {}
    monkeypatch.setattr(audio, "concat_audio", lambda parts, out, **kw: joined.update(parts=parts))
    join_takes(project, index, project.script_sections()[1])
    # Each take plays to its sound end and no further, and its tail is silence placed after that.
    assert [(p.lead, p.play, p.tail) for p in joined["parts"]] == [(0.5, 2.5, 0.7)] * 3
    assert (index.start("01"), index.end("01")) == (0.0, 3.7)
    assert (index.start("02"), index.end("02")) == (3.7, 7.4)


@pytest.mark.media
def test_the_join_cuts_or_pads_each_take_to_its_length(tmp_path):
    """The filter graph places every take to the sample, so the joined track is as long as the clock says.

    This one runs the real ffmpeg, which `decktalk install` fetches, so it carries the media marker
    and the default suite leaves it out.
    """
    from decktalk.media import ffmpeg as real_ffmpeg

    one, two = tmp_path / "a.mp3", tmp_path / "b.mp3"
    for path in (one, two):
        audio.write_clicks(path, 1.0, [0.1], sample_rate=48000, bitrate="128k")
    out = tmp_path / "joined.mp3"
    # The first take plays all of its 1.0 s and gets 0.25 s of tail, and the second is cut at 0.6 s.
    parts = [audio.Placement(one, lead=0.5, play=1.0, tail=0.25), audio.Placement(two, lead=0.25, play=0.6)]
    audio.concat_audio(parts, out, bitrate="128k", sample_rate=48000)
    assert real_ffmpeg.decoded_duration(out, sample_rate=48000) == pytest.approx(2.6, abs=0.03)
