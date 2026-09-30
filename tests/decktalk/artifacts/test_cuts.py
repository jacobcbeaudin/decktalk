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


def test_the_film_ends_where_its_last_section_does() -> None:
    assert FILM.total_seconds == 8.0
    assert Cuts(fps=25).total_seconds == 0.0


def test_the_cut_list_round_trips_through_its_own_file(tmp_path: Path) -> None:
    path = FILM.write(tmp_path / "cuts.json")
    assert Cuts.read(path) == FILM


def test_a_path_is_written_with_forward_slashes(tmp_path: Path) -> None:
    """Three platforms read the same bytes, so a cut list never carries a backslash."""
    path = FILM.write(tmp_path / "cuts.json")
    assert "build/sections/01.mp4" in path.read_text(encoding="utf-8")
