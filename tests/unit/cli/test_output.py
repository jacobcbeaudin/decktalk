"""The text a person reads: the summary that leads it, and the clock it is written in."""

from __future__ import annotations

from types import SimpleNamespace

from decktalk.artifacts import CueTimes
from decktalk.cli.output import align_table, lead, mmss, status_table, verify_table
from decktalk.stages.align import AlignResult, SectionCueTimes
from decktalk.status import RunStatus, SectionStatus, StatusResult
from decktalk.verdicts import Findings, Verdict


def test_mmss_rounds_before_splitting_minutes():
    assert mmss(179.6) == "3:00"
    assert mmss(59.5) == "1:00"
    assert mmss(197.96) == "3:18"
    assert mmss(0.0) == "0:00"
    assert mmss(None) == "  --  "


def test_the_summary_leads_with_the_command_s_own_counts():
    assert lead({"sections": 9, "cues": 24}, Findings()) == "sections 9, cues 24"
    assert lead({"unresolved": 0, "unknown": None}, Findings()) == "unresolved 0"
    assert lead({}, Findings()) == ""


def test_the_summary_names_what_was_found_when_anything_was():
    assert lead({"cues": 24}, Findings(certain=1, uncertain=2)) == "cues 24 | 1 certain, 2 uncertain finding(s)"
    assert lead({}, Findings(uncertain=1)) == "0 certain, 1 uncertain finding(s)"


def test_the_align_table_names_every_cue_it_resolved_and_counts_what_it_did_not():
    section = SectionCueTimes(key="03", speech_end=18.5, min_seconds=20.0, resolved=[])
    section.resolved.append(SimpleNamespace(cue="3.1bowl", at=4.25))
    result = AlignResult(cue_times=CueTimes(), sections=[section], unresolved=1, estimated=False, unknown=0)
    lines = align_table(result).splitlines()
    assert lines[0].split() == ["sec", "speech", "need", "cues"]
    assert lines[1].split() == ["03", "18.5", "20.0", "3.1bowl@4.25"]
    assert lines[-1] == "0 sections with cues, 1 unresolved"
    assert ";" not in align_table(result)


def test_the_verify_table_leads_with_each_section_start_and_totals_the_film():
    start = SimpleNamespace(key="01", start=0.0, probe_at=0.4, yavg=52.0, ymax=201.0, verdict=Verdict.OK)
    result = SimpleNamespace(
        starts=[start], total_seconds=41.25, black_starts=0, cuts=[], seams=[], cues=[], recordings=[]
    )
    lines = verify_table(result).splitlines()
    assert lines[0].split() == ["sec", "start", "probe", "YAVG", "YMAX", "result"]
    assert lines[1].split() == ["01", "0.00", "0.40", "52", "201", "ok"]
    assert lines[-1] == "total 41.25s, 0 black section start(s)"


def test_the_status_table_says_what_is_there_and_whether_a_build_runs(tmp_path):
    report = StatusResult(
        root=tmp_path,
        name="deck",
        script=tmp_path / "script.md",
        script_exists=True,
        cues=tmp_path / "cues.json",
        cues_exists=True,
        sections=[SectionStatus(key="01", kind="page", source="deck/index.html?scene=1", recorded=True, cut=False)],
        takes=None,
        cue_times_exists=False,
        cue_times_sections={},
        final=tmp_path / "build" / "out" / "deck.mp4",
        final_exists=False,
        final_duration=None,
        run=RunStatus(pid=7, started="2026-09-18T20:32:53.581Z", stage="record",
                      sections_done=2, sections_total=None, alive=True),
    )  # fmt: skip
    lines = status_table(report).splitlines()
    assert lines[0].startswith(f"project   {tmp_path}") and "(name: deck)" in lines[0]
    assert lines[1].split()[:3] == ["script", "script.md", "ok"]
    assert "final     not built" in lines
    assert lines[-1] == "build     running at record (started 2026-09-18T20:32:53.581Z)"
