"""The envelope a program reads: its shape, its exit policy, its rows and its progress log."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from decktalk.cli.envelope import (
    SCHEMA,
    envelope_of,
    error_code,
    error_of,
    exit_for,
    finding_rows,
    line_buffer_stdout,
    usage_error,
    wrote,
)
from decktalk.cli.schema import ENVELOPE_KEYS, ErrorSlot
from decktalk.errors import ConfigError, DeckTalkError, ErrorCode, MissingInputError, ProviderError, ToolError
from decktalk.jsonio import read_as
from decktalk.pipeline import Stage
from decktalk.verdicts import Finding, Findings, SkipReason, Verdict


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
    unresolved = Finding(detail="phrase not found", verdict=Verdict.UNRESOLVED, section=1, where="cues.json")
    doc = envelope_of(
        Stage.ALIGN.value,
        "0.4.0",
        ok=False,
        exit_code=1,
        summary={"sections": 9},
        findings=Findings(certain=1, uncertain=2),
        items=[unresolved.to_dict()],
        written=["build/cue-times.json"],
        payload={"unknown": 0},
    )
    assert list(doc) == [*ENVELOPE_KEYS, Stage.ALIGN.value]
    assert (doc["schema"], doc["version"], doc["command"]) == (SCHEMA, "0.4.0", Stage.ALIGN.value)
    assert (doc["ok"], doc["exit_code"]) == (False, 1)
    assert doc["findings"] == {"certain": 1, "uncertain": 2, "items": [unresolved.to_dict()]}
    assert doc["written"] == ["build/cue-times.json"] and doc["error"] is None
    assert doc[Stage.ALIGN.value] == {"unknown": 0}


def test_a_payload_is_keyed_under_the_command_that_produced_it():
    """One word names the command, the call, the type it returns and the key its payload sits under."""
    doc = envelope_of("status", "0.4.0", ok=True, exit_code=0, payload={"sections": 9})
    assert doc["command"] == "status" and doc["status"] == {"sections": 9}
    assert list(doc)[-1] == doc["command"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigError("bad"), ErrorCode.CONFIG),
        (MissingInputError("gone"), ErrorCode.MISSING_INPUT),
        (ProviderError("refused"), ErrorCode.PROVIDER),
        (ToolError("no ffmpeg"), ErrorCode.TOOL),
        (DeckTalkError("bare"), ErrorCode.INTERNAL),
        (RuntimeError("bug"), ErrorCode.INTERNAL),
    ],
)
def test_an_error_code_is_the_one_its_class_carries(error, code):
    assert error_code(error) is code
    assert read_as(ErrorSlot, error_of(error)).code is code


def test_a_subclass_that_carries_a_result_reports_the_code_of_the_error_it_is_a_kind_of():
    """The codes are a closed list, so a stage cannot mint a seventh no caller can dispatch on."""
    from decktalk.stages.align import UnknownCueError

    assert issubclass(UnknownCueError, ConfigError)
    assert error_code(UnknownCueError.__new__(UnknownCueError)) is ErrorCode.CONFIG

    class DeeplyNestedError(MissingInputError):
        pass

    assert error_code(DeeplyNestedError("x")) is ErrorCode.MISSING_INPUT


def test_every_error_the_package_can_raise_carries_one_of_the_six_codes():
    """A reader dispatches on `error.code`, so every code it can meet is named by the contract."""
    import decktalk

    seen = set()
    stack = [decktalk.DeckTalkError]
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if cls is not decktalk.DeckTalkError:
            seen.add(error_code(cls.__new__(cls)))
    assert seen <= set(ErrorCode), f"these codes are outside the contract: {sorted(map(repr, seen - set(ErrorCode)))}"


def test_the_error_slot_names_where_the_problem_is_when_the_raiser_knows():
    plain = read_as(ErrorSlot, error_of(ConfigError("no [[section]] for 3")))
    assert plain == ErrorSlot(code=ErrorCode.CONFIG, message="no [[section]] for 3", hint=None, path=None, line=None)
    located = ConfigError("unknown key 'steps'")
    located.path = Path("/p/decktalk.toml")
    located.line = 12
    located.hint = "see docs/reference/decktalk-toml"
    slot = read_as(ErrorSlot, error_of(located, Path("/p")))
    assert (slot.path, slot.line, slot.hint) == ("decktalk.toml", 12, "see docs/reference/decktalk-toml")
    assert read_as(ErrorSlot, usage_error("unknown flag --nope")).code is ErrorCode.USAGE


def test_a_payload_verdict_is_its_code_its_label_and_its_certainty():
    """A verdict is matched by its code. Nothing parses a label, so the label is never sent alone."""
    row = {"cue": "1.1a", "verdict": Verdict.OFF_CUE.to_dict()}
    written = json.loads(json.dumps({"cues": [row]}))
    assert Verdict.from_dict(written["cues"][0]["verdict"]) is Verdict.OFF_CUE


def test_findings_items_are_one_shape_whatever_the_payload_nested_them_in():
    payload = {
        "starts": [
            {"key": "01", "verdict": Verdict.OK.to_dict()},
            {"key": "02", "verdict": Verdict.BLACK.to_dict(), "detail": "the first frame is dark"},
        ],
        "cues": [
            {"section": 3, "cue": "3.1bowl", "verdict": Verdict.THIN_CHANGE.to_dict(), "detail": "0.2 percent changed"},
            {"section": 3, "cue": "3.2", "verdict": Verdict.OFF_CUE.to_dict(), "detail": "first change +200 ms"},
        ],
    }
    rows = [Finding.from_dict(row) for row in finding_rows(payload)]
    assert [r.verdict for r in rows] == [Verdict.BLACK, Verdict.OFF_CUE, Verdict.THIN_CHANGE]  # certain rows lead
    assert rows[1] == Finding(detail="first change +200 ms", verdict=Verdict.OFF_CUE, section=3, cue="3.2")
    assert rows[0].section == 2 and rows[2].detail == "0.2 percent changed"


def test_a_lifted_row_takes_its_sentence_from_detail_and_nowhere_else():
    """A producer fills `detail` on every judged row, so a reason code or a note never stands in for it."""
    judged = {"cue": "3.1", "verdict": Verdict.NO_CHANGE.to_dict(), "reason": SkipReason.NO_SLIDE.value, "note": "x"}
    [row] = finding_rows({"cues": [judged]})
    assert row["detail"] is None
    with pytest.raises(ValueError, match="carries no sentence"):
        Finding.from_dict(row)


def test_a_row_that_already_has_the_shape_is_taken_as_it_is():
    """`Finding` is the envelope's row exactly, so a stage that writes one is copied, not rebuilt."""
    row = Finding(detail="no take", verdict=Verdict.MISSING, section=4, cue="4.1", where="script.md")
    payload = {"sections": [{"key": "04", "notes": [row.to_dict(), Finding(detail="fyi").to_dict()]}]}
    rows = finding_rows(payload)
    assert rows == [row.to_dict()]  # the NOTE row judged nothing, so it is not a finding


def test_a_verdict_this_package_wrote_wrong_stops_the_lift():
    """The lift reads each verdict back, so a payload whose code names no verdict is a bug and not a row."""
    wrong = {**Verdict.OFF_CUE.to_dict(), "label": Verdict.OFF_STAGE.label}
    with pytest.raises(ValueError, match="is not what the verdict"):
        finding_rows({"cues": [{"verdict": wrong, "detail": "x"}]})


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
    rows = finding_rows({"cues": [{"verdict": Verdict.THIN_CHANGE.to_dict(), "detail": "thin"}]}, extra)
    assert [Finding.from_dict(r).verdict for r in rows] == [Verdict.MISSING, Verdict.THIN_CHANGE]


def test_a_passing_verdict_is_never_a_finding():
    passing = (Verdict.OK, Verdict.CHANGED, Verdict.SKIPPED, Verdict.NOTE)
    assert finding_rows({"rows": [{"verdict": v.to_dict()} for v in passing]}) == []
