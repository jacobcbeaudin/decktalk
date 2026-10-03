"""What the document derives from its own tables: the fade of every cut and the length of a dip."""

from __future__ import annotations

from decktalk.inputs import Inputs
from decktalk.inputs.document import ClipSection, Document, Mix, PageSection, Transition, frame_dip
from support.projects import MINIMAL_TOML, write_project


def test_fade_flags_follow_dips_and_page_fade_in(tmp_path):
    p = Inputs.load(
        write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = [[0, 1]]\npage_fades_in = true\n"), environ={}
    )
    flags = p.document.fade_flags
    assert flags["00"] == (False, True)  # clip fades out into the dip
    assert flags["01"] == (False, False)  # page fades itself in, with no dip after
    assert flags["02"] == (False, False)
    p2 = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})  # no dips key: every cut dips
    assert p2.document.fade_flags["01"] == (False, True)
    p3 = Inputs.load(write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = []\n"), environ={})
    assert set(p3.document.fade_flags.values()) == {(False, False)}  # an empty list is straight cuts


def test_frame_dip_quantizes_to_whole_frames():
    assert frame_dip(0.15, 25) == 0.16  # 3.75 frames rounds up to 4
    assert frame_dip(0.15, 30) == 0.1333  # 4.5 frames rounds to the even 4
    assert frame_dip(0.001, 25) == 0.04  # never shorter than one frame
    assert frame_dip(0.0, 25) == 0.0


def test_a_project_that_writes_no_value_gets_the_default_its_field_declares() -> None:
    """The parse call and the field spell every default once, so the two cannot disagree."""
    doc = Document.from_toml(
        {
            "section": [{"number": 1, "clip": "a.mp4"}, {"number": 2, "page": "deck/a.html"}],
            "mix": {},
            "transition": {},
        },
        default_name="t",
    )
    clip, page = doc.sections
    assert isinstance(clip, ClipSection) and clip.slate_seconds == ClipSection(number=1, clip="a.mp4").slate_seconds
    assert (
        isinstance(page, PageSection) and page.record_margin_seconds == PageSection(1, "a", "1").record_margin_seconds
    )
    assert doc.mix == Mix() and doc.transition == Transition()
