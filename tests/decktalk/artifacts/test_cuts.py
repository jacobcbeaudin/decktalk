"""The cut list: where every section sits in the finished film."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.cuts import Cut, Cuts
from decktalk.results import SectionKind, Substitute


def cut(section: int, start: float, end: float, *, substitute: Substitute | None = None) -> Cut:
    return Cut(
        section=section,
        key=f"{section:02d}",
        kind=SectionKind.PAGE,
        start=start,
        end=end,
        source=Path(f"build/sections/{section:02d}.mp4"),
        chapter=f"Section {section}",
        substitute=substitute,
    )


FILM = Cuts(fps=25, sections=(cut(1, 0.0, 3.2), cut(2, 3.2, 8.0, substitute=Substitute.SLATE)))


def test_a_cut_runs_from_its_start_to_its_end() -> None:
    assert FILM.sections[0].seconds == 3.2


def test_the_film_ends_where_its_last_section_does() -> None:
    assert FILM.total_seconds == 8.0
    assert Cuts(fps=25).total_seconds == 0.0


def test_the_section_playing_at_a_second_is_found_and_past_the_end_there_is_none() -> None:
    assert FILM.at(0.0).section == 1
    assert FILM.at(3.2).section == 2
    assert FILM.at(8.0) is None


def test_a_section_is_found_by_its_number() -> None:
    assert FILM.of(2).chapter == "Section 2"
    assert FILM.of(9) is None


def test_the_substituted_sections_are_the_ones_a_strict_run_refuses() -> None:
    assert [row.section for row in FILM.substituted] == [2]


def test_the_cut_list_round_trips_through_its_own_file(tmp_path: Path) -> None:
    path = FILM.write(tmp_path / "cuts.json")
    assert Cuts.read(path) == FILM


def test_a_path_is_written_with_forward_slashes(tmp_path: Path) -> None:
    """Three platforms read the same bytes, so a cut list never carries a backslash."""
    path = FILM.write(tmp_path / "cuts.json")
    assert "build/sections/01.mp4" in path.read_text(encoding="utf-8")
