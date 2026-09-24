"""`build/cue-times.json`: every cue resolved against the words, and the queries the page asks of it."""

from __future__ import annotations

import json

from decktalk.artifacts.cue_times import CueTime, CueTimes


def test_cue_times_roundtrip_and_answer_by_section_and_cue(tmp_path):
    rows = [CueTime("a", "hi", 1.5, 1.2), CueTime("panel:bought", "$end", 2.0)]
    b = CueTimes({"01": rows}, estimated=True)
    b.save(tmp_path / "b.json")
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {
        "estimated": True,
        "sections": {"01": [
            {"cue": "a", "on": "hi", "at": 1.5, "word_at": 1.2},
            {"cue": "panel:bought", "on": "$end", "at": 2.0, "word_at": None},
        ]},
    }  # fmt: skip
    back = CueTimes.load(tmp_path / "b.json")
    assert back.estimated and back.get("01", "panel:bought") == 2.0 and back.word_at("01", "a") == 1.2
    assert back.query("01") == "a@1.5,panel:bought@2.0" and back.times("01") == {"a": 1.5, "panel:bought": 2.0}
    assert back.get("01", "nope") is None and back.query("99") is None
