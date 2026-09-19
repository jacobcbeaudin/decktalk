"""decktalk preflight's frozen frames, in a real headless Chromium with the ffmpeg that `decktalk install` fetches.

uv run pytest -m browser

These tests start their own Chromium, so they live apart from test_runtime.py, whose module-wide page
fixture keeps a Playwright session open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.scaffold import init
from decktalk.toolchain.assets import runtime_path
from decktalk.verdicts import Findings

pytestmark = [pytest.mark.browser, pytest.mark.media]


def template_cues(root: Path, scene: str) -> list[str]:
    """The cue ids the scaffold's cues.json lists for one scene, in order."""
    data = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    return [c["cue"] for s in data["sections"].values() for c in s["cues"] if c["cue"].split(".")[0] == scene]


def test_preflight_estimates_each_reveal_and_the_seam_from_frozen_frames(tmp_path, monkeypatch):
    """Sections 1 and 2 of the scaffold, with section 2 set to open on the Open's last picture."""
    from decktalk.project import Project
    from decktalk.stages.narrate import narrate
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = init(tmp_path / "p", name="p")
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("scene = 2\n", "scene = 2\nseamless = true\n", 1))
    narrate(Project.load(root, environ={}), silent=True)  # the timeline gives the pages their words
    p = Project.load(root, environ={})
    result = preflight(p, only=[1, 2])
    assert result.frames == root / "build" / "preflight"
    rows = {c.check: c for c in result.cues}
    assert list(rows) == [f"1:{c}" for c in template_cues(root, "1")] + [f"2:{c}" for c in template_cues(root, "2")]
    for c in result.cues:
        assert c.verdict in ("changed", "THIN CHANGE?"), (c.check, c.changed_percent, c.verdict, c.reason)
        assert c.before is not None and c.after is not None and c.before.exists() and c.after.exists()
    assert rows["2:2.1lesson"].changed_percent > 5  # the push into the lesson moves most of the frame
    # The first cue of each section freezes its slide just before the cue, and each later cue after the one before.
    assert rows["1:1.1bowl"].before.name == "slide-1.1-before-1.1bowl.png"
    assert rows["1:1.1ball"].before == rows["1:1.1bowl"].after
    # Scene 2 opens on the Open's last picture, so the seam does not show.
    [seam] = result.seams
    assert (seam.key, seam.verdict) == ("02", "ok") and seam.changed_percent == 0.0
    assert seam.last == rows["1:1.1word"].after and seam.first == rows["2:2.1mark"].before


def test_preflight_only_checks_the_cut_into_a_seamless_section_it_names(tmp_path, monkeypatch):
    """`--only 2` on a seamless section after section 1 still resolves section 1, but reports only section 2."""
    from decktalk.project import Project
    from decktalk.stages.narrate import narrate
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = init(tmp_path / "p", name="p")
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("scene = 2\n", "scene = 2\nseamless = true\n", 1))
    narrate(Project.load(root, environ={}), silent=True)
    result = preflight(Project.load(root, environ={}), only=[2])
    [seam] = result.seams
    assert (seam.key, seam.verdict) == ("02", "ok"), (seam.verdict, seam.reason, seam.detail)
    assert seam.changed_percent == 0.0 and seam.last is not None and seam.first is not None
    assert [t.segment.key for t in result.takes] == ["02"]
    assert {c.check.split(":")[0] for c in result.cues} == {"2"}
    assert set(result.align.cue_times.sections) == {"02"}


SYNTH_PAGE = """
const box = (x, size, cue) => `<div ${cue ? `data-cue="${cue}" data-reveal="instant"` : ""}
  style="position:absolute;left:${x}px;top:200px;width:${size}px;height:${size}px;background:#000"></div>`;
DeckTalk.scene(1, { slides: [{ id: '1.1', preview: { '1.1in': 0, '1.1big': 1, '1.1mid': 2, '1.1dot': 3 },
  render: () => box(100, 400, '1.1in') + box(700, 400, '1.1big')
    + box(1300, 60, '1.1mid') + box(1500, 8, '1.1dot') }] });
DeckTalk.scene(2, { slides: [{ id: '2.1', preview: { '2.1go': 1 },
  render: () => box(1300, 400, '') + box(100, 60, '2.1go') }] });
DeckTalk.scene(3, { slides: [{ id: '3.1', preview: { '3.1go': 1 },
  render: () => box(1300, 400, '') + box(100, 60, '') + box(700, 400, '3.1go') }] });
"""


def test_preflight_reads_each_verdict_from_a_synthetic_page(tmp_path, monkeypatch):
    """A big reveal, a thin one, a dot, a cue at the start, a cut that pops, and a seamless cut."""
    from decktalk.project import Project
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = tmp_path / "synth"
    root.mkdir()
    (root / "page.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#fff}</style></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script><script>{SYNTH_PAGE}</script></body></html>',
        encoding="utf-8",
    )
    (root / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\npage = 'page.html'\n"
        "[[section]]\nnumber = 2\npage = 'page.html'\nseamless = true\n"
        "[[section]]\nnumber = 3\npage = 'page.html'\nseamless = true\n",
        encoding="utf-8",
    )
    words = "one two three four five six seven eight nine ten"
    (root / "script.md").write_text(
        f"## 1. A\n\n{words}.\n\n## 2. B\n\n{words}.\n\n## 3. C\n\n{words}.\n", encoding="utf-8"
    )
    cues = {
        "1": [("1.1in", "$start"), ("1.1big", "three"), ("1.1mid", "six"), ("1.1dot", "nine")],
        "2": [("2.1go", "five")],
        "3": [("3.1go", "five")],
    }
    doc = {"sections": {k: {"cues": [{"cue": c, "on": on} for c, on in v]} for k, v in cues.items()}}
    (root / "cues.json").write_text(json.dumps(doc), encoding="utf-8")
    result = preflight(Project.load(root, environ={}))
    got = {c.check: (c.verdict, c.reason) for c in result.cues}
    assert got == {
        "1:1.1in": ("skipped", "AT_SECTION_START"),
        "1:1.1big": ("changed", None),
        "1:1.1mid": ("THIN CHANGE?", None),
        "1:1.1dot": ("NO CHANGE", None),
        "2:2.1go": ("THIN CHANGE?", None),
        "3:3.1go": ("changed", None),
    }
    shares = {c.check: c.changed_percent for c in result.cues}
    assert shares["1:1.1big"] == pytest.approx(100 * 100 * 100 / (480 * 270), rel=0.05)  # 400 px at 1080p is 100 px
    # Section 1 ends on boxes at 100 and 700, and section 2 opens on one at 1300. Section 3 opens as section 2 ends.
    assert {k.key: k.verdict for k in result.seams} == {"02": "POP AT CUT", "03": "ok"}
    assert [k.changed_percent for k in result.seams][1] == 0.0
    assert result.findings() == Findings(certain=2, uncertain=2)
