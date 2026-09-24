"""Which frozen states of a page each cue is measured between."""

from __future__ import annotations

from decktalk.verdicts import SkipReason


def test_plan_frames_follows_cue_mode_and_freezes_just_before_each_reveal():
    from decktalk.stages.preflight.freeze import (
        ORDER_NOTE,
        Freeze,
        first_state,
        last_state,
        mounts,
        owner_slide,
        plan_frames,
    )

    slides = {"1.1": ["1.1in", "1.1a", "1.1b"], "1.2": ["1.2a", "1.2b"]}
    cue_times = {"1.1in": 0.0, "1.1a": 1.0, "1.1b": 2.0, "1.2a": 3.0, "1.2b": 4.0, "1.1zz": 4.5, "9x": 5.0}
    assert [owner_slide(c, slides) for c in ("1.2b", "1.2", "1.1zz", "9x")] == ["1.2", "1.2", "1.1", None]
    assert mounts(slides, cue_times) == [("1.1", 0.0), ("1.2", 3.0)]
    plan = [(p.cue, p.before, p.after, p.reason, p.note) for p in plan_frames(slides, cue_times, 25)]
    assert plan == [
        ("1.1in", None, None, SkipReason.AT_SECTION_START, ""),
        ("1.1a", Freeze("1.1", cue="1.1in"), Freeze("1.1", cue="1.1a"), None, ""),
        ("1.1b", Freeze("1.1", cue="1.1a"), Freeze("1.1", cue="1.1b"), None, ""),
        # 1.2a mounts slide 1.2, so the frame before it is slide 1.1 with every cue it fired.
        ("1.2a", Freeze("1.1", cue="1.1b"), Freeze("1.2", cue="1.2a"), None, ""),
        ("1.2b", Freeze("1.2", cue="1.2a"), Freeze("1.2", cue="1.2b"), None, ""),
        # The prefix gives 1.1zz to slide 1.1, which declares no element for it, so there is no
        # state to freeze between and the row is skipped for the one reason a skipped cue carries.
        ("1.1zz", None, None, SkipReason.NO_SLIDE, ""),
        ("9x", None, None, SkipReason.NO_SLIDE, ""),
    ]
    assert last_state(slides, cue_times) == Freeze("1.2", cue="1.2b")
    assert first_state(slides, cue_times, 25) == Freeze("1.1", cue="1.1in")

    # The first slide mounts at 0 even when its first cue comes later, so that cue freezes just before itself.
    late = {"2.1": ["2.1a", "2.1b"]}
    late_cue_times = {"2.1a": 1.5, "2.1b": 3.0}
    assert plan_frames(late, late_cue_times, 25)[0].before == Freeze("2.1", before="2.1a")
    assert first_state(late, late_cue_times, 25) == Freeze("2.1", before="2.1a")
    assert Freeze("2.1", before="2.1a").query() == {"slide": "2.1", "before": "2.1a"}
    assert Freeze("2.1", cue="2.1aloud").label == "slide-2.1-after-2.1aloud" and Freeze("2.1").query() == {
        "slide": "2.1"
    }

    # A freeze fires in preview order, so cue times in another order get a note.
    swapped = plan_frames({"3.1": ["3.1b", "3.1a"]}, {"3.1a": 1.0, "3.1b": 2.0}, 25)
    assert [(p.before, p.note) for p in swapped] == [
        (Freeze("3.1", cue="3.1b"), ORDER_NOTE),
        (Freeze("3.1", before="3.1b"), ORDER_NOTE),
    ]


def test_slide_cues_reads_a_catalog_scene_and_names_the_reason_when_it_cannot():
    from decktalk.stages.preflight.freeze import slide_cues

    catalog = [
        {"scene": "1", "name": "One", "slides": ["1.1", "1.2"], "cues": {"1.1": ["1.1a"], "1.2": []}},
        {"scene": "2", "name": "Two", "slides": ["2.1"]},
    ]
    assert slide_cues(catalog, "1") == ({"1.1": ["1.1a"], "1.2": []}, "")
    assert slide_cues(catalog, "3") == (None, "registers no scene 3")
    assert slide_cues([], "1") == (None, "is missing or has no runtime catalog")
    assert slide_cues(None, "1") == (None, "is missing or has no runtime catalog")
    # A hand-written page may register a scene without the runtime's cue map, and preflight says so
    # rather than raising, because docs/guides/page-by-hand.mdx invites exactly such a page.
    assert slide_cues(catalog, "2") == (None, "registers scene 2 without a slides list and a cues map")
