"""The align stage: cue phrases become cue times, and the two files are checked against each other."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk import ConfigError, Project
from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.cli import main
from decktalk.model.cues import Cue, find_phrase
from decktalk.stages.align import UnknownCueError, align, resolve_cue, unknown_hint, unknown_message
from decktalk.verdicts import Verdict

TOML = "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"
WORDS = [Word("Hello", 0.5, 0.9), Word("there", 1.0, 1.4)]


def _project(tmp_path: Path, html: str, cues: dict, words: list[Word] = WORDS) -> Project:
    """Section 0 a clip and section 1 a page, with one take of `words` for section 1."""
    root = tmp_path / "proj"
    (root / "deck").mkdir(parents=True)
    (root / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (root / "deck" / "index.html").write_text(html, encoding="utf-8")
    (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    project = Project.load(root, environ={})
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.sections["01"] = Take(1, "A", "h.mp3", "h.words.json", "h", 2, 1.0, 3.0)
    index.save(project.takes_path)
    write_words(project.narration_dir / "h.words.json", words)
    return project


def test_a_phrase_resolves_by_occurrence_and_by_the_two_edges():
    words = [Word("Hello", 0.0, 0.3), Word("one", 0.4, 0.6), Word("in", 0.7, 0.8), Word("ten", 0.9, 1.2),
             Word("one", 1.5, 1.7)]  # fmt: skip
    assert find_phrase(words, "one in ten") == 1
    assert find_phrase(words, "one", occurrence=2) == 4
    assert find_phrase(words, "missing") is None
    assert resolve_cue(Cue("a", "in ten", offset=0.1), words) == 0.8
    assert resolve_cue(Cue("a", "$end"), words) == 1.7
    assert resolve_cue(Cue("a", "$start", offset=2), words) == 2.0


def test_a_cue_time_is_an_object_with_the_word_behind_it(tmp_path):
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "there", "offset": 0.25}]}}
    project = _project(tmp_path, "<b data-cue='1.1a'></b>", cues)
    result = align(project)
    (row,) = result.cue_times.sections["01"]
    assert (row.cue, row.on, row.at, row.word_at) == ("1.1a", "there", 1.25, 1.0)
    assert result.cue_times.query("01") == "1.1a@1.25"
    assert json.loads(project.cue_times_path.read_text(encoding="utf-8")) == {
        "estimated": False,
        "sections": {"01": [{"cue": "1.1a", "on": "there", "at": 1.25, "word_at": 1.0}]},
    }


def test_a_cue_no_page_reveals_is_reported_and_stops_the_build(tmp_path):
    cues = {
        "0": {"cues": [{"cue": "0.clip", "on": "$start"}]},
        "1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]},
    }
    project = _project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    with pytest.raises(UnknownCueError) as caught:
        align(project)
    # The message is one sentence and the next action is the hint, which is where a skill reads it.
    assert str(caught.value) == (
        "1 cue id(s) in cues.json appear nowhere in the page that plays them, so the page would never reveal them."
    )
    hint = caught.value.hint or ""
    assert hint.startswith('Add data-cue="4.1answer" to the slide in deck/index.html')
    assert "--allow-unknown-cues" in hint and "section 01: 4.1answer: not in deck/index.html" in hint
    assert isinstance(caught.value, ConfigError) and caught.value.result.unknown == 1
    assert project.cue_times_path.exists(), "cue-times.json is written before the stop"
    result = align(project, allow_unknown_cues=True)
    assert result.unknown == 1 and result.unresolved == 0 and result.findings.certain == 0


def test_an_element_no_cue_fires_is_reported_the_other_way(tmp_path):
    """The check runs both ways: a cue with no element, and an element with no cue."""
    html = '<b data-cue="1.1a"></b><i data-cue="1.2forgotten"></i>'
    project = _project(tmp_path, html, {"1": {"cues": [{"cue": "1.1a", "on": "hello"}]}})
    result = align(project)
    assert result.uncued == 1 and result.findings.certain == 1
    (row,) = [r for r in result.sections[0].rows if r.verdict is Verdict.UNCUED_ELEMENT]
    assert row.cue == "1.2forgotten" and "has no entry in cues.json" in row.detail
    assert result.to_dict(project.root)["uncued"] == 1


def test_a_section_with_no_cues_at_all_still_reports_its_uncued_elements(tmp_path):
    project = _project(tmp_path, '<i data-cue="1.9alone"></i>', {})
    result = align(project)
    assert result.uncued == 1 and [s.key for s in result.sections] == ["01"]
    assert result.sections[0].rows[0].verdict is Verdict.UNCUED_ELEMENT


def test_an_unresolved_phrase_and_a_short_section_are_counted_apart(tmp_path):
    cues = {"1": {"min_seconds": 9, "cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "1.1b", "on": "missing phrase"}]}}
    project = _project(tmp_path, "<b data-cue='1.1a'></b><b data-cue='1.1b'></b>", cues)
    result = align(project)
    doc = json.loads(json.dumps(result.to_dict(project.root)))
    assert doc["estimated"] is False and doc["cue_times_file"] == "build/cue-times.json"
    assert (doc["unresolved"], doc["unknown"], doc["uncued"]) == (1, 0, 0)
    assert result.findings.certain == 1 and result.findings.uncertain == 1
    (section,) = doc["sections"]
    assert section["speech_end_seconds"] == 1.4 and section["min_seconds"] == 9.0 and section["cues"] == {"1.1a": 0.5}
    assert [(n["code"], n["cue"], n["detail"]) for n in section["notes"]] == [
        (Verdict.UNRESOLVED.name, "1.1b", "phrase not found: 'missing phrase'"),
        (Verdict.NOTE.name, None, "speech 1.4s is 7.6s shorter than the visuals need"),
    ]


def test_a_repeated_phrase_warns_unless_the_cue_names_its_occurrence(tmp_path, capsys):
    cues = {
        "1": {
            "cues": [
                {"cue": "1.1step", "on": "every step"},
                {"cue": "1.1later", "on": "every step", "occurrence": 2},
                {"cue": "1.1once", "on": "there"},
                {"cue": "1.1case", "on": "Every", "case_sensitive": True},
                {"cue": "1.1end", "on": "$end"},
            ]
        }
    }
    ids = "".join(f"<b data-cue='{c}'></b>" for c in ("1.1step", "1.1later", "1.1once", "1.1case", "1.1end"))
    words = [Word("Every", 0.5, 0.8), Word("step", 0.9, 1.2), Word("there.", 1.3, 1.6),
             Word("For", 36.0, 36.2), Word("every", 36.3, 36.6), Word("step!", 36.7, 37.1)]  # fmt: skip
    project = _project(tmp_path, ids, cues, words)
    index = project.takes()
    assert index is not None
    index.sections["01"].duration_seconds = 38.0
    index.save(project.takes_path)
    result = align(project)
    (section,) = result.sections
    detail = (
        "'every step' occurs 2 times in this section, at 0.50s, 36.30s. The cue uses the first. "
        'Set "occurrence" to choose one.'
    )
    # Only the cue that names no occurrence is ambiguous, and a case-sensitive phrase counts its own case alone.
    assert [(r.cue, r.verdict, r.detail) for r in section.rows] == [("1.1step", Verdict.NOTE, detail)]
    assert section.resolved[0].at == 0.5 and section.resolved[1].at == 36.3 and result.unresolved == 0
    assert main(["-p", str(project.root), "align", "--strict"]) == 0  # A warning, not a finding.
    assert f"! 1.1step: {detail}" in capsys.readouterr().out


def test_cli_align_reports_both_directions_as_findings(tmp_path, capsys):
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]}}
    project = _project(tmp_path, '<b data-cue="1.1a"></b><b data-cue="1.9alone"></b>', cues)
    # Without --allow-unknown-cues the command still prints its JSON and exits 1.
    assert main(["-p", str(project.root), "align", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"]["certain"] == 2
    assert doc["align"]["unknown"] == 1 and doc["align"]["uncued"] == 1
    assert main(["-p", str(project.root), "align", "--json", "--allow-unknown-cues"]) == 1
    assert json.loads(capsys.readouterr().out)["findings"]["certain"] == 1


def test_a_deck_with_no_cues_file_runs_its_own_timing_and_is_not_faulted(tmp_path):
    """A page that keeps its built-in timing cues nothing, so no element on it is waiting for a phrase."""
    root = tmp_path / "proj"
    (root / "deck").mkdir(parents=True)
    (root / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (root / "deck" / "index.html").write_text('<b data-cue="1.1a"></b><i data-cue="1.2b"></i>', encoding="utf-8")
    project = Project.load(root, environ={})
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.sections["01"] = Take(1, "A", "h.mp3", "h.words.json", "h", 2, 1.0, 3.0)
    index.save(project.takes_path)
    write_words(project.narration_dir / "h.words.json", WORDS)
    assert not project.cues.exists()
    result = align(project)
    assert result.uncued == 0 and result.findings.certain == 0


def test_a_page_section_with_no_take_reports_its_cues_rather_than_dropping_them(tmp_path):
    """A cue nobody resolved must be counted, because the recorder would otherwise play that section blind."""
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "there"}]}}
    project = _project(tmp_path, "<b data-cue='1.1a'></b>", cues)
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.save(project.takes_path)
    result = align(project)
    assert result.unresolved == 1 and result.findings.certain == 1
    (row,) = result.sections
    assert row.skipped == "no narration (no take in the take index)"
    assert row.rows[0].verdict is Verdict.UNRESOLVED and row.rows[0].cue == "1.1a"


def test_a_clip_section_with_no_take_is_the_plain_skip_it_should_be(tmp_path):
    """Section 0 plays a clip, which the voice never reads, so its own cue is not an unresolved phrase."""
    cues = {"0": {"cues": [{"cue": "0.1a", "on": "$start"}]}, "1": {"cues": [{"cue": "1.1a", "on": "there"}]}}
    project = _project(tmp_path, "<b data-cue='1.1a'></b>", cues)
    result = align(project, allow_unknown_cues=True)
    (clip,) = [s for s in result.sections if s.key == "00"]
    assert clip.skipped == "no narration (a clip section, which the voice never reads)"
    assert result.unresolved == 0


def test_the_page_a_row_is_about_sits_on_the_row_and_not_inside_its_sentence(tmp_path):
    """`where` carries the page, so a message never has to read another function's sentence back."""
    cues = {"1": {"cues": [{"cue": "4.1answer", "on": "there"}]}}
    project = _project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    result = align(project, allow_unknown_cues=True)
    (row,) = [r for r in result.sections[0].rows if r.verdict is Verdict.UNKNOWN_CUE]
    assert row.where == "deck/index.html"
    assert "deck/index.html" in unknown_hint(result)
    assert "deck/index.html" not in unknown_message(result), "the sentence says what is wrong, not where to look"
