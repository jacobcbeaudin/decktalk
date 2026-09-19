"""The CLI's exit policy, its --json output, and the flags that pass straight through to a stage.

The stage functions are replaced with hand-built results here, so these tests need no
recording, no ffmpeg run, and no browser.
"""

from __future__ import annotations

import importlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from decktalk import cli
from decktalk.artifacts import CueTimes
from decktalk.cli import _exit_for, build_parser, main
from decktalk.stages.align import AlignResult, SectionCueTimes
from decktalk.verdicts import Findings, Verdict


def stage(name: str) -> ModuleType:
    """The stage module itself. `decktalk.stages.verify` as a dotted name is the re-exported function."""
    return importlib.import_module(f"decktalk.stages.{name}")


@pytest.fixture
def fake_project(monkeypatch, tmp_path):
    """Skip Project.load, so a command runs against the stage function a test installs."""
    project = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(cli, "_project", lambda args: project)
    return project


def recording_row(*verdicts: Verdict) -> SimpleNamespace:
    return SimpleNamespace(
        key="01",
        duration=9.0,
        wanted=10.5,
        y10=100.0,
        y50=100.0,
        y90=100.0,
        max50=200.0,
        verdicts=verdicts,
        stall_ms=None,
        label=" ".join(verdicts) or "ok",
        page_errors=[],
        ok=not verdicts,
        to_dict=lambda root: {"key": "01", "verdicts": [v.name for v in verdicts]},
    )


def verify_result(*cues: SimpleNamespace) -> SimpleNamespace:
    start = SimpleNamespace(key="01", start=0.0, probe_at=0.5, yavg=50.0, ymax=200.0, ok=True, verdict=Verdict.OK)
    cut = SimpleNamespace(key="01", cut_at=10.0, rms_db=-120.0, ok=True, verdict=Verdict.QUIET)
    return SimpleNamespace(
        total_seconds=10.0,
        starts=[start],
        cuts=[cut],
        cues=list(cues),
        black_starts=0,
        ok=all(c.verdict in (Verdict.CHANGED, Verdict.SKIPPED) for c in cues),
        seams=[],
        findings=Findings.of([start.verdict, cut.verdict, *(c.verdict for c in cues)]),
        to_dict=lambda root: {"cues": [{"check": c.check, "verdict": c.verdict} for c in cues]},
    )


def cue_row(check: str, verdict: Verdict, **fields) -> SimpleNamespace:
    row = dict(
        check=check,
        cue_seconds=1.0,
        final_seconds=1.0,
        changed_percent=1.0,
        control_percent=0.0,
        ok=verdict == Verdict.CHANGED,
        note="",
        offset_ms=0,
        av_ms=None,
        verdict=verdict,
        reason=None,
    )
    row.update(fields)
    return SimpleNamespace(**row)


@pytest.mark.parametrize(
    ("certain", "uncertain", "strict", "exit_zero", "code"),
    [
        (0, 0, False, False, 0),
        (0, 0, True, False, 0),
        (0, 2, False, False, 0),
        (0, 2, True, False, 1),
        (1, 0, False, False, 1),
        (1, 0, True, False, 1),
        (1, 3, False, True, 0),
        (0, 3, True, True, 0),
    ],
)
def test_exit_policy_table(certain, uncertain, strict, exit_zero, code):
    assert _exit_for(Findings(certain, uncertain), strict, exit_zero) == code


def test_verdicts_carry_a_code_a_label_and_a_certainty():
    assert Verdict.THIN_CHANGE.name == "THIN_CHANGE" and str(Verdict.THIN_CHANGE) == "THIN CHANGE?"
    assert not Verdict.THIN_CHANGE.certain and not Verdict.THIN_CHANGE.passing
    assert Verdict.STALLED.certain and Verdict.NO_COVER.certain and Verdict.MISSING.certain
    assert not Verdict.BLACK_UNSURE.certain and not Verdict.KATEX_UNSURE.certain
    assert Verdict.CHANGED.passing and Verdict.OK.passing and not Verdict.CHANGED.certain
    assert Findings.of([Verdict.THIN_CHANGE, Verdict.CHANGED]) == Findings(0, 1)
    assert Findings.of([Verdict.OK, Verdict.BLACK_UNSURE, Verdict.KATEX_UNSURE, Verdict.TRUNCATED, None]) == Findings(
        1, 2
    )
    assert Findings(1, 2) + Findings(0, 1) == Findings(1, 3)


def test_doctor_json_stdout_is_pure_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # keeps the test free of Chromium
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    code = main(["doctor", "--json"])
    out = capsys.readouterr().out
    doc = json.loads(out)  # the whole of stdout parses, so nothing else was printed there
    assert doc["command"] == "doctor" and isinstance(doc["version"], str)
    names = {c["name"]: c for c in doc["doctor"]["components"]}
    assert names["python"]["ok"] is True
    assert names["katex"]["ok"] is True
    assert names["katex"]["detail"].startswith("0.18.7 in the wheel")
    # Chromium is missing here, and KaTeX ships in the wheel.
    assert doc["findings"]["certain"] >= 1 and doc["findings"]["uncertain"] == 0
    assert doc["ok"] is False and code == 1
    assert main(["doctor", "--json", "--exit-zero"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_a_command_takes_only_the_exit_flags_its_own_findings_can_reach():
    """`doctor` writes only certain rows and `status` judges nothing, so the parser refuses the rest."""
    for argv in (["doctor", "--strict"], ["status", "--strict"], ["status", "--exit-zero"]):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(argv)
        assert exit_info.value.code == 2
    assert build_parser().parse_args(["doctor", "--exit-zero"]).strict is False
    status_args = build_parser().parse_args(["status"])
    assert status_args.strict is False and status_args.exit_zero is False


def test_doctor_names_every_missing_component_and_exits_1(monkeypatch, capsys):
    from decktalk import scaffold
    from decktalk.scaffold import DoctorRow

    katex = "/wheel/katex lacks katex.min.css  -> reinstall decktalk"
    rows = [
        DoctorRow("python", True, "3.13.1 (/venv/bin/python3)"),
        DoctorRow("ffmpeg", True, "/bin/ffmpeg"),
        DoctorRow("katex", False, katex),
    ]
    monkeypatch.setattr(scaffold, "doctor", lambda: rows)
    assert main(["doctor"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"katex     MISSING {katex}"
    assert main(["doctor", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["doctor", "--json", "--exit-zero"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"] == {"certain": 1, "uncertain": 0}
    assert doc["doctor"]["components"][-1] == {"name": "katex", "ok": False, "detail": katex}
    capsys.readouterr()
    # A second missing component is named too, and the command still exits 1.
    rows.insert(1, DoctorRow("chromium", False, "playwright package missing"))
    assert main(["doctor"]) == 1
    assert "chromium  MISSING playwright package missing" in capsys.readouterr().out.splitlines()


def test_status_json_on_scaffold(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    from decktalk.scaffold import init

    root = init(tmp_path / "lesson", name="lesson")
    assert main(["status", "-p", str(root), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "status" and doc["ok"] is True
    assert doc["findings"] == {"certain": 0, "uncertain": 0}
    st = doc["status"]
    assert st["project"]["name"] == "lesson"
    assert st["project"]["script"] == "script.md" and st["project"]["script_exists"] is True
    assert st["sections"] and not any(s["recorded"] or s["cut"] for s in st["sections"])
    # The scaffold has seven page sections and two clip sections, the edit's BEFORE and AFTER.
    assert [s["kind"] for s in st["sections"]] == ["page"] * 4 + ["clip", "page", "clip", "page", "page"]
    assert st["sections"][0]["key"] == "01" and st["sections"][0]["source"].startswith("deck/index.html?scene=")
    assert st["timeline"] == {"exists": False, "estimated": False, "total_seconds": None, "sections": []}
    assert st["cue_times"] == {"exists": False, "sections": []}
    assert st["final"] == {"path": "build/out/lesson.mp4", "exists": False, "duration": None}
    assert st["outputs"]["srt"] == {"path": "build/out/lesson.srt", "exists": False}
    # The table reads the same report.
    assert main(["status", "-p", str(root)]) == 0
    out = capsys.readouterr().out
    assert "final     not built" in out and "captions  build/out/lesson.srt  not built" in out


def test_check_exits_1_on_truncated_without_strict(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(stage("measure"), "check", lambda project, only=None: [recording_row(Verdict.TRUNCATED)])
    assert main(["check"]) == 1
    assert "TRUNCATED" in capsys.readouterr().out
    assert main(["check", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["check", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"] == {"certain": 1, "uncertain": 0}
    assert doc["check"]["recordings"] == [{"key": "01", "verdicts": ["TRUNCATED"]}]


def test_check_exits_0_on_uncertain_unless_strict(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(
        stage("measure"),
        "check",
        lambda project, only=None: [recording_row(Verdict.BLACK_UNSURE, Verdict.KATEX_UNSURE)],
    )
    assert main(["check"]) == 0
    assert main(["check", "--strict"]) == 1
    capsys.readouterr()
    assert main(["check", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["findings"] == {"certain": 0, "uncertain": 2}
    assert main(["check", "--json", "--strict"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_verify_exit_zero_returns_0_on_off_cue(fake_project, monkeypatch, capsys):
    result = verify_result(
        cue_row("1:1.1a", Verdict.OFF_CUE, offset_ms=200, note="first change +200 ms from the cue, limit 67 ms"),
        cue_row("1:1.1b", Verdict.SKIPPED, changed_percent=None, final_seconds=None, reason="OPTED_OUT"),
    )
    monkeypatch.setattr(stage("verify"), "verify", lambda project, checks=None, only=None: result)
    assert main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "OFF CUE" in out and "skipped OPTED_OUT" in out
    assert main(["verify", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["verify", "--json", "--exit-zero"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"] == {"certain": 1, "uncertain": 0}
    assert doc["verify"]["cues"][0] == {"check": "1:1.1a", "verdict": "OFF CUE"}


def test_cli_verify_takes_its_cues_as_positionals_only(fake_project, monkeypatch, capsys):
    calls = []

    def fake_verify(project, checks=None, only=None):
        calls.append((checks, only))
        return verify_result(cue_row("1:1.1a", Verdict.CHANGED))

    monkeypatch.setattr(stage("verify"), "verify", fake_verify)
    assert main(["verify", "1:1.1a", "2:2.1b", "3:3.1c", "--only", "2", "--only", "3"]) == 0
    assert main(["verify"]) == 0
    assert calls == [(["1:1.1a", "2:2.1b", "3:3.1c"], [2, 3]), (None, None)]
    args = build_parser().parse_args(["verify", "4:4.1s1"])
    assert args.checks == ["4:4.1s1"] and not args.strict and not args.exit_zero


def test_align_and_build_accept_allow_unknown_cues():
    assert build_parser().parse_args(["align", "--allow-unknown-cues", "--json"]).allow_unknown_cues is True
    assert build_parser().parse_args(["build", "--allow-unknown-cues"]).allow_unknown_cues is True
    assert build_parser().parse_args(["align"]).allow_unknown_cues is False


def test_align_counts_unknown_ids_unless_allowed(fake_project, monkeypatch, capsys):
    seen = []

    def fake_resolve(project, *, allow_unknown_cues=False):
        # The real result's own arithmetic, so this test proves the CLI and not a number typed here.
        seen.append(allow_unknown_cues)
        section = SectionCueTimes(key="04", speech_end=20.0, min_seconds=25.0, resolved=[])
        result = AlignResult(
            cue_times=CueTimes(),
            sections=[section],
            unresolved=0,
            estimated=False,
            unknown=1,
            allow_unknown_cues=allow_unknown_cues,
        )
        return SimpleNamespace(
            cue_times=result.cue_times,
            sections=result.sections,
            unresolved=result.unresolved,
            unknown=result.unknown,
            estimated=result.estimated,
            findings=result.findings,
            to_dict=lambda root: {"unknown": 1},
        )

    monkeypatch.setattr(stage("align"), "align", fake_resolve)
    assert main(["align", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 1, "uncertain": 1} and doc["align"] == {"unknown": 1}
    assert main(["align", "--allow-unknown-cues"]) == 0
    assert main(["align", "--allow-unknown-cues", "--strict"]) == 1  # the min_seconds shortfall is uncertain
    assert seen == [False, True, True]


def test_screenshots_after_needs_exactly_one_slide(fake_project, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stage("screenshots"),
        "screenshot_slides",
        lambda project, pages=None, slides=None, cues=None: calls.append((pages, slides, cues)) or [],
    )
    for argv in (
        ["screenshots", "--after", "3.1eq"],
        ["screenshots", "--slide", "3.1", "--slide", "3.2", "--after", "3.1eq"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    assert main(["screenshots", "--slide", "3.1", "--after", "3.1eq", "--after", "3.1p1"]) == 0
    assert calls == [(None, ["3.1"], ["3.1eq", "3.1p1"])]
