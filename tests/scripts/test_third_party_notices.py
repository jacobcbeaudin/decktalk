"""THIRD_PARTY_NOTICES.md names every font face the repository's graphics embed, and no other.

The graphics under `assets/` and `docs/` carry subsets of the faces `assets/fonts/LICENSE.txt` names.
The generator that embedded them lives with the site's sources, so the licence file beside the
graphics is the record of what they hold, and the notices have to agree with it.
"""

from __future__ import annotations

import re

from support.paths import REPO

NOTICES = REPO / "THIRD_PARTY_NOTICES.md"
GRAPHICS_LICENSE = REPO / "assets" / "fonts" / "LICENSE.txt"


def embedded_faces() -> set[str]:
    """Every face the graphics' licence file names, by the line that opens its copyright."""
    text = GRAPHICS_LICENSE.read_text(encoding="utf-8")
    return {match["face"] for match in re.finditer(r"^(?P<face>[A-Z][\w ]+), Copyright ", text, re.MULTILINE)}


def rows() -> dict[str, str]:
    """Every row of the notices' table, by the component it names, with what it says about it."""
    found = {}
    for line in NOTICES.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 3 and (name := re.match(r"\[(?P<name>[^\]]+)\]", cells[0])):
            found[name["name"]] = cells[1]
    return found


def test_the_graphics_licence_names_the_faces_it_covers() -> None:
    assert embedded_faces() == {"Instrument Sans", "IBM Plex Mono", "Instrument Serif", "Bricolage Grotesque"}


def test_every_face_the_graphics_embed_has_a_notice_that_says_so() -> None:
    table = rows()
    for face in embedded_faces():
        assert face in table, f"THIRD_PARTY_NOTICES.md has no row for {face}"
        assert "assets/" in table[face], f"the {face} row does not say the graphics embed it"


def test_no_other_face_is_said_to_be_in_the_graphics() -> None:
    faces = embedded_faces()
    for name, how in rows().items():
        if "assets/" in how:
            assert name in faces, f"the {name} row says the graphics embed it, and assets/fonts/LICENSE.txt does not"


EMBEDS = re.compile(r"font/woff2|<path\b")
"""What a graphic that carries a face holds: a base64 WOFF2 subset, or the letters traced into outline paths."""


def test_every_graphic_the_licence_names_carries_a_face() -> None:
    """A path the licence names is one whose graphics hold a subset, so the licence claims no file that has none."""
    opening = GRAPHICS_LICENSE.read_text(encoding="utf-8").split(" carry subsets", 1)[0]
    named = re.findall(r"(?:assets|docs)/[\w./-]*", opening)
    assert named, "the licence names no graphics"
    for place in named:
        target = REPO / place
        graphics = sorted(target.rglob("*.svg")) if target.is_dir() else [target]
        assert any(EMBEDS.search(graphic.read_text(encoding="utf-8")) for graphic in graphics), (
            f"assets/fonts/LICENSE.txt names {place}, which embeds no face"
        )


def test_no_asset_names_a_script_this_repository_does_not_hold() -> None:
    """The asset builder lives with the site's sources, so a generated asset says so rather than name a gone script."""
    for asset in sorted((REPO / "assets").rglob("*")):
        if asset.suffix not in {".css", ".svg", ".txt"}:
            continue
        for script in re.findall(r"scripts/[\w/]+\.py", asset.read_text(encoding="utf-8")):
            assert (REPO / script).is_file(), f"{asset.relative_to(REPO)} names {script}, which is not here"
