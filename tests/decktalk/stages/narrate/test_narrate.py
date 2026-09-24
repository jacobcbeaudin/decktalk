"""The narrate stage: what one run writes, what it refuses, and what it reports."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk import ConfigError
from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.cli import main
from decktalk.cli.parser import build_parser
from decktalk.media import audio, ffmpeg
from decktalk.model import Project
from decktalk.pipeline import TakeStatus
from decktalk.speech import register_speech_provider
from decktalk.stages.narrate import narrate, take_name, text_hash, words_name
from decktalk.stages.record import jobs
from decktalk.verdicts import Verdict
from support.media_cards import write_tone_with_tail


@pytest.fixture
def offline(monkeypatch):
    """ffmpeg stubbed out, so a run without voice writes its files and measures nothing."""
    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.8)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)


def test_a_run_without_voice_writes_a_take_index_of_placeholders(project, offline):
    result = narrate(project, silent=True)
    assert result.synthesized == ["01", "02", "03"] and result.cached == []
    index = Takes.load(project.takes_path)
    assert index is not None and index.estimated and index.voiced_keys == []
    assert [take.voiced for take in index.sections.values()] == [False] * 3
    assert index.estimate_basis.endswith("wpm + declared pauses")
    assert result.takes == index and project.narration_path.exists()
    # The placeholders are cached by content too, so a second run writes nothing new.
    assert narrate(project, silent=True).cached == ["01", "02", "03"]


def test_a_voiced_run_indexes_one_take_per_section_and_checkpoints(project, voice, monkeypatch):
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    result = narrate(project)
    assert result.synthesized == ["01", "02", "03"] and len(voice.sent) == 3
    index = Takes.load(project.takes_path)
    assert index is not None and not index.estimated and index.voiced_keys == ["01", "02", "03"]
    for key, take in index.sections.items():
        assert take.file == f"{take.hash}.mp3" and (project.narration_dir / take.file).exists()
        assert take.chapter and take.index == int(key)
    # The second run pays nothing, because every take is already on disk under its own hash.
    assert narrate(project).cached == ["01", "02", "03"] and len(voice.sent) == 3


def _voiced(project, voice, monkeypatch) -> Takes:
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    narrate(project)
    index = Takes.load(project.takes_path)
    assert index is not None
    return index


def test_a_run_without_voice_refuses_only_the_sections_that_are_paid_for(project, voice, monkeypatch, offline):
    """The commonest edit is one new section in a film that is paid for, and it must cost nothing."""
    index = _voiced(project, voice, monkeypatch)
    paid = {key: (project.narration_dir / take.file).read_bytes() for key, take in index.sections.items()}
    with pytest.raises(ConfigError) as caught:
        narrate(project, silent=True)
    message = str(caught.value)
    assert "holds paid takes for section(s) 01, 02, 03" in message
    assert "Every section of this project is voiced already" in caught.value.hint
    assert "--force" not in message and "--force" not in caught.value.hint, "the refusal must not teach --force"
    for key, take in index.sections.items():
        assert (project.narration_dir / take.file).read_bytes() == paid[key]

    # A section nobody has paid for rehearses for nothing, and the paid takes stay in the index.
    del index.sections["03"]
    index.save(project.takes_path)
    with pytest.raises(ConfigError) as caught:
        narrate(project, silent=True)
    assert caught.value.hint == "Rehearse the sections nobody has paid for: --only 3."
    result = narrate(project, silent=True, only=[3])
    assert result.synthesized == ["03"]
    after = Takes.load(project.takes_path)
    assert after is not None and after.voiced_keys == ["01", "02"] and after.estimated
    assert not after.sections["03"].voiced and after.sections["01"].file == index.sections["01"].file


def test_the_result_carries_the_plan_the_price_and_the_index(project, voice, monkeypatch):
    _voiced(project, voice, monkeypatch)
    result = narrate(project)
    doc = json.loads(json.dumps(result.to_dict(project.root)))
    assert doc["voice"]["provider"] == "test-voice" and doc["cached"] == ["01", "02", "03"]
    assert [row["status"] for row in doc["sections"]] == [TakeStatus.CACHED.value] * 3
    assert doc["totals"]["characters_sent"] == 0 and doc["totals"]["estimated_cost"] == 0.0
    assert [row["voiced"] for row in doc["takes"]["sections"]] == [True] * 3
    # Every section runs for its 0.5 s lead, its take to its last sound at 1.0 s, and its 0.7 s tail.
    assert doc["takes"]["estimated"] is False and doc["takes"]["total_seconds"] == 6.6
    assert result.findings.certain == 0 and result.findings.uncertain == 0


def test_digits_in_the_script_are_reported_as_an_uncertain_finding(project, offline, base, make_project):
    toml, script = base
    counted = make_project(toml=toml, script=script.replace("A bowl.", "41 bowls."), name="counted")
    result = narrate(counted, silent=True)
    (row,) = result.rows
    assert row.verdict is Verdict.SPOKEN_SYMBOL and row.section == 1 and row.where == "script.md"
    assert result.findings.uncertain == 1 and result.findings.certain == 0


def test_the_stage_refuses_a_script_the_voice_must_not_receive(base, make_project):
    toml, script = base
    bad = make_project(toml=toml, script=script.replace("A ball.", "A ball. <!-- cut this -->"), name="bad")
    with pytest.raises(ConfigError, match="an HTML comment"):
        narrate(bad, silent=True)
    with pytest.raises(ConfigError, match="an HTML comment"):
        narrate(bad, dry_run=True)


@pytest.mark.parametrize("command", [["build", "--no-voice"], ["narrate", "--no-voice"]])
def test_cli_a_run_without_voice_over_paid_takes_is_an_error_and_not_a_finding(
    project, voice, monkeypatch, command, capsys
):
    """It stops the run before it does anything, which is exit 3, not a finding about the project."""
    _voiced(project, voice, monkeypatch)
    assert main([*command, "-p", str(project.root)]) == 3
    err = capsys.readouterr().err
    assert "error[CONFIG]: " in err and "holds paid takes for section(s) 01, 02, 03" in err
    assert build_parser().parse_args([*command, "--force"]).force


def test_a_voiced_run_refuses_a_script_that_still_holds_a_placeholder(make_project, base, voice, monkeypatch):
    """The voice would read the word NUMBER aloud and charge for it, so an unfilled placeholder stops the run."""
    monkeypatch.setattr(audio, "sound_end", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    toml, script = base
    project = make_project(toml=toml, script=script.replace("A ball.", "A [NUMBER] ball."), name="held")
    with pytest.raises(ConfigError, match=r"\['NUMBER'\]"):
        narrate(project)
    assert voice.sent == [], "nothing was sent before the refusal"
    assert narrate(project, allow_placeholders=True).synthesized == ["01", "02", "03"]


def test_an_only_that_matches_no_spoken_section_names_the_ones_there_are(project, offline):
    """A typed section number that is not in the script must not quietly do nothing."""
    with pytest.raises(ConfigError) as caught:
        narrate(project, silent=True, only=[9])
    assert str(caught.value) == "no spoken section matches [9]. The spoken sections are [1, 2, 3]."


def test_the_refusal_names_a_command_line_the_parser_accepts(project, voice, monkeypatch, offline):
    """The advice is pasted back, so it uses the flag the parser takes rather than a comma list."""
    index = _voiced(project, voice, monkeypatch)
    del index.sections["02"]
    del index.sections["03"]
    index.save(project.takes_path)
    with pytest.raises(ConfigError) as caught:
        narrate(project, silent=True)
    advice = caught.value.hint.rsplit("paid for: ", 1)[1].rstrip(".")
    assert advice == "--only 2 --only 3"
    assert build_parser().parse_args(["narrate", *advice.split()]).only == [2, 3]


@pytest.mark.media
def test_a_cached_take_with_a_short_tail_is_placed_and_never_rewritten_or_voiced_again(tmp_path):
    """A take whose own silence is shorter than min_tail_seconds gets the rest from the join, and keeps its bytes."""

    class NeverSpeaks:
        name = "never"

        def speak(self, request):
            raise AssertionError("a cached take was sent to the voice again")

        def cache_key(self, request):
            return "never-voice"

    register_speech_provider("never", lambda context: NeverSpeaks())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[narration]\nmin_tail_seconds = 0.9\n[voice]\nprovider = 'never'\n[[section]]\nnumber = 1\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    cfg = p.settings.narration
    p.narration_dir.mkdir(parents=True)
    _all, spoken = p.script_sections()
    seg = spoken[0]
    settings = p.voice.api_settings()
    digest = text_hash(seg, cfg, "never-voice", settings)
    # One second of tone for the speech, then the 0.4 s of silence the voice left after it.
    take = p.narration_dir / take_name(digest)
    ffmpeg.run(
        "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1", "-af", "apad=pad_dur=0.4",
        "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(take),
    )  # fmt: skip
    write_words(p.narration_dir / words_name(digest), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)])
    data = take.read_bytes()
    take_index = Takes(script="script.md", model="m", output_format=cfg.output_format)
    take_index.sections[seg.key] = Take(
        index=1, chapter="Open", file=take_name(digest), words_file=words_name(digest), hash=digest,
        word_count=2, estimated_seconds=1.0, duration_seconds=ffmpeg.probe_duration(take), speech_end_seconds=1.0,
    )  # fmt: skip
    take_index.save(p.takes_path)

    first = narrate(p)
    assert first.cached == [seg.key] and first.synthesized == []
    assert take.read_bytes() == data, "a cached take was rewritten"
    placed = Takes.load(p.takes_path).sections[seg.key]
    assert placed.hash == digest and placed.sound_end_seconds == pytest.approx(1.0, abs=0.03)
    # The section runs for its lead, its take to its last sound, and exactly min_tail_seconds after that.
    lead = p.lead_seconds(seg.key)
    assert (placed.lead_seconds, placed.tail_seconds) == (lead, 0.9)
    assert first.takes.span(seg.key) == pytest.approx(lead + 1.0 + 0.9, abs=0.03)
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(first.takes.total_seconds, abs=0.03)
    assert audio.rms_db(narration, lead + 1.05, 0.8) < -60, "the tail is not silent"

    second = narrate(p)
    assert second.cached == [seg.key] and second.synthesized == []
    assert Takes.load(p.takes_path).sections[seg.key] == placed, "a second run placed the take differently"
    assert take.read_bytes() == data


@pytest.mark.media
def test_a_renumbered_section_keeps_its_take_and_is_never_voiced_again(tmp_path):
    """A close that moves from section 2 to section 3 plays the same file, because a take is its content."""

    class NeverSpeaks:
        name = "never-renumbered"

        def speak(self, request):
            raise AssertionError("a renumbered take was sent to the voice again")

        def cache_key(self, request):
            return "never-voice"

    register_speech_provider("never-renumbered", lambda context: NeverSpeaks())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 3. Close\n\nGoodbye now.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[narration]\nmin_tail_seconds = 0.5\n[voice]\nprovider = 'never-renumbered'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 3\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    cfg = p.settings.narration
    p.narration_dir.mkdir(parents=True)
    _all, spoken = p.script_sections()
    settings = p.voice.api_settings()
    take_index = Takes(script="script.md", model="m", output_format=cfg.output_format)
    digests: dict[str, str] = {}
    for old_key, seg, freq in [("01", spoken[0], 440), ("02", spoken[1], 660)]:
        digest = text_hash(seg, cfg, "never-voice", settings)
        digests[old_key] = digest
        ffmpeg.run(
            "-f", "lavfi", "-i", f"sine=f={freq}:r=44100:d=1", "-af", "apad=pad_dur=1",
            "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(p.narration_dir / take_name(digest)),
        )  # fmt: skip
        write_words(p.narration_dir / words_name(digest), [Word("word", 0.0, 1.0)])
        take_index.sections[old_key] = Take(
            index=int(old_key), chapter=seg.title, file=take_name(digest), words_file=words_name(digest),
            hash=digest, word_count=2, estimated_seconds=1.0,
            duration_seconds=ffmpeg.probe_duration(p.narration_dir / take_name(digest)),
        )  # fmt: skip
    take_index.save(p.takes_path)

    result = narrate(p)
    assert result.synthesized == [] and result.cached == ["01", "03"]
    moved = Takes.load(p.takes_path)
    assert sorted(moved.sections) == ["01", "03"]
    # The close moved from section 2 to section 3 and plays the very same file, which nothing copied.
    assert moved.sections["03"].file == take_name(digests["02"]) and moved.sections["03"].index == 3
    assert sorted(f.name for f in p.narration_dir.glob("*.mp3")) == sorted(
        [take_name(digests["01"]), take_name(digests["02"]), "narration.mp3"]
    )
    assert result.takes.keys == ["01", "03"]
    assert narrate(p).cached == ["01", "03"]


@pytest.mark.media
@pytest.mark.parametrize("tail", [1.3, 0.2])
def test_narrate_twice_leaves_a_voiced_take_untouched(tmp_path, tail):
    """A take lands the same way on every run, and its own silence reaches none of its placement."""

    class ToneVoice:
        name = f"tone-{tail}"
        calls = 0

        def speak(self, request):
            ToneVoice.calls += 1
            src = write_tone_with_tail(tmp_path / "voice.mp3", tail=tail)
            return src.read_bytes(), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)]

        def cache_key(self, request):
            return f"tone-voice-{tail}"

    register_speech_provider(ToneVoice.name, lambda context: ToneVoice())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 2. Close\n\nBye.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        f"[narration]\nmin_tail_seconds = 1.3\nlead_seconds = 0\n[voice]\nprovider = '{ToneVoice.name}'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    first = narrate(p)
    assert first.synthesized == ["01", "02"] and ToneVoice.calls == 2
    entry = Takes.load(p.takes_path).sections["02"]
    take = p.narration_dir / entry.file
    # A long pause the voice left and a short one both give the section 1.3 s after its last sound.
    assert entry.sound_end_seconds == pytest.approx(1.0, abs=0.03)
    assert entry.span_seconds == pytest.approx(1.0 + 1.3, abs=0.03)
    data = take.read_bytes()

    second = narrate(p)
    assert second.cached == ["01", "02"] and second.synthesized == [] and ToneVoice.calls == 2
    again = Takes.load(p.takes_path).sections["02"]
    assert again == entry, "a cached take was placed differently"
    assert take.read_bytes() == data


@pytest.mark.media
def test_lead_and_tail_seconds_leave_a_voiced_take_cached(tmp_path):
    """A section's lead and tail are silence placed around its take in narration.mp3, and neither voices it again."""

    class ToneVoice:
        name = "tone-lead"
        calls = 0

        def speak(self, request):
            ToneVoice.calls += 1
            src = write_tone_with_tail(tmp_path / "voice.mp3", tail=0.8)
            return src.read_bytes(), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)]

        def cache_key(self, request):
            return "tone-lead-voice"

    register_speech_provider(ToneVoice.name, lambda context: ToneVoice())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 2. Close\n\nBye.\n", encoding="utf-8")
    base = (
        f"[narration]\nmin_tail_seconds = 0.7\nlead_seconds = 0\n[voice]\nprovider = '{ToneVoice.name}'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\npage = 'a.html'\n"
    )
    toml = tmp_path / "decktalk.toml"
    toml.write_text(base, encoding="utf-8")
    first = narrate(Project.load(tmp_path, environ={}))
    assert first.synthesized == ["01", "02"] and ToneVoice.calls == 2
    takes_path = tmp_path / "build" / "narration" / "takes.json"
    entry = Takes.load(takes_path).sections["02"]
    take = tmp_path / "build" / "narration" / entry.file
    data = take.read_bytes()
    before_start, before_span = first.takes.start("02"), first.takes.span("02")
    before_words = Project.load(tmp_path, environ={}).narration_words("02", entry.words_file, at=before_start)
    before_total = first.takes.total_seconds

    toml.write_text(base + "lead_seconds = 1.5\n", encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    second = narrate(p)
    assert second.synthesized == [] and second.cached == ["01", "02"] and ToneVoice.calls == 2
    # The take is byte-identical and still cached: a lead is silence placed before it, so only the row records it.
    assert take.read_bytes() == data
    after = Takes.load(takes_path).sections["02"]
    assert after == replace(entry, lead_seconds=1.5) and after.lead_seconds == 1.5
    start = second.takes.start("02")
    assert start == before_start
    assert second.takes.span("02") == pytest.approx(before_span + 1.5, abs=0.002)
    after_words = p.narration_words("02", after.words_file, at=start)
    assert [w.start for w in after_words] == [pytest.approx(w.start + 1.5, abs=0.001) for w in before_words]
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(before_total + 1.5, abs=0.03)
    assert audio.rms_db(narration, start + 0.1, 1.3) < -60, "the lead is not silent"
    assert audio.rms_db(narration, start + 1.55, 0.4) > -30, "the take does not follow the lead"

    toml.write_text(base + "lead_seconds = 1.5\ntail_seconds = 2\n", encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    third = narrate(p)
    assert third.synthesized == [] and third.cached == ["01", "02"] and ToneVoice.calls == 2
    assert take.read_bytes() == data
    tailed = Takes.load(takes_path).sections["02"]
    assert tailed == replace(after, tail_seconds=2.0)
    # The section now runs 2 s past its last sound where it ran 0.7 s, and section 01 keeps its own tail.
    assert third.takes.span("02") == pytest.approx(second.takes.span("02") + 1.3, abs=0.002)
    assert Takes.load(takes_path).sections["01"].tail_seconds == 0.7
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(third.takes.total_seconds, abs=0.03)
    speech_ends = start + 1.5 + tailed.sound_end_seconds
    assert audio.rms_db(narration, speech_ends + 0.05, 1.9) < -60, "the tail is not silent"
    fourth = narrate(p)
    assert fourth.synthesized == [] and ToneVoice.calls == 2
    assert Takes.load(takes_path).sections["02"] == tailed


def _take_with_a_noisy_tail(path: Path, *, speech: float) -> None:
    """`speech` seconds of tone, then 0.3 s of noise just under the silence threshold, the way a voice breathes out.

    Re-encoding such a file lets one sample near its end cross the threshold, so any placement that measured
    a file some run had rewritten would drift. This seed and level sit there on purpose.
    """
    ffmpeg.run(
        "-f", "lavfi", "-i", f"sine=f=440:r=44100:d={speech}",
        "-f", "lavfi", "-i", "anoisesrc=r=44100:a=0.0125:c=white:seed=6:d=0.3",
        "-filter_complex", "[0:a]volume=0.25[s];[s][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
        "-c:a", "libmp3lame", "-b:a", "128k", str(path),
    )  # fmt: skip


@pytest.mark.media
def test_changing_one_section_leaves_its_neighbours_placed_as_they_were(tmp_path):
    """A rebuild after one section's words change keeps every other section's length, words and recording key.

    A take's place in the narration is a pure function of the take and its own section's settings, so a
    section that was voiced by the last run and reused by this one lands exactly where it did.
    """

    class BreathingVoice:
        name = "breathing-voice"
        calls: list[str] = []

        def speak(self, request):
            BreathingVoice.calls.append(request.text)
            words = request.text.split()
            speech = 1.2 if len(words) <= 3 else 1.6
            src = tmp_path / "voice.mp3"
            _take_with_a_noisy_tail(src, speech=speech)
            per = speech / len(words)
            timed = [
                Word(w.strip(".,"), round(i * per, 3), round((i + 1) * per - 0.02, 3)) for i, w in enumerate(words)
            ]
            return src.read_bytes(), timed

        def cache_key(self, request):
            return self.name

    register_speech_provider(BreathingVoice.name, lambda context: BreathingVoice())
    (tmp_path / "a.html").write_text("<!doctype html><title>a</title>", encoding="utf-8")
    script = tmp_path / "script.md"
    script.write_text("## 1. Open\n\nHello there.\n\n## 2. Middle\n\nA middle line.\n\n## 3. Close\n\nBye now.\n")
    (tmp_path / "decktalk.toml").write_text(
        f"[narration]\nmin_tail_seconds = 1.3\n[voice]\nprovider = '{BreathingVoice.name}'\n"
        + "".join(f"[[section]]\nnumber = {n}\npage = 'a.html'\nscene = {n}\n" for n in (1, 2, 3)),
        encoding="utf-8",
    )

    def placed() -> dict[str, tuple[float | None, list[Word], str]]:
        p = Project.load(tmp_path, environ={})
        takes = p.takes()
        assert takes is not None
        keys = {job.section.key: job.input_hash for job in jobs(p, None, None, use_cues=False)}
        return {
            key: (takes.span(key), p.section_words(key, takes.sections[key].words_file), keys[key])
            for key in ("01", "02", "03")
        }

    first = narrate(Project.load(tmp_path, environ={}))
    assert first.synthesized == ["01", "02", "03"]
    before = placed()

    script.write_text(script.read_text(encoding="utf-8").replace("A middle line.", "A longer middle line than before."))
    second = narrate(Project.load(tmp_path, environ={}))
    assert second.synthesized == ["02"] and second.cached == ["01", "03"]
    after = placed()
    assert after["02"] != before["02"]
    for key in ("01", "03"):
        assert after[key] == before[key], f"section {key} moved although only section 02 changed"
