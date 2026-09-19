"""Rendering the frozen frames and comparing them: the verdicts and the rows."""

from __future__ import annotations

from dataclasses import replace

from decktalk.artifacts import CueTimes
from decktalk.settings import Settings
from decktalk.verdicts import Findings, Verdict


def test_preflight_verdicts_and_findings():
    from decktalk.stages.align import AlignResult
    from decktalk.stages.preflight import PreflightResult
    from decktalk.stages.preflight.scan import CueEstimate, SeamEstimate, cue_verdict

    cfg = Settings().verify
    assert [cue_verdict(x, cfg) for x in (0.05, 0.2, 0.5)] == [
        Verdict.NO_CHANGE,
        Verdict.THIN_CHANGE,
        Verdict.CHANGED,
    ]
    result = PreflightResult(
        voice={}, narration=Settings().narration, takes=[], note=None, estimated=[], rate=0.30,
        align=AlignResult(cue_times=CueTimes(), sections=[], unresolved=1, estimated=True, unknown=2),
        cues=[CueEstimate("1:a", 1.0, "1.1", 0.2, Verdict.THIN_CHANGE),
              CueEstimate("1:b", 2.0, "1.1", 0.0, Verdict.NO_CHANGE),
              CueEstimate("1:c", 3.0, "1.1", 4.0, Verdict.CHANGED),
              CueEstimate("1:d", 0.0, None, None, Verdict.SKIPPED)],
        seams=[SeamEstimate("02", 3.1, Verdict.POP_AT_CUT), SeamEstimate("03", 0.0, Verdict.OK)],
    )  # fmt: skip
    assert result.findings == Findings(certain=5, uncertain=1)
    assert replace(result, allow_unknown_cues=True).findings == Findings(certain=3, uncertain=1)


def test_a_frozen_state_is_opened_at_the_url_the_recorder_would_open(tmp_path):
    """The frames are compared against what `verify` measures, so the page has to be the recorded one."""
    from decktalk.model import Project
    from decktalk.stages.preflight.freeze import Freeze
    from decktalk.stages.preflight.scan import freeze_url

    toml = (
        '[project]\nname = "t"\n\n[[section]]\nnumber = 1\npage = "deck/index.html"\n'
        'params = { theme = "dark", cues = "x@1", slide = "9.9" }\n'
    )
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    project = Project.load(tmp_path, environ={})
    section = project.page_sections[0]

    url = freeze_url(project, section, Freeze(slide="1.1", cue="1.1draw"))
    assert url.startswith("http://project.localhost/deck/index.html?")
    assert "slide=1.1" in url and "after=1.1draw" in url and "theme=dark" in url
    # The section's own cue times and its own slide belong to a playing page and never to a freeze.
    assert "cues=" not in url and "slide=9.9" not in url
    assert "before=" in freeze_url(project, section, Freeze(slide="1.1", before="1.1draw"))
