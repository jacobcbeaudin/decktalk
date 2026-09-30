"""`cues.json`: the key that names a cue, and the keys a row may not use instead."""

from __future__ import annotations

import json
import string
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from decktalk.errors import InputError
from decktalk.inputs import cues as cues_module
from decktalk.inputs.cues import Spoken, load_cues, norm
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


WORD = st.text(string.ascii_letters + string.digits + "'", min_size=1, max_size=5).filter(
    lambda word: word.strip("'") != ""
)
"""One spoken word as the matcher keeps it: letters, digits and the apostrophe of a contraction."""

MARKS = st.text(',.;:!?"()-\u2014 ', max_size=2)
"""What a provider writes around a word and the matcher ignores, space included."""


@given(st.lists(WORD, min_size=1, max_size=8), st.data())
def test_a_phrase_matches_the_words_it_names_whatever_their_case_and_punctuation(
    words: list[str], data: st.DataObject
) -> None:
    """Every occurrence is found by default, and a case-sensitive match finds the ones in its own case."""
    start = data.draw(st.integers(min_value=0, max_value=len(words) - 1))
    end = data.draw(st.integers(min_value=start + 1, max_value=len(words)))
    cased = [data.draw(st.sampled_from((word, word.upper(), word.lower(), word.title()))) for word in words]
    said = Spoken.of(
        tuple(
            Word(word=data.draw(MARKS) + word + data.draw(MARKS), start=float(i), end=i + 0.5)
            for i, word in enumerate(cased)
        )
    )
    phrase, width = words[start:end], end - start

    def found(spoken: list[str], asked: list[str]) -> list[int]:
        return [i for i in range(len(spoken) - width + 1) if spoken[i : i + width] == asked]

    assert said.matches(" ".join(phrase)) == found([w.lower() for w in cased], [w.lower() for w in phrase])
    assert said.matches(" ".join(phrase), case_sensitive=True) == found(cased, phrase)
    assert start in said.matches(" ".join(cased[start:end]), case_sensitive=True)


@given(MARKS)
def test_a_phrase_of_punctuation_alone_matches_nothing(phrase: str) -> None:
    assert SAID.matches(phrase) == []


def test_the_nth_occurrence_is_found_and_one_past_the_last_is_none() -> None:
    assert SAID.find("hello", occurrence=2) == 2
    assert SAID.find("hello", occurrence=3) is None


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


@pytest.mark.parametrize(
    ("spoken", "cue", "wanted"),
    [
        ("café", "café", "café"),
        ("café", "café", "café"),
        ("Naïve,", "naïve", "naïve"),
        ("don’t", "don't", "don't"),
    ],
)
def test_an_accented_or_typographic_word_is_matched_whole(spoken: str, cue: str, wanted: str) -> None:
    # Before, every letter outside ASCII was cut out, so "café" matched as "caf".
    assert norm(spoken) == norm(cue) == wanted


def test_a_word_with_its_accent_cut_out_does_not_match_the_accented_word() -> None:
    assert norm("caf") != norm("café")
