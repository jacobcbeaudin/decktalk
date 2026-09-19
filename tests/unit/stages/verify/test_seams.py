"""Section starts, quiet cuts and the seam into a section that carries the picture before it."""

from __future__ import annotations

import json

from decktalk.artifacts import Timeline, TimelineSection, Word
from decktalk.cli import main
from decktalk.media import audio
from decktalk.model import Project
from decktalk.stages.verify import verify
from decktalk.stages.verify.seams import cut_checks, seam_checks, start_checks
from decktalk.verdicts import Verdict


def test_verify_flags_a_pop_at_the_cut_into_a_seamless_section(verify_project, pages_toml, monkeypatch, capsys):
    from decktalk.media import frames as frames_module

    seamless_toml = pages_toml.replace(
        'number = 2\npage = "deck/index.html"\n', 'number = 2\npage = "deck/index.html"\nseamless = true\n'
    )
    seamless_toml = seamless_toml.replace(
        'number = 3\npage = "deck/index.html"\n', 'number = 3\npage = "deck/index.html"\nseamless = true\n'
    )
    p = verify_project({}, toml=seamless_toml)
    calls: list[tuple[float, float, dict]] = []
    share = [0.05]

    def changed(path, t1, t2, **kw):
        calls.append((round(t1, 3), round(t2, 3), kw))
        return share[0]

    monkeypatch.setattr(frames_module, "changed_pixels_percent", changed)
    result = verify(p)
    # Section 1 dips out over 0.16 s, so the last frame compared sits before the dip. Section 3 is not assembled.
    assert calls == [(4.78, 5.0, {"level": 40, "width": 480, "height": 270})]
    (row,) = result.seams
    assert (row.key, row.cut_at, row.verdict, row.ok) == ("02", 5.0, "ok", True) and result.ok

    share[0] = 0.5
    result = verify(p)
    assert [c.verdict for c in result.seams] == ["POP AT CUT"] and not result.ok
    assert result.to_dict(p.root)["seams"] == [
        {"key": "02", "cut_at": 5.0, "last_at": 4.78, "first_at": 5.0, "changed_percent": 0.5, "verdict": "POP AT CUT"}
    ]
    assert main(["-p", str(p.root), "verify"]) == 1
    assert "POP AT CUT" in capsys.readouterr().out
    assert main(["-p", str(p.root), "verify", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 1, "uncertain": 0} and doc["verify"]["seams"][0]["verdict"] == "POP AT CUT"

    # A straight cut compares the frame just before the cut, and a section without the key gets no row.
    calls.clear()
    (p.root / "decktalk.toml").write_text(seamless_toml + "\n[transition]\ndips = []\n", encoding="utf-8")
    assert [c.last_at for c in verify(Project.load(p.root, environ={})).seams] == [4.94]
    (p.root / "decktalk.toml").write_text(pages_toml, encoding="utf-8")
    assert verify(Project.load(p.root, environ={})).seams == []


def test_a_section_that_opens_on_black_is_a_certain_finding(verify_project, monkeypatch):
    from decktalk.media import frames as frames_module

    project = verify_project({"01": "a@1.0"})
    monkeypatch.setattr(frames_module, "luma_at", lambda path, t, crop=None: (1.0, 2.0))
    rows = start_checks(project, project.final, {"01": 0.0, "02": 5.0})
    assert [(r.key, r.verdict, r.ok) for r in rows] == [("01", Verdict.BLACK, False), ("02", Verdict.BLACK, False)]
    assert rows[0].to_dict()["ymax"] == 2.0


def test_a_cut_is_quiet_when_the_narration_has_stopped(verify_project, monkeypatch):
    project = verify_project({"01": "a@1.0"})
    (project.narration_dir / "narration.mp3").write_bytes(b"x")
    timeline = Timeline(
        narration="narration.mp3",
        total_seconds=5.0,
        sections={"01": TimelineSection("A", 0.0, 5.0, 5.0, 4.5, [Word("Hi", 0.7, 1.0)])},
    )
    monkeypatch.setattr(audio, "rms_db", lambda path, start, length: -60.0)
    [quiet] = cut_checks(project, timeline, {"01": 0.0})
    assert (quiet.key, quiet.verdict, quiet.ok) == ("01", Verdict.QUIET, True)
    monkeypatch.setattr(audio, "rms_db", lambda path, start, length: -3.0)
    [loud] = cut_checks(project, timeline, {"01": 0.0})
    assert (loud.verdict, loud.ok) == (Verdict.SPEECH_AT_CUT, False)
    # With no narration track there is nothing to listen to, so there are no rows at all.
    assert cut_checks(project, None, {"01": 0.0}) == []


def test_a_section_that_sets_no_seamless_key_has_no_seam_row(verify_project):
    project = verify_project({"01": "a@1.0"})
    assert seam_checks(project, project.final, {"01": 0.0, "02": 5.0}) == []
