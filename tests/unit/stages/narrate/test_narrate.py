"""The narrate stage: what one run writes, what it refuses, and what it reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk import ConfigError
from decktalk.artifacts import Takes
from decktalk.cli import main
from decktalk.cli.parser import build_parser
from decktalk.media import audio, ffmpeg
from decktalk.pipeline import TakeStatus
from decktalk.stages.narrate import narrate
from decktalk.verdicts import Verdict


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
