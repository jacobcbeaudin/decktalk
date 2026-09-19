"""Every section cut to its span: the recording, the clip, the slate and the black stand-in."""

from __future__ import annotations

import importlib

import pytest

from decktalk.artifacts import Timeline, TimelineSection
from decktalk.errors import MissingInputError
from decktalk.media.encode import Encoder
from decktalk.model import Project
from decktalk.pipeline import Substitute


def test_timeline_targets_are_frame_exact():
    """A section is cut to a whole number of frames, so the film never drifts off the narration."""
    from decktalk.stages.assemble.cut import timeline_targets

    tl = Timeline(
        narration="n",
        total_seconds=2.5,
        sections={"01": TimelineSection("a", 0, 1.02, 1.02, None), "02": TimelineSection("b", 1.02, 2.5, 1.48, None)},
    )
    t = timeline_targets(tl, 30)
    assert abs(t["01"] - 1.0333) < 1e-3 and abs(t["02"] - 1.4667) < 1e-3


def test_strict_fails_on_a_missing_clip_unless_the_section_is_optional(tmp_path, monkeypatch, caplog, write_project):

    asm = importlib.import_module("decktalk.stages.assemble.cut")
    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\n"
        "[[section]]\nnumber = 2\nclip = 'media/real.mp4'\n"
        "[[section]]\nnumber = 3\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    real, slot = p.clip_sections
    assert (real.optional, slot.optional) == (False, True)
    ran = []
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: ran.append(args))
    monkeypatch.setattr(asm, "section_slate", lambda project, sec: None)
    enc = Encoder(p.settings.video)
    out = tmp_path / "out.mp4"

    with pytest.raises(MissingInputError) as err:
        asm.render_clip(p, enc, real, out, (False, False), 0.0, strict=True)
    assert str(err.value) == (
        f"section 2: clip missing: {p.root / 'media' / 'real.mp4'}. Put your clip at that path, "
        "or set optional = true on the section to play its slate under --strict."
    )
    assert ran == []

    with caplog.at_level("WARNING", logger="decktalk"):
        assert asm.render_clip(p, enc, slot, out, (False, False), 0.0, strict=True) == (
            Substitute.SLATE.value,
            "media/slot.mp4",
            Substitute.SLATE,
            None,
        )
        assert asm.render_clip(p, enc, real, out, (False, False), 0.0, strict=False) == (
            Substitute.SLATE.value,
            "media/real.mp4",
            Substitute.SLATE,
            None,
        )
    assert len(ran) == 2
    assert [r.getMessage() for r in caplog.records] == [
        "section 03: media/slot.mp4 missing; slate for 4s (drop your clip at that path; the section is optional, "
        "so --strict allows the slate)",
        "section 02: media/real.mp4 missing; slate for 5s (drop your clip at that path)",
    ]
