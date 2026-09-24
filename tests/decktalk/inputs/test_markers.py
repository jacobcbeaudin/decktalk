"""`markers.json`: the rows the music answers to, and what a malformed file says."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.inputs.markers import load_markers


def test_the_markers_file_is_parsed_into_rows_and_a_bad_one_names_its_file(tmp_path, caplog) -> None:
    """The music answers to these rows, so a malformed file fails at load with the row named."""
    path = tmp_path / "markers.json"
    path.write_text(
        json.dumps(
            {
                "boost_db": 4,
                "boost_seconds": 1.5,
                "markers": [
                    {"name": "turn", "section": 3, "on": "$start", "mute_seconds": 0.4},
                    {"name": "land", "section": 4, "on": "seal", "offset": 0.2, "occurrence": 2, "zebra": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING", logger="decktalk"):
        markers = load_markers(path, tmp_path)
    assert (markers.boost_db, markers.boost_seconds) == (4.0, 1.5)
    assert [(m.name, m.section, m.key, m.on, m.offset, m.occurrence) for m in markers.markers] == [
        ("turn", 3, "03", "$start", 0.0, 1),
        ("land", 4, "04", "seal", 0.2, 2),
    ]
    assert "ignoring unknown key 'zebra'" in caplog.text

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(InputError, match="no top-level 'markers' array"):
        load_markers(path, tmp_path)

    path.write_text(json.dumps({"markers": [{"name": "turn"}]}), encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_markers(path, tmp_path)
    # The row names the file it is about rather than carrying an absolute path inside its sentence.
    assert "'section' is required and is not there." in str(info.value)
    assert str(path) not in str(info.value) and info.value.location.file == Path(path.name)

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_markers(path, tmp_path)
    # A machine-readable payload carries no path from outside the project, so the line goes in its own slot.
    assert str(info.value).startswith("markers.json is not valid JSON:")
    assert info.value.location.file == Path(path.name) and info.value.location.line == 1
