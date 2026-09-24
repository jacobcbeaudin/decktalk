"""The cut list: where every section sits in the finished film, and what stands in for it."""

from __future__ import annotations

import json

import pytest

from decktalk.artifacts import Cut, Cuts
from decktalk.pipeline import SectionKind, Substitute


def a_cuts() -> Cuts:
    return Cuts(
        fps=25,
        total_seconds=9.5,
        sections=[
            Cut(1, SectionKind.PAGE, 0.0, 4.0, "build/recordings/01.webm", "Open", dip_out=True),
            Cut(2, SectionKind.CLIP, 4.0, 6.5, "media/broll.mp4", "B-roll", dip_in=True),
            Cut(3, SectionKind.PAGE, 6.5, 9.5, "build/recordings/03.webm", "Close", substitute=Substitute.BLACK),
        ],
    )


def test_a_row_knows_its_key_and_how_long_it_runs():
    first, _clip, last = a_cuts().sections
    assert (first.key, first.duration) == ("01", 4.0)
    assert (last.key, last.duration) == ("03", 3.0)


def test_the_substituted_sections_are_the_ones_that_played_a_stand_in():
    cuts = a_cuts()
    assert [c.section for c in cuts.substituted] == [3]
    cuts.sections[1] = Cut(2, SectionKind.CLIP, 4.0, 6.5, "media/broll.mp4", "B-roll", substitute=Substitute.SLATE)
    assert [c.substitute for c in cuts.substituted] == [Substitute.SLATE, Substitute.BLACK]


def test_a_second_of_the_film_names_the_section_playing_there():
    cuts = a_cuts()
    assert cuts.at(0.0).section == 1
    assert cuts.at(4.0).section == 2  # the boundary belongs to the section that starts there
    assert cuts.at(9.4).section == 3
    assert cuts.at(9.5) is None


def test_the_file_round_trips_and_is_plain_json(tmp_path):
    path = tmp_path / "cuts.json"
    a_cuts().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["fps"] == 25 and data["total_seconds"] == 9.5
    assert data["sections"][0] == {
        "section": 1,
        "kind": SectionKind.PAGE.value,
        "start": 0.0,
        "end": 4.0,
        "source": "build/recordings/01.webm",
        "substitute": None,
        "chapter": "Open",
        "dip_in": False,
        "dip_out": True,
    }
    again = Cuts.load(path)
    assert again is not None and again.to_dict() == a_cuts().to_dict()
    assert Cuts.load(tmp_path / "nothing.json") is None


def test_a_row_reads_its_kind_and_its_substitute_as_members_and_refuses_any_other_word():
    """A misspelt word in the file fails where it is read, rather than making every comparison false."""
    row = a_cuts().sections[2].to_dict()
    assert Cut.from_dict(row).substitute is Substitute.BLACK and Cut.from_dict(row).kind is SectionKind.PAGE
    with pytest.raises(ValueError):
        Cut.from_dict({**row, "kind": "movie"})
    with pytest.raises(ValueError):
        Cut.from_dict({**row, "substitute": "grey"})
