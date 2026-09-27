"""`cues.json`: the key that names a cue, and the keys a row may not use instead."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.inputs import cues as cues_module
from decktalk.inputs.cues import Spoken, load_cues
from decktalk.results import Word


def write_cues(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "cues.json"
    path.write_text(json.dumps({"sections": {"1": {"cues": rows}}}), encoding="utf-8")
    return path


def test_a_cue_row_names_its_id_under_one_key_and_no_other(tmp_path: Path) -> None:
    path = write_cues(tmp_path, [{"cue": "1.1:a", "on": "$start"}, {"cue": "1.1:b", "on": "hello"}])
    (section,) = load_cues(path, tmp_path, {1})
    assert [c.cue for c in section.cues] == ["1.1:a", "1.1:b"]
    assert [c.on for c in section.cues] == ["$start", "hello"]
    # A cue row that names the id under any other key is refused, naming the key and the row.
    write_cues(tmp_path, [{"id": "1.1:a", "on": "$start"}])
    with pytest.raises(InputError, match="'id' is not a key of a cue"):
        load_cues(path, tmp_path, {1})
    # A key one letter away from a real one moves a cue in silence unless it is refused too.
    write_cues(tmp_path, [{"cue": "1.1:a", "on": "hello", "occurence": 2}])
    with pytest.raises(InputError) as info:
        load_cues(path, tmp_path, {1})
    assert "'occurence' is not a key of a cue" in str(info.value)
    assert info.value.location is not None and info.value.location.file.name == "cues.json"
    assert info.value.hint is not None and "occurrence" in info.value.hint


def test_a_row_a_fix_scaffolded_loads_with_its_phrase_still_to_be_written(tmp_path: Path) -> None:
    """`check --fix` writes the rows a page declares and leaves each `on` empty, because the phrase a
    cue lands on is the author's own line. Refusing the file here made the fix write a project that
    the next command could not read at all."""
    path = write_cues(tmp_path, [{"cue": "1.1:open", "on": ""}])
    (section,) = load_cues(path, tmp_path, {1})
    assert section.cues[0].cue == "1.1:open"
    assert section.cues[0].on == ""


def test_a_row_with_no_id_is_still_refused(tmp_path: Path) -> None:
    path = write_cues(tmp_path, [{"cue": "", "on": "the words"}])
    with pytest.raises(InputError, match="'cue' must not be empty"):
        load_cues(path, tmp_path, {1})


# ---- matching a phrase against a section's words -----------------------------------------------

SAID = Spoken.of(
    (
        Word(word="Hello,", start=0.0, end=0.4),
        Word(word="World", start=0.5, end=0.9),
        Word(word="hello", start=1.0, end=1.3),
        Word(word="world.", start=1.4, end=1.8),
    )
)


def test_a_phrase_matches_without_case_or_punctuation_by_default() -> None:
    assert SAID.matches("hello world") == [0, 2]


def test_a_case_sensitive_phrase_matches_its_own_case_alone() -> None:
    assert SAID.matches("hello world", case_sensitive=True) == [2]


def test_the_nth_occurrence_is_found_and_one_past_the_last_is_none() -> None:
    assert SAID.find("hello", occurrence=2) == 2
    assert SAID.find("hello", occurrence=3) is None


def test_a_phrase_of_punctuation_alone_matches_nothing() -> None:
    assert SAID.matches("...") == []


def test_the_words_are_normalised_once_when_they_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long section with many cues would otherwise normalise every word once per cue."""
    calls: list[str] = []
    real = cues_module.norm
    monkeypatch.setattr(
        cues_module, "norm", lambda token, case_sensitive=False: calls.append(token) or real(token, case_sensitive)
    )
    said = Spoken.of((Word(word="a", start=0.0, end=0.1), Word(word="b", start=0.2, end=0.3)))
    before = len(calls)
    for _ in range(10):
        said.matches("a b")
    assert len(calls) - before == 20


def test_each_row_knows_the_line_its_phrase_is_written_on(tmp_path: Path) -> None:
    text = (
        '{"sections": {\n'
        '  "1": {"cues": [{"cue": "1.1:a", "on": "Hello"},\n'
        '                 {"cue": "1.1:b",\n'
        '                  "on": "say \\"there\\""}]},\n'
        '  "2": {"cues": [{"cue": "2.1:a", "on": "again", "_comment": "\\"on\\": \\"decoy\\""}]}\n'
        "}}\n"
    )
    (tmp_path / "cues.json").write_text(text, encoding="utf-8")
    loaded = load_cues(tmp_path / "cues.json", tmp_path, {1, 2})
    assert [(cue.on, cue.line) for block in loaded for cue in block.cues] == [
        ("Hello", 2),
        ('say "there"', 4),
        ("again", 5),
    ]
