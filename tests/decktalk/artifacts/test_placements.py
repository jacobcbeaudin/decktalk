"""The placements: where every section sits in the finished film."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.placements import Placement, Placements
from decktalk.results import SectionKind, Substitute


def placed(section: int, start: float, end: float, *, substitute: Substitute | None = None) -> Placement:
    return Placement(
        section=section,
        key=f"{section:02d}",
        kind=SectionKind.PAGE,
        start=start,
        end=end,
        source=Path(f"build/sections/{section:02d}.mp4"),
        chapter=f"Section {section}",
        substitute=substitute,
    )


FILM = Placements(fps=25, sections=(placed(1, 0.0, 3.2), placed(2, 3.2, 8.0, substitute=Substitute.SLATE)))


def test_the_film_ends_where_its_last_section_does() -> None:
    assert FILM.total_seconds == 8.0
    assert Placements(fps=25).total_seconds == 0.0


def test_the_placements_round_trip_through_their_own_file(tmp_path: Path) -> None:
    path = FILM.write(tmp_path / "placements.json")
    assert Placements.read(path) == FILM


def test_a_path_is_written_with_forward_slashes(tmp_path: Path) -> None:
    """Three platforms read the same bytes, so the placements never carry a backslash."""
    path = FILM.write(tmp_path / "placements.json")
    assert "build/sections/01.mp4" in path.read_text(encoding="utf-8")
