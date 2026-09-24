"""`cues.json`: the key that names a cue, and the keys a row may not use instead."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.inputs.cues import load_cues


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
