"""`build/narration/takes.json`: the take index and the narration clock it answers with."""

from __future__ import annotations

from decktalk.artifacts.takes import Take, Takes


def test_takes_roundtrip_sorts_and_totals(tmp_path):
    m = Takes(script="s.md", model="m", output_format="mp3")
    m.sections["02"] = Take(2, "B", "02-b.mp3", "02-b.words.json", "h", 3, 1.0, 2.5)
    m.sections["01"] = Take(1, "A", "01-a.mp3", "01-a.words.json", "h", 3, 1.0, 1.5)
    path = tmp_path / "takes.json"
    m.save(path)
    back = Takes.load(path)
    assert back is not None and list(back.sections) == ["01", "02"] and back.total_seconds == 4.0
    assert not list(tmp_path.glob(".*.tmp"))  # atomic write left nothing behind


def test_a_reloaded_take_index_answers_the_clock_it_wrote(tmp_path):
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.sections["01"] = Take(1, "A", "h.mp3", "h.words.json", "h", 1, 3.0, 2.5, lead_seconds=0.5)
    index.save(tmp_path / "t.json")
    back = Takes.load(tmp_path / "t.json")
    # The clock is arithmetic over the rows, so a reloaded index answers exactly what it wrote.
    assert back is not None and back.span("01") == 3.0 and back.total_seconds == 3.0
    assert (back.start("01"), back.end("01")) == (0.0, 3.0) and back.sections["01"].chapter == "A"
