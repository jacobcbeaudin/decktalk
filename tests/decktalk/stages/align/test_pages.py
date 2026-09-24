"""The two scans that read a page: the cue ids it mentions, and the elements nobody cued."""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.model import Project
from decktalk.model.cues import page_mentions
from decktalk.stages.align.pages import uncued_elements, unknown_cue_ids

TOML = "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"


def _project(tmp_path: Path, html: str, cues: dict) -> Project:
    """Section 0 a clip and section 1 a page, with the cues file the scans read against it."""
    root = tmp_path / "proj"
    (root / "deck").mkdir(parents=True)
    (root / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (root / "deck" / "index.html").write_text(html, encoding="utf-8")
    (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    return Project.load(root, environ={})


def test_page_mentions_finds_quoted_ids_and_data_cue():
    html = """<div data-cue="4.1answer"></div>
    <script>DeckTalk.scene({ preview: { "4.1x": 1 }, on: { '4.1y': () => {} }, tpl: `4.1z` });</script>"""
    for cue in ("4.1answer", "4.1x", "4.1y", "4.1z"):
        assert page_mentions(html, cue), cue
    assert not page_mentions(html, "4.1")  # A prefix of a quoted id is not a mention.
    assert not page_mentions(html, "4.1ans")
    assert not page_mentions("<p>\"4.1x'</p>", "4.1x")  # The quotes must match.


def test_a_cue_id_its_page_never_mentions_is_named_with_that_page(tmp_path):
    cues = {"1": {"cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "4.1answer", "on": "there"}]}}
    project = _project(tmp_path, '<b data-cue="1.1a"></b>', cues)
    assert unknown_cue_ids(project, project.cue_specs()) == [("01", "4.1answer", "deck/index.html")]


def test_an_element_no_cue_names_is_reported_against_the_section_its_prefix_owns(tmp_path):
    html = '<b data-cue="1.1a"></b><i data-cue="1.2forgotten"></i><i data-cue="${IDS[i]}"></i>'
    project = _project(tmp_path, html, {"1": {"cues": [{"cue": "1.1a", "on": "hello"}]}})
    # An id a script builds from a variable is left to preflight, which reads the page's own catalog.
    assert uncued_elements(project, project.cue_specs()) == [("01", "1.2forgotten", "deck/index.html")]


def test_an_element_in_a_scene_no_section_plays_waits_for_nothing(tmp_path):
    """The runtime mounts a scene's elements only when that scene plays, so an unplayed one is never uncued.

    An element in a played scene is reported against the section that plays the scene, whatever its id
    says, because `data-owns` lets a slide own an id with any prefix.
    """
    html = """<div data-scene="1">
      <template data-slide="1.1" data-owns="close"><b data-cue="1.1a"></b><i data-cue="close"></i></template>
    </div>
    <div data-scene="hero" data-name="The short cut's close">
      <template data-slide="4.2" data-owns="tag"><svg><path data-cue="4.2mark"/></svg><p data-cue="tag">x</p></template>
    </div>
    <script>DeckTalk.scene(7, { slides: [{ id: "7.1", render: () => `<i data-cue="1.9late"></i>` }] });</script>"""
    project = _project(tmp_path, html, {"1": {"cues": [{"cue": "1.1a", "on": "hello"}]}})
    # The hero scene is an alternate that no section plays, so its two ids are not reported. The id a script
    # writes sits in no scene wrapper, so its prefix names the section that owns it.
    assert uncued_elements(project, project.cue_specs()) == [
        ("01", "1.9late", "deck/index.html"),
        ("01", "close", "deck/index.html"),
    ]


def test_a_project_with_no_cues_file_has_no_uncued_element_at_all(tmp_path):
    """A page that keeps its own built-in timing cues nothing, so no element on it waits for a phrase.

    The rule sits in the scan rather than in each caller, because `align` and `preflight` both run it.
    """
    root = tmp_path / "proj"
    (root / "deck").mkdir(parents=True)
    (root / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (root / "deck" / "index.html").write_text('<b data-cue="1.1a"></b><i data-cue="1.2b"></i>', encoding="utf-8")
    project = Project.load(root, environ={})
    assert not project.cues.exists()
    assert uncued_elements(project, project.cue_specs()) == []
    assert unknown_cue_ids(project, project.cue_specs()) == []
