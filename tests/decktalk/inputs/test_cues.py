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
from decktalk.inputs.script import parse_script
from decktalk.results import Word
from decktalk.speech import PUNCT
from decktalk.stages.narrate.takes import estimated_words


def write_cues(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "cues.json"
    path.write_text(json.dumps({"sections": {"1": {"cues": rows}}}), encoding="utf-8")
    return path


def test_a_cue_row_names_its_id_under_one_key_and_no_other(tmp_path: Path) -> None:
    path = write_cues(tmp_path, [{"id": "1.1:a", "phrase": "$start"}, {"id": "1.1:b", "phrase": "hello"}])
    (section,) = load_cues(path, tmp_path, {1})
    assert [c.id for c in section.cues] == ["1.1:a", "1.1:b"]
    assert [c.phrase for c in section.cues] == ["$start", "hello"]
    # A cue row that names the id under any other key is refused, naming the key and the row.
    write_cues(tmp_path, [{"cue": "1.1:a", "phrase": "$start"}])
    with pytest.raises(InputError, match="'cue' is not a key of a cue"):
        load_cues(path, tmp_path, {1})
    # A key one letter away from a real one moves a cue in silence unless it is refused too.
    write_cues(tmp_path, [{"id": "1.1:a", "phrase": "hello", "occurence": 2}])
    with pytest.raises(InputError) as info:
        load_cues(path, tmp_path, {1})
    assert "'occurence' is not a key of a cue" in str(info.value)
    location = info.value.location
    assert location is not None and location.file is not None and location.file.name == "cues.json"
    assert info.value.hint is not None and "occurrence" in info.value.hint


def test_a_row_a_fix_scaffolded_loads_with_its_phrase_still_to_be_written(tmp_path: Path) -> None:
    """`check --fix` writes the rows a page declares and leaves each `phrase` empty, because the phrase a
    cue lands on is the author's own line. Refusing the file here made the fix write a project that
    the next command could not read at all."""
    path = write_cues(tmp_path, [{"id": "1.1:open", "phrase": ""}])
    (section,) = load_cues(path, tmp_path, {1})
    assert section.cues[0].id == "1.1:open"
    assert section.cues[0].phrase == ""


@pytest.mark.parametrize("occurrence", [0, -1])
def test_an_occurrence_below_the_first_is_refused_where_it_is_written(tmp_path: Path, occurrence: int) -> None:
    """Occurrences count from one, so a row asking for the zeroth names no word and is the author's typo."""
    path = write_cues(tmp_path, [{"id": "1.1:open", "phrase": "the words", "occurrence": occurrence}])
    with pytest.raises(InputError, match=rf"'occurrence' is {occurrence}, and occurrences count from 1"):
        load_cues(path, tmp_path, {1})


def test_a_row_with_no_id_is_still_refused(tmp_path: Path) -> None:
    path = write_cues(tmp_path, [{"id": "", "phrase": "the words"}])
    with pytest.raises(InputError, match="'id' must not be empty"):
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


@pytest.mark.parametrize("occurrence", [0, -1])
def test_an_occurrence_below_the_first_finds_nothing_rather_than_counting_from_the_end(occurrence: int) -> None:
    """Occurrences count from one, so zero names no occurrence rather than the last one."""
    assert SAID.find("hello", occurrence=occurrence) is None


def test_the_words_are_normalised_once_when_they_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long section with many cues would otherwise normalise every word once per cue."""
    calls: list[str] = []
    real = cues_module.pieces
    monkeypatch.setattr(
        cues_module, "pieces", lambda token, case_sensitive=False: calls.append(token) or real(token, case_sensitive)
    )
    said = Spoken.of((Word(word="a", start=0.0, end=0.1), Word(word="b", start=0.2, end=0.3)))
    before = len(calls)
    for _ in range(10):
        said.matches("a b")
    assert len(calls) - before == 20


def test_each_row_knows_the_line_its_phrase_is_written_on(tmp_path: Path) -> None:
    text = (
        '{"sections": {\n'
        '  "1": {"cues": [{"id": "1.1:a", "phrase": "Hello"},\n'
        '                 {"id": "1.1:b",\n'
        '                  "phrase": "say \\"there\\""}]},\n'
        '  "2": {"cues": [{"id": "2.1:a", "phrase": "again", "_comment": "\\"phrase\\": \\"decoy\\""}]}\n'
        "}}\n"
    )
    (tmp_path / "cues.json").write_text(text, encoding="utf-8")
    loaded = load_cues(tmp_path / "cues.json", tmp_path, {1, 2})
    assert [(cue.phrase, cue.line) for block in loaded for cue in block.cues] == [
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


@pytest.mark.parametrize(("spoken", "phrase"), [("Straße", "STRASSE"), ("Straße", "straße"), ("ΟΔΟΣ", "οδος")])
def test_a_word_whose_case_folds_to_other_letters_matches_its_phrase(spoken: str, phrase: str) -> None:
    # Before, the transcript was lowered while the phrase was folded, so "Straße" never matched itself.
    said = Spoken.of((Word(word=spoken, start=0.0, end=0.4),))
    assert said.find(phrase) == 0


# ---- words a voice speaks as a symbol, and words joined by hyphens ------------------------------

SCRIPT = "Our R & D team ships state-of-the-art tools / fast, with Q & A on input / output."
"""A line of ordinary script text whose `&` and `/` a voice gives back as words of their own."""

SPOKEN_SCRIPT = Spoken.of(
    tuple(Word(word=token.strip(PUNCT), start=float(i), end=i + 0.5) for i, token in enumerate(SCRIPT.split()))
)
"""The line as a voice's transcript reads it, split on spaces with the edge punctuation stripped."""


@pytest.mark.parametrize(
    ("phrase", "index"),
    [
        ("R & D", 1),
        ("R & D team", 1),
        ("Q & A", 11),
        ("tools / fast", 7),
        ("input / output", 15),
        ("& D", 2),
        ("/ output", 16),
        ("R D", 1),
    ],
)
def test_a_phrase_with_a_symbol_the_voice_speaks_as_its_own_word_resolves(phrase: str, index: int) -> None:
    """A word with no letter in it is skipped on both sides, and a phrase that opens on one lands on it."""
    assert SPOKEN_SCRIPT.find(phrase) == index


@pytest.mark.parametrize(
    ("phrase", "index"),
    [
        ("state-of-the-art", 6),
        ("state of the art", 6),
        ("stateoftheart", 6),
        ("ships state of the art tools", 5),
        ("the art tools", 6),
        ("of the", 6),
    ],
)
def test_a_hyphenated_word_matches_whole_and_by_its_parts(phrase: str, index: int) -> None:
    """A phrase that starts inside a hyphenated word lands on the start of the word that holds it."""
    assert SPOKEN_SCRIPT.find(phrase) == index


def test_a_phrase_written_with_a_hyphen_matches_the_parts_a_voice_spoke_apart() -> None:
    said = Spoken.of(tuple(Word(word=w, start=float(i), end=i + 0.5) for i, w in enumerate("a zig zag line".split())))
    assert said.find("zig-zag") == 1
    assert said.find("zig zag line") == 1


def test_a_hyphenated_word_counts_once_where_its_whole_and_its_parts_both_match() -> None:
    assert SPOKEN_SCRIPT.matches("state-of-the-art") == [6]


@pytest.mark.parametrize(("phrase", "index"), [("R & D", 1), ("state of the art", 6), ("tools / fast", 7)])
def test_the_placeholder_words_of_a_run_without_voice_resolve_the_same_phrases(phrase: str, index: int) -> None:
    (section,) = parse_script(f"## 1. Open\n\n{SCRIPT}\n")
    assert Spoken.of(estimated_words(section, 8.0)).find(phrase) == index


SYMBOL = st.sampled_from(["&", "/", "+", "-", "—", "..."])
"""A word a voice may speak that carries no letter or digit, so the matcher reads nothing from it."""

PIECE = st.text(string.ascii_letters + string.digits, min_size=1, max_size=4)

ANY_WORD = st.one_of(
    WORD, SYMBOL, st.lists(PIECE, min_size=2, max_size=3).map("-".join), st.lists(PIECE, min_size=2).map("/".join)
)
"""A plain word, a word with no letter in it, or a word whose parts a hyphen or a slash joins."""


@given(st.lists(ANY_WORD, min_size=1, max_size=10), st.data())
def test_any_run_of_spoken_words_resolves_on_or_before_its_own_first_word(
    words: list[str], data: st.DataObject
) -> None:
    start = data.draw(st.integers(min_value=0, max_value=len(words) - 1))
    end = data.draw(st.integers(min_value=start + 1, max_value=len(words)))
    said = Spoken.of(tuple(Word(word=word, start=float(i), end=i + 0.5) for i, word in enumerate(words)))
    phrase = " ".join(words[start:end])
    if not any(norm(word) for word in words[start:end]):
        assert said.matches(phrase) == []
        return
    found = said.matches(phrase)
    assert start in found
    assert found[0] <= start
    assert said.find(phrase) == found[0]
