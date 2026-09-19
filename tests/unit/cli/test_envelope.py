"""The envelope a program reads: its shape, its exit policy, its rows and its progress log."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from decktalk.cli.envelope import (
    SCHEMA,
    ProgressLog,
    envelope_of,
    error_code,
    error_of,
    exit_for,
    expand,
    finding_rows,
    line_buffer_stdout,
    usage_error,
    wrote,
)
from decktalk.errors import ConfigError, DeckTalkError, MissingInputError, ProviderError, ToolError
from decktalk.verdicts import Finding, Findings, Verdict

ENVELOPE_KEYS = ["schema", "version", "command", "ok", "exit_code", "summary", "findings", "written", "error"]


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
    assert exit_for(Findings(certain, uncertain), strict, exit_zero) == code


def test_the_envelope_carries_the_whole_contract_in_one_object():
    doc = envelope_of(
        "align",
        "0.4.0",
        ok=False,
        exit_code=1,
        summary={"sections": 9},
        findings=Findings(certain=1, uncertain=2),
        items=[{"code": "UNRESOLVED"}],
        written=["build/cue-times.json"],
        payload={"unknown": 0},
    )
    assert list(doc) == [*ENVELOPE_KEYS, "align"]
    assert (doc["schema"], doc["version"], doc["command"]) == (SCHEMA, "0.4.0", "align")
    assert (doc["ok"], doc["exit_code"]) == (False, 1)
    assert doc["findings"] == {"certain": 1, "uncertain": 2, "items": [{"code": "UNRESOLVED"}]}
    assert doc["written"] == ["build/cue-times.json"] and doc["error"] is None
    assert doc["align"] == {"unknown": 0}


def test_a_payload_is_keyed_under_the_command_that_produced_it():
    """One word names the command, the call, the type it returns and the key its payload sits under."""
    doc = envelope_of("status", "0.4.0", ok=True, exit_code=0, payload={"sections": 9})
    assert doc["command"] == "status" and doc["status"] == {"sections": 9}
    assert list(doc)[-1] == doc["command"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigError("bad"), "CONFIG"),
        (MissingInputError("gone"), "MISSING_INPUT"),
        (ProviderError("refused"), "PROVIDER"),
        (ToolError("no ffmpeg"), "TOOL"),
        (DeckTalkError("bare"), "INTERNAL"),
        (RuntimeError("bug"), "INTERNAL"),
    ],
)
def test_an_error_code_is_drawn_from_the_class_name(error, code):
    assert error_code(error) == code
    assert error_of(error)["code"] == code


def test_the_error_slot_names_where_the_problem_is_when_the_raiser_knows():
    plain = error_of(ConfigError("no [[section]] for 3"))
    assert plain == {
        "code": "CONFIG",
        "message": "no [[section]] for 3",
        "hint": None,
        "path": None,
        "line": None,
    }
    located = ConfigError("unknown key 'steps'")
    located.path = Path("/p/decktalk.toml")
    located.line = 12
    located.hint = "see docs/reference/decktalk-toml"
    slot = error_of(located, Path("/p"))
    assert (slot["path"], slot["line"], slot["hint"]) == ("decktalk.toml", 12, "see docs/reference/decktalk-toml")
    assert usage_error("unknown flag --nope")["code"] == "USAGE"


def test_a_verdict_reaches_a_payload_as_its_code_its_label_and_its_certainty():
    """A verdict is matched by its code. Nothing parses a label, so the label is never sent alone."""
    opened = expand({"cues": [{"cue": "1.1a", "verdict": Verdict.OFF_CUE}]})
    assert opened == {"cues": [{"cue": "1.1a", "verdict": {"code": "OFF_CUE", "label": "OFF CUE", "certain": True}}]}
    assert json.loads(json.dumps(opened))["cues"][0]["verdict"]["code"] == "OFF_CUE"


def test_findings_items_are_one_shape_whatever_the_payload_nested_them_in():
    payload = expand(
        {
            "starts": [{"key": "01", "verdict": Verdict.OK}, {"key": "02", "verdict": Verdict.BLACK}],
            "cues": [
                {"section": 3, "cue": "3.1bowl", "verdict": Verdict.THIN_CHANGE, "note": "0.2 percent changed"},
                {"section": 3, "cue": "3.2", "verdict": Verdict.OFF_CUE, "detail": "first change +200 ms"},
            ],
        }
    )
    rows = finding_rows(payload)
    assert [r["code"] for r in rows] == ["BLACK", "OFF_CUE", "THIN_CHANGE"]  # the certain rows lead
    assert all(sorted(r) == ["certain", "code", "cue", "detail", "label", "section", "where"] for r in rows)
    assert rows[1] == {
        "code": "OFF_CUE",
        "label": "OFF CUE",
        "certain": True,
        "section": 3,
        "cue": "3.2",
        "where": None,
        "detail": "first change +200 ms",
    }
    assert rows[0]["section"] == 2 and rows[2]["detail"] == "0.2 percent changed"


def test_a_row_that_already_has_the_shape_is_taken_as_it_is():
    """`Finding` is the envelope's row exactly, so a stage that writes one is copied, not rebuilt."""
    row = Finding(detail="no take", verdict=Verdict.MISSING, section=4, cue="4.1", where="script.md")
    payload = expand({"sections": [{"key": "04", "notes": [row.to_dict(), Finding(detail="fyi").to_dict()]}]})
    rows = finding_rows(payload)
    assert rows == [row.to_dict()]  # the NOTE row judged nothing, so it is not a finding


def test_a_written_file_is_listed_once_and_a_path_that_is_not_there_is_not_listed(tmp_path):
    """`written` is what the command wrote, so a file it names twice is one row and a miss is none."""
    real = tmp_path / "out.mp4"
    real.write_bytes(b"mp4")
    assert wrote(tmp_path, real, tmp_path / "gone.srt", real, None) == ["out.mp4"]


def test_stdout_stays_line_buffered_so_the_envelope_and_the_log_keep_their_order():
    """`--json` promises a line-buffered stdout, so a pipe reads the envelope when it is printed."""
    sys.stdout.reconfigure(line_buffering=False)
    line_buffer_stdout()
    assert sys.stdout.line_buffering is True


def test_a_row_a_command_holds_outside_its_payload_sorts_with_the_rest():
    extra = [Finding(detail="chromium missing", verdict=Verdict.MISSING, where="chromium")]
    rows = finding_rows(expand({"cues": [{"verdict": Verdict.THIN_CHANGE, "detail": "thin"}]}), extra)
    assert [r["code"] for r in rows] == ["MISSING", "THIN_CHANGE"]


def test_a_passing_verdict_is_never_a_finding():
    payload = expand({"rows": [{"verdict": v} for v in (Verdict.OK, Verdict.CHANGED, Verdict.SKIPPED, Verdict.NOTE)]})
    assert finding_rows(payload) == []


def test_the_progress_log_is_one_json_line_per_event_and_starts_empty(tmp_path):
    path = tmp_path / "build" / "progress.jsonl"
    log = ProgressLog(path, ("narrate", "align", "record"), pid=4242)
    log.event("narrate", "start")
    log.event("narrate", "done", detail="7 sections")
    log.event("align", "start", section=3)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["start", "done", "start"]
    assert [(r["stage"], r["stage_index"], r["stage_count"]) for r in rows] == [
        ("narrate", 1, 3),
        ("narrate", 1, 3),
        ("align", 2, 3),
    ]
    assert rows[1]["detail"] == "7 sections" and rows[2]["section"] == 3
    assert rows[0]["ts"].endswith("Z") and sorted(rows[0]) == sorted(
        ["ts", "pid", "stage", "stage_index", "stage_count", "section", "event", "detail"]
    )
    # Every row names the process that wrote it, which is how `status` answers whether it is alive.
    assert {r["pid"] for r in rows} == {4242}
    # A second run truncates, so a reader never mistakes the last run for this one.
    ProgressLog(path, ("narrate",))
    assert path.read_text(encoding="utf-8") == ""
