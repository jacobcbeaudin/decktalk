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
