"""Writing one take, padding its tail, and joining every take into one narration track."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Timeline, Word, write_words
from decktalk.media import audio, ffmpeg
from decktalk.settings import Settings
from decktalk.stages.align import align
from decktalk.stages.narrate import build_timeline, ensure_tail, estimated_words, section_config
from decktalk.stages.narrate.plan import silent_hash, take_name, words_name
from decktalk.stages.narrate.takes import index_cached_take, write_silent_take, write_voiced_take

from decktalk.model.script import Segment  # isort: skip


def _segment(text: str = "one two three four five", index: int = 1) -> Segment:
    return Segment(index=index, title="T", slug="t", text=text)


def test_estimated_words_span_the_take_and_leave_its_tail():
    """A take carries no lead any more, so the first estimated word starts at zero."""
    words = estimated_words(_segment(), 5.0, Settings().narration)
    assert [w.word for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0].start == 0.0 and words[-1].end < 5.0 - 0.65


def test_a_silent_take_is_written_under_its_content_hash(project, monkeypatch):
    seg = project.script_sections()[1][0]
    digest = silent_hash(seg, project.settings.narration)
    project.narration_dir.mkdir(parents=True)
    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    row = write_silent_take(project, seg, "Open", digest)
    assert row.file == take_name(digest) and row.words_file == words_name(digest)
    assert row.voiced is False and row.duration_seconds == 2.0 and row.index == 1
    # The opening silence is the section's lead, joined in later, and is not in the take.
    assert row.lead_seconds == 0.7
    assert (project.narration_dir / take_name(digest)).read_bytes() == b"clicks"


def test_a_voiced_take_is_the_voice_bytes_with_its_tail(project, voice, monkeypatch):
    from decktalk.speech import SpeechRequest

    seg = project.script_sections()[1][0]
    project.narration_dir.mkdir(parents=True)
    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 0.1)
    padded: list[float] = []
    monkeypatch.setattr(audio, "pad_tail", lambda path, seconds, bitrate: padded.append(seconds))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    request = SpeechRequest(text=seg.text, model="m", voice_settings={}, output_format="mp3")
    row = write_voiced_take(project, voice, seg, "Open", "abc123", request)
    assert voice.sent == [request] and row.voiced is True and row.hash == "abc123"
    assert padded == [row.tail_padded_seconds] and row.tail_padded_seconds == pytest.approx(0.65)
    assert (project.narration_dir / "abc123.mp3").read_bytes().startswith(b"mp3 ")
    assert row.speech_end_seconds is not None and row.spoken == seg.spoken


def test_a_cached_take_is_padded_once_and_never_again(project, monkeypatch):
    seg = project.script_sections()[1][0]
    project.narration_dir.mkdir(parents=True)
    (project.narration_dir / "abc123.mp3").write_bytes(b"mp3")
    write_words(project.narration_dir / "abc123.words.json", [Word("a", 0.1, 0.4)])
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 0.1)
    padded: list[float] = []
    monkeypatch.setattr(audio, "pad_tail", lambda path, seconds, bitrate: padded.append(seconds))
    first = index_cached_take(project, seg, "Open", "abc123", voiced=True, previous=None)
    index = Takes(script="script.md", model="m", output_format="mp3", sections={"01": first})
    index.save(project.takes_path)
    # The second run measures a tail within one mp3 frame of min_tail_seconds and leaves it alone.
    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 0.68)
    again = index_cached_take(project, seg, "Open", "abc123", voiced=True, previous=index)
    assert len(padded) == 1 and again.tail_padded_seconds == first.tail_padded_seconds
    # The earlier padding is found by the digest, so the twin of a shared take is not padded twice.
    twin = project.script_sections()[1][1]
    assert index_cached_take(project, twin, "Middle", "abc123", voiced=True, previous=index).tail_padded_seconds
    assert len(padded) == 1


def test_a_section_tail_seconds_replaces_min_tail_seconds(project, monkeypatch):
    seg = project.script_sections()[1][1]
    assert section_config(project, seg).min_tail_seconds == 0.7
    toml = (project.root / "decktalk.toml").read_text(encoding="utf-8")
    (project.root / "decktalk.toml").write_text(toml.replace("number = 2\n", "number = 2\ntail_seconds = 2.5\n"))
    from decktalk.model import Project

    reloaded = Project.load(project.root, environ={})
    later = reloaded.script_sections()[1][1]
    assert section_config(reloaded, later).min_tail_seconds == 2.5
    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 1.0)
    padded: list[float] = []
    monkeypatch.setattr(audio, "pad_tail", lambda path, seconds, bitrate: padded.append(seconds))
    # A 1.0 s tail passes 0.7 but not 2.5, which it reaches plus the slack.
    assert ensure_tail(Path("x.mp3"), section_config(project, seg)) == 0.0
    assert ensure_tail(Path("x.mp3"), section_config(reloaded, later)) == pytest.approx(1.55)
    assert padded == [pytest.approx(1.55)]


def _index(project, digest: str, key: str, lead: float = 0.0) -> Take:
    write_words(project.narration_dir / words_name(digest), [Word("a", 0.5, 0.9), Word("b", 1.0, 1.6)])
    return Take(int(key), key, take_name(digest), words_name(digest), digest, 2, 1.0, 3.0, lead_seconds=lead)


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
    index.sections = {"01": _index(p, "h1", "01"), "02": _index(p, "h2", "02"), "03": _index(p, "h3", "03")}
    index.save(p.takes_path)
    joined: dict = {}
    monkeypatch.setattr(
        audio, "concat_audio", lambda files, out, **kw: joined.update(files=[f.name for f in files], leads=kw["leads"])
    )
    monkeypatch.setattr(
        ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 11.95 if path.name == "narration.mp3" else 3.0
    )
    timeline = build_timeline(p, index, p.script_sections()[1])
    # Section 1 carries [narration] opening_silence_seconds, and section 2 its own lead_seconds.
    assert joined == {"files": ["h1.mp3", "h2.mp3", "h3.mp3"], "leads": [0.7, 1.25, 0.0]}
    one, two = timeline.sections["01"], timeline.sections["02"]
    assert (one.start, one.end, one.lead_seconds) == (0.0, 3.7, 0.7)
    assert (two.start, two.end, two.duration, two.lead_seconds) == (3.7, 7.95, 4.25, 1.25)
    assert [(w.start, w.end) for w in two.words] == [(5.45, 5.85), (5.95, 6.55)]
    saved = Timeline.load(p.timeline_path)
    assert saved is not None and saved.sections["02"].lead_seconds == 1.25
    # Cue times count from the section start, so the lead moves each word cue and $start stays at 0.
    assert align(p).cue_times.times("02") == {"2.0": 0.0, "2.1": 2.25}


def test_a_shared_cache_dir_holds_the_takes_and_never_one_project_index(make_project, base, tmp_path, monkeypatch):
    """A take is named by content and safe to share, while the index and the joined track are one project's own."""
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import narrate

    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
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


def test_a_take_in_a_shared_cache_is_never_rewritten_by_the_project_that_reads_it(
    make_project, base, tmp_path, monkeypatch
):
    """`min_tail_seconds` is not part of the content hash, so it may not reshape a file others read."""
    toml, script = base
    shared = tmp_path / "shared"
    shared.mkdir()
    project = make_project(toml=toml.replace("[narration]\n", f'[narration]\ncache_dir = "{shared}"\n'), name="one")
    seg = project.script_sections()[1][0]
    (shared / "abc123.mp3").write_bytes(b"mp3")
    write_words(shared / "abc123.words.json", [Word("a", 0.1, 0.4)])
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 0.1)
    monkeypatch.setattr(audio, "pad_tail", lambda *a, **kw: pytest.fail("a shared take was rewritten"))
    row = index_cached_take(project, seg, "Open", "abc123", voiced=True, previous=None)
    # The silence the section still needs is joined in after the take, exactly as a lead is before it.
    assert row.tail_padded_seconds == 0.0 and row.tail_joined_seconds == pytest.approx(0.65)
    assert (shared / "abc123.mp3").read_bytes() == b"mp3"


def test_a_joined_tail_reaches_the_join_the_clock_and_the_total(make_project, base, tmp_path, monkeypatch):
    """Silence a shared take may not carry itself has to arrive somewhere, so every reader counts it."""
    toml, script = base
    shared = tmp_path / "shared"
    shared.mkdir()
    project = make_project(toml=toml.replace("[narration]\n", f'[narration]\ncache_dir = "{shared}"\n'), name="joined")
    project.narration_dir.mkdir(parents=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    for key in ("01", "02", "03"):
        row = _index(project, f"h{key}", key, lead=0.7 if key == "01" else 0.0)
        row.tail_joined_seconds = 0.5
        index.sections[key] = row
    index.save(project.takes_path)
    # Every take runs 3.0 s, section 1 carries the 0.7 s opening lead, and each carries a 0.5 s tail.
    assert index.total_seconds == pytest.approx(3 * 3.5 + 0.7)
    joined: dict = {}
    monkeypatch.setattr(
        audio,
        "concat_audio",
        lambda files, out, **kw: joined.update(leads=kw["leads"], tails=kw["tails"]),
    )
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 3.0)
    timeline = build_timeline(project, index, project.script_sections()[1])
    assert joined == {"leads": [0.7, 0.0, 0.0], "tails": [0.5, 0.5, 0.5]}
    one, two = timeline.sections["01"], timeline.sections["02"]
    assert (one.start, one.end) == (0.0, 4.2) and (two.start, two.end) == (4.2, 7.7)


def test_the_join_pads_after_a_take_it_does_not_rewrite(tmp_path):
    """The filter graph has to add both silences, so the joined track is as long as the clock says."""
    from decktalk.media import ffmpeg as real_ffmpeg

    one, two = tmp_path / "a.mp3", tmp_path / "b.mp3"
    for path in (one, two):
        audio.write_clicks(path, 1.0, [0.1], sample_rate=48000, bitrate="128k")
    out = tmp_path / "joined.mp3"
    audio.concat_audio([one, two], out, bitrate="128k", sample_rate=48000, leads=[0.5, 0.0], tails=[0.25, 0.75])
    assert real_ffmpeg.decoded_duration(out, sample_rate=48000) == pytest.approx(3.5, abs=0.05)
