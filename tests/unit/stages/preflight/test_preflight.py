"""The preflight stage: what it plans from the words each section will have, and its frozen frames.

The frame tests drive a real headless Chromium with the ffmpeg that `decktalk install` fetches, so
each carries the browser and media markers and `uv run pytest -m browser` selects them. They start
their own Chromium, so they live apart from test_runtime.py, whose module-wide page fixture keeps a
Playwright session open. The first test needs neither, and runs in the default suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from decktalk.cli import main
from decktalk.scaffold import init
from decktalk.speech import register_speech_provider
from decktalk.toolchain.assets import RUNTIME_FILE, runtime_path
from decktalk.verdicts import Findings, SkipReason, Verdict


def template_cues(root: Path, scene: str) -> list[str]:
    """The cue ids a project's cues.json lists for one scene, in order."""
    data = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    return [c["cue"] for s in data["sections"].values() for c in s["cues"] if c["cue"].split(".")[0] == scene]


@pytest.mark.browser
@pytest.mark.media
def test_preflight_estimates_each_reveal_from_frozen_frames(tmp_path, monkeypatch):
    """Sections 1 and 2 of the starter: every reveal is measured between two frozen frames."""
    from decktalk.model import Project
    from decktalk.stages.narrate import narrate
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = init(tmp_path / "p", name="p").root
    narrate(Project.load(root, environ={}), silent=True)  # the timeline gives the pages their words
    p = Project.load(root, environ={})
    result = preflight(p, only=[1, 2])
    assert result.frames == root / "build" / "preflight"
    rows = {c.check: c for c in result.cues}
    assert list(rows) == [f"1:{c}" for c in template_cues(root, "1")] + [f"2:{c}" for c in template_cues(root, "2")]
    for c in result.cues:
        # Every reveal the starter ships clears the floor with room to spare, which is what makes it
        # a page an author can copy: CHANGED and never THIN CHANGE?.
        assert c.verdict is Verdict.CHANGED, (c.check, c.changed_percent, c.verdict, c.reason)
        assert c.before is not None and c.after is not None and c.before.exists() and c.after.exists()
    assert rows["2:2.1code"].changed_percent > 5  # the code card fills a quarter of the frame
    # The first cue of each section freezes its slide just before the cue, and each later cue after the one before.
    assert rows["1:1.1title"].before.name == "slide-1.1-before-1.1title.png"
    assert rows["1:1.1script"].before == rows["1:1.1title"].after
    # No section of the starter continues the picture of the one before, so there is no seam to check.
    assert result.seams == []


@pytest.mark.browser
@pytest.mark.media
def test_preflight_only_checks_the_cut_into_a_seamless_section_it_names(tmp_path, monkeypatch):
    """`--only 3` on a seamless section after section 2 still resolves section 2, but reports only section 3."""
    from decktalk.model import Project
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    root = synth_project(tmp_path)
    result = preflight(Project.load(root, environ={}), only=[3])
    [seam] = result.seams
    assert (seam.key, seam.verdict) == ("03", Verdict.OK), (seam.verdict, seam.reason, seam.detail)
    assert seam.changed_percent == 0.0 and seam.last is not None and seam.first is not None
    assert [t.segment.key for t in result.takes] == ["03"]
    assert {c.check.split(":")[0] for c in result.cues} == {"3"}
    assert set(result.align.cue_times.sections) == {"03"}


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


def synth_project(tmp_path: Path) -> Path:
    """A three-section project on one synthetic page, where sections 2 and 3 are seamless."""
    root = tmp_path / "synth"
    root.mkdir()
    shutil.copyfile(runtime_path(), root / RUNTIME_FILE)
    (root / "page.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#fff}</style></head><body>'
        f'<script src="{RUNTIME_FILE}"></script><script>{SYNTH_PAGE}</script></body></html>',
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
    return root


@pytest.mark.browser
@pytest.mark.media
def test_preflight_reads_each_verdict_from_a_synthetic_page(tmp_path, monkeypatch):
    """A big reveal, a thin one, a dot, a cue at the start, a cut that pops, and a seamless cut."""
    from decktalk.model import Project
    from decktalk.stages.preflight import preflight

    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    result = preflight(Project.load(synth_project(tmp_path), environ={}))
    got = {c.check: (c.verdict, c.reason) for c in result.cues}
    assert got == {
        "1:1.1in": (Verdict.SKIPPED, SkipReason.AT_SECTION_START),
        "1:1.1big": (Verdict.CHANGED, None),
        "1:1.1mid": (Verdict.THIN_CHANGE, None),
        "1:1.1dot": (Verdict.NO_CHANGE, None),
        "2:2.1go": (Verdict.THIN_CHANGE, None),
        "3:3.1go": (Verdict.CHANGED, None),
    }
    shares = {c.check: c.changed_percent for c in result.cues}
    assert shares["1:1.1big"] == pytest.approx(100 * 100 * 100 / (480 * 270), rel=0.05)  # 400 px at 1080p is 100 px
    # Section 1 ends on boxes at 100 and 700, and section 2 opens on one at 1300. Section 3 opens as section 2 ends.
    assert {k.key: k.verdict for k in result.seams} == {"02": "POP AT CUT", "03": "ok"}
    assert [k.changed_percent for k in result.seams][1] == 0.0
    assert result.findings == Findings(certain=2, uncertain=2)


def test_preflight_resolves_cues_on_the_words_each_section_will_have(
    tmp_path, monkeypatch, capsys, planned_scaffold, unchanged
):
    from decktalk.artifacts import read_words
    from decktalk.stages.align import find_phrase
    from decktalk.stages.preflight import preflight

    p, files = planned_scaffold(tmp_path, monkeypatch)
    result = preflight(p, frames=False)
    assert {t.segment.key: t.status for t in result.takes} == {
        "01": "cached",
        "02": "synthesize",
        "03": "cached",
    }
    assert result.estimated == ["02"]
    assert result.align.unresolved == 0 and result.align.unknown == 0
    resolved = {s.key: {r.cue: r.at for r in s.resolved} for s in result.align.sections}
    # A cached take resolves on its own words, which the fixture spaced 0.4 s apart, after the
    # silence the section leads with, because no silence lives inside a take.
    takes = p.takes()
    open_words = read_words(p.takes_dir / takes.sections["01"].words_file)
    lead = p.lead_seconds("01")
    at = round(open_words[find_phrase(open_words, "This is DeckTalk")].start + lead, 3)
    assert resolved["01"]["1.1title"] == at == lead
    close_words = read_words(p.takes_dir / takes.sections["03"].words_file)
    close_at = close_words[find_phrase(close_words, "Make your own")].start + p.lead_seconds("03")
    assert resolved["03"]["3.1make"] == round(close_at, 2)
    # A section that would be voiced resolves on estimated words, inside its estimated length.
    assert all(0 < t < 60 for t in resolved["02"].values()) and len(resolved["02"]) == 4
    assert result.cues == [] and result.seams == [] and result.frames is None
    assert unchanged(p, files) and not p.cue_times_path.exists() and not p.preflight_dir.exists()

    assert main(["preflight", "--no-frames", "--json", "-p", str(p.root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "preflight" and doc["ok"] is True
    payload = doc["preflight"]
    assert set(payload) == {
        "voice", "note", "placeholders", "takes", "totals", "cue_times", "cues", "seams", "warnings", "frames",
    }  # fmt: skip
    assert payload["cue_times"]["estimated_sections"] == ["02"] and payload["totals"]["synthesize"] == 1
    assert [t["hash"] for t in payload["takes"] if t["status"] == "cached"] == [
        takes.sections[key].hash for key in ("01", "03")
    ]
    assert main(["preflight", "--no-frames", "-p", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert "2 cached." in out and "frames skipped (--no-frames)" in out

    # A phrase that is not in the script, under an id the page never names, is two certain findings.
    cues_path = p.root / "cues.json"
    cues = json.loads(cues_path.read_text(encoding="utf-8"))
    cues["sections"]["1"]["cues"].append({"cue": "1.1nope", "on": "not in the script"})
    cues_path.write_text(json.dumps(cues), encoding="utf-8")
    script = p.root / "script.md"
    script.write_text(script.read_text(encoding="utf-8").replace("## 3. Close\n", "## 3. Close\n\n[CLIENT_NAME]\n", 1))
    assert main(["preflight", "--no-frames", "--json", "-p", str(p.root)]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 3, "uncertain": 0} and doc["preflight"]["placeholders"] == ["CLIENT_NAME"]
    assert main(["preflight", "--no-frames", "--json", "--allow-unknown-cues", "-p", str(p.root)]) == 1
    assert json.loads(capsys.readouterr().out)["findings"] == {"certain": 2, "uncertain": 0}
    assert main(["preflight", "--no-frames", "--exit-zero", "-p", str(p.root)]) == 0
    assert unchanged(p, files)


def test_preflight_estimates_a_take_a_later_section_of_the_same_run_would_write(tmp_path: Path) -> None:
    """Two sections of the same words share one digest, so the second is planned cached before it exists.

    `preflight` spends nothing and writes nothing, so it must read the disk rather than the plan's
    status and fall back to estimated words when the file is not there yet.
    """
    from decktalk.model import Project
    from decktalk.stages.narrate import voiced_plan
    from decktalk.stages.preflight import planned_words

    root = tmp_path / "twins"
    root.mkdir()
    (root / "decktalk.toml").write_text(
        "[project]\nname = 't'\n[voice]\nprovider = 'test-voice'\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\n[[section]]\nnumber = 2\npage = 'deck/index.html'\n",
        encoding="utf-8",
    )
    (root / "script.md").write_text("## 1. Open\n\nThe very same words.\n\n## 2. Two\n\nThe very same words.\n")
    register_speech_provider("test-voice", lambda context: _Silent())
    project = Project.load(root, environ={})
    plans, note = voiced_plan(project, project.script_sections()[1], model="m")
    assert note is None and [p.status for p in plans] == ["synthesize", "cached"]
    words, length, estimated = planned_words(project, plans[1])
    assert estimated is True and length > 0 and words


class _Silent:
    """A provider that is never asked to speak, because preflight sends nothing."""

    name = "test-voice"

    def speak(self, request):  # pragma: no cover - preflight never sends
        raise AssertionError("preflight must send nothing")

    def cache_key(self, request) -> str:
        return "test-voice"
