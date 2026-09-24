"""`cues.json`: the key that names a cue, and the keys a row may not use instead."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.errors import ConfigError
from decktalk.model.cues import load_cues


def write_cues(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "cues.json"
    path.write_text(json.dumps({"sections": {"1": {"cues": rows}}}), encoding="utf-8")
    return path


def test_cues_load_with_cue_keys_and_reject_any_other_id_key(tmp_path):
    path = write_cues(tmp_path, [{"cue": "1.1a", "on": "$start"}, {"cue": "1.1b", "on": "hello"}])
    (section,) = load_cues(path, {1})
    assert [c.cue for c in section.cues] == ["1.1a", "1.1b"]
    assert [c.on for c in section.cues] == ["$start", "hello"]
    # A cue row that names the id under any other key is refused, naming the key and the row.
    write_cues(tmp_path, [{"id": "1.1a", "on": "$start"}])
    with pytest.raises(ConfigError, match="'id' is not a key of a cue"):
        load_cues(path, {1})
    # A key one letter away from a real one moves a cue in silence unless it is refused too.
    write_cues(tmp_path, [{"cue": "1.1a", "on": "hello", "occurence": 2}])
    with pytest.raises(ConfigError) as info:
        load_cues(path, {1})
    assert "'occurence' is not a key of a cue" in str(info.value)
    assert info.value.path is not None and info.value.path.name == "cues.json"
    assert info.value.hint is not None and "occurrence" in info.value.hint
