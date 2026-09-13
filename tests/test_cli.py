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
from decktalk.cli import _exit_for, build_parser, main
from decktalk.verdicts import CERTAIN, Findings, count, is_certain, split


def stage(name: str) -> ModuleType:
    """The stage module itself. `decktalk.stages.verify` as a dotted name is the re-exported function."""
    return importlib.import_module(f"decktalk.stages.{name}")


@pytest.fixture
def fake_project(monkeypatch, tmp_path):
    """Skip Project.load, so a command runs against the stage function a test installs."""
    project = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(cli, "_project", lambda args: project)
    return project


def recording_row(verdict: str) -> SimpleNamespace:
    return SimpleNamespace(
        key="01",
        duration=9.0,
        wanted=10.5,
        y10=100.0,
        y50=100.0,
        y90=100.0,
        max50=200.0,
        verdict=verdict,
        page_errors=[],
        ok=verdict == "ok",
        to_dict=lambda root: {"key": "01", "verdict": verdict},
    )


def verify_result(*cues: SimpleNamespace) -> SimpleNamespace:
    start = SimpleNamespace(key="01", start=0.0, probe_at=0.5, yavg=50.0, ymax=200.0, ok=True)
    cut = SimpleNamespace(key="01", cut_at=10.0, rms_db=-120.0, ok=True)
    return SimpleNamespace(
        total_seconds=10.0,
        starts=[start],
        cuts=[cut],
        cues=list(cues),
        black_starts=0,
        ok=all(c.verdict in ("changed", "skipped") for c in cues),
        to_dict=lambda root: {"cues": [{"check": c.check, "verdict": c.verdict} for c in cues]},
    )


def cue_row(check: str, verdict: str, **fields) -> SimpleNamespace:
    row = dict(
        check=check,
        cue_seconds=1.0,
        final_seconds=1.0,
        changed_percent=1.0,
        control_percent=0.0,
        ok=verdict == "changed",
        note="",
        offset_ms=0,
        av_ms=None,
        verdict=verdict,
        reason=None,
    )
    row.update(fields)
    return SimpleNamespace(**row)


@pytest.mark.parametrize(
    ("certain", "uncertain", "strict", "no_fail", "code"),
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
def test_exit_policy_table(certain, uncertain, strict, no_fail, code):
    assert _exit_for(Findings(certain, uncertain), strict, no_fail) == code


def test_verdicts_split_and_certainty():
    assert split("BLACK? TRUNCATED STALLED 1840ms PAGE ERROR") == ["BLACK?", "TRUNCATED", "STALLED", "PAGE ERROR"]
    assert split("ok") == ["ok"]
    assert is_certain("STALLED 1840ms") and is_certain("NO COVER")
    assert not is_certain("BLACK?") and not is_certain("KATEX?") and not is_certain("changed")
    assert "MISSING" in CERTAIN and "UNKNOWN" in CERTAIN
    assert count(["ok", "BLACK? KATEX?", "TRUNCATED", "skipped", "SOMETHING NEW"]) == Findings(1, 4)


def test_doctor_json_stdout_is_pure_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # keeps the test free of Chromium
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    code = main(["doctor", "--json"])
    out = capsys.readouterr().out
    doc = json.loads(out)  # the whole of stdout parses, so nothing else was printed there
    assert doc["command"] == "doctor" and isinstance(doc["version"], str)
    names = {c["name"]: c for c in doc["doctor"]["components"]}
    assert names["python"]["ok"] is True and names["katex"]["ok"] is False
    assert doc["findings"]["certain"] >= 2 and doc["findings"]["uncertain"] == 0
    assert doc["ok"] is False and code == 1
    assert main(["doctor", "--json", "--no-fail"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False


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
    assert st["sections"] and all(s["kind"] == "page" and not s["recorded"] and not s["cut"] for s in st["sections"])
    assert st["sections"][0]["key"] == "01" and st["sections"][0]["source"].startswith("deck/index.html?scene=")
    assert st["timeline"] == {"exists": False, "estimated": False, "total_seconds": None, "sections": []}
    assert st["beats"] == {"exists": False, "sections": []}
    assert st["final"] == {"path": "build/out/lesson.mp4", "exists": False, "duration": None}
    assert st["outputs"]["srt"] == {"path": "build/out/lesson.srt", "exists": False}
    # The table reads the same report.
    assert main(["status", "-p", str(root)]) == 0
    out = capsys.readouterr().out
    assert "final    not built" in out and "captions build/out/lesson.srt  not built" in out


def test_check_exits_1_on_truncated_without_strict(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(stage("measure"), "check", lambda project, only=None: [recording_row("TRUNCATED")])
    assert main(["check"]) == 1
    assert "TRUNCATED" in capsys.readouterr().out
    assert main(["check", "--no-fail"]) == 0
    capsys.readouterr()
    assert main(["check", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"] == {"certain": 1, "uncertain": 0}
    assert doc["check"]["recordings"] == [{"key": "01", "verdict": "TRUNCATED"}]


def test_check_exits_0_on_uncertain_unless_strict(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(stage("measure"), "check", lambda project, only=None: [recording_row("BLACK? KATEX?")])
    assert main(["check"]) == 0
    assert main(["check", "--strict"]) == 1
    capsys.readouterr()
    assert main(["check", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["findings"] == {"certain": 0, "uncertain": 2}
    assert main(["check", "--json", "--strict"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_verify_no_fail_returns_0_on_off_cue(fake_project, monkeypatch, capsys):
    result = verify_result(
        cue_row("1:1.1a", "OFF CUE", offset_ms=200, note="first change +200 ms from the cue, limit 67 ms"),
        cue_row("1:1.1b", "skipped", changed_percent=None, final_seconds=None, reason="OPTED_OUT"),
    )
    monkeypatch.setattr(stage("verify"), "verify", lambda project, checks=None, only=None: result)
    assert main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "OFF CUE" in out and "skipped OPTED_OUT" in out
    assert main(["verify", "--no-fail"]) == 0
    capsys.readouterr()
    assert main(["verify", "--json", "--no-fail"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"] == {"certain": 1, "uncertain": 0}
    assert doc["verify"]["cues"][0] == {"check": "1:1.1a", "verdict": "OFF CUE"}


def test_cli_verify_cue_flag_merges_with_positional(fake_project, monkeypatch, capsys):
    calls = []

    def fake_verify(project, checks=None, only=None):
        calls.append((checks, only))
        return verify_result(cue_row("1:1.1a", "changed"))

    monkeypatch.setattr(stage("verify"), "verify", fake_verify)
    assert main(["verify", "1:1.1a", "--cue", "2:2.1b", "--cue", "3:3.1c", "--only", "2", "--only", "3"]) == 0
    assert main(["verify"]) == 0
    assert calls == [(["1:1.1a", "2:2.1b", "3:3.1c"], [2, 3]), (None, None)]
    args = build_parser().parse_args(["verify", "--cue", "4:4.1s1"])
    assert args.checks == [] and args.cue == ["4:4.1s1"] and not args.strict and not args.no_fail


def test_beats_and_build_accept_allow_unknown():
    assert build_parser().parse_args(["beats", "--allow-unknown", "--json"]).allow_unknown is True
    assert build_parser().parse_args(["build", "--allow-unknown"]).allow_unknown is True
    assert build_parser().parse_args(["beats"]).allow_unknown is False


def test_beats_counts_unknown_ids_unless_allowed(fake_project, monkeypatch, capsys):
    seen = []

    def fake_resolve(project, *, allow_unknown=False):
        seen.append(allow_unknown)
        section = SimpleNamespace(key="04", speech_end=20.0, min_seconds=25.0, resolved={}, notes=[], skipped=None)
        return SimpleNamespace(
            beats=SimpleNamespace(sections={}),
            sections=[section],
            unresolved=0,
            unknown=1,
            estimated=False,
            to_dict=lambda root: {"unknown": 1},
        )

    monkeypatch.setattr(stage("beats"), "resolve_beats", fake_resolve)
    assert main(["beats", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 1, "uncertain": 1} and doc["beats"] == {"unknown": 1}
    assert main(["beats", "--allow-unknown"]) == 0
    assert main(["beats", "--allow-unknown", "--strict"]) == 1  # the min_seconds shortfall is uncertain
    assert seen == [False, True, True]


def test_shots_cue_needs_exactly_one_step(fake_project, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stage("shots"),
        "shoot_steps",
        lambda project, pages=None, steps=None, cues=None: calls.append((pages, steps, cues)) or [],
    )
    for argv in (["shots", "--cue", "3.1eq"], ["shots", "--step", "3.1", "--step", "3.2", "--cue", "3.1eq"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    assert main(["shots", "--step", "3.1", "--cue", "3.1eq", "--cue", "3.1p1"]) == 0
    assert calls == [(None, ["3.1"], ["3.1eq", "3.1p1"])]
