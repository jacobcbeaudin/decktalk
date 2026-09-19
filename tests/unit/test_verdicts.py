"""The shared vocabulary of judgement: what a verdict is certain about, and what a row reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from decktalk.verdicts import Certainty, Finding, Findings, SkipReason, StageResult, Verdict

WIRE = json.loads((Path(__file__).resolve().parents[1] / "data" / "vocabulary.json").read_text(encoding="utf-8"))
"""The codes, labels and certainties the contract fixes, as a reader of the JSON meets them."""


def test_every_verdict_writes_the_object_the_contract_fixes_in_the_order_it_lists_them():
    """The wire form is the contract, so a code, a label or a certainty that moves fails here."""
    assert [verdict.to_dict() for verdict in Verdict] == WIRE["verdicts"]
    assert [verdict.name for verdict in Verdict if verdict.passing] == WIRE["passing"]


def test_every_verdict_reads_back_from_its_own_object():
    for written in WIRE["verdicts"]:
        verdict = Verdict.from_dict(written)
        assert (verdict.name, verdict.label, verdict.certain) == (written["code"], written["label"], written["certain"])


def test_a_raw_string_never_compares_equal_to_a_verdict():
    """A plain enum, so code that holds a code or a label where it meant a verdict fails where it is written."""
    for verdict in Verdict:
        written = verdict.to_dict()
        assert verdict != written["code"] and verdict != written["label"] and verdict != written


def test_every_verdict_is_certain_uncertain_or_passing():
    """A verdict the tables print has to fall in exactly one of the three groups."""
    for verdict in Verdict:
        assert [verdict.certain, verdict.passing].count(True) <= 1, verdict
        assert verdict.certain is (verdict.certainty is Certainty.CERTAIN)
        assert verdict.passing is (verdict.certainty is Certainty.PASSING)


def test_an_uncertain_label_ends_in_a_question_mark_and_a_certain_one_does_not():
    """The label carries the certainty to a reader, and the property carries it to the code."""
    for verdict in Verdict:
        if not verdict.passing:
            assert verdict.label.endswith("?") is not verdict.certain, verdict


@pytest.mark.parametrize(
    ("written", "refusal"),
    [
        ({"code": "NOPE", "label": "NOPE", "certain": True}, "is not a verdict code"),
        ({**Verdict.OFF_CUE.to_dict(), "label": Verdict.OFF_STAGE.label}, "is not what the verdict"),
        ({**Verdict.OFF_CUE.to_dict(), "certain": False}, "is not what the verdict"),
        ({"code": Verdict.OFF_CUE.name, "label": Verdict.OFF_CUE.label}, "exactly code, label, certain"),
        ({**Verdict.OFF_CUE.to_dict(), "detail": "x"}, "exactly code, label, certain"),
        (Verdict.OFF_CUE.name, "exactly code, label, certain"),
    ],
)
def test_an_object_that_is_not_a_verdicts_own_is_refused(written, refusal):
    """A reader that took a wrong object for a verdict would dispatch on something no writer meant."""
    with pytest.raises(ValueError, match=refusal):
        Verdict.from_dict(written)


def test_a_skip_reason_is_its_code_and_is_no_string():
    """`verify` and `preflight` name the shared reasons alike, so the value is the code."""
    assert [reason.value for reason in SkipReason] == WIRE["skip_reasons"]
    for reason in SkipReason:
        assert reason.name == reason.value and reason != reason.value


def test_a_finding_carries_the_verdict_opened_out_and_reads_back():
    """The row a command reports is the row a reader receives, with no second spelling."""
    finding = Finding(
        detail="the picture landed 0.40s after its word",
        verdict=Verdict.OFF_CUE,
        section=3,
        cue="3.2",
        where="build/out/lesson.mp4",
    )
    assert finding.to_dict() == {
        **Verdict.OFF_CUE.to_dict(),
        "section": 3,
        "cue": "3.2",
        "where": "build/out/lesson.mp4",
        "detail": "the picture landed 0.40s after its word",
    }
    assert finding.text == "3.2: the picture landed 0.40s after its word"
    assert Finding.from_dict(json.loads(json.dumps(finding.to_dict()))) == finding


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"detail": None}, "carries no sentence"),
        ({"section": "3"}, "not a section number"),
        ({"section": True}, "not a section number"),
        ({"where": 3}, "not a string"),
        ({"extra": 1}, "exactly code"),
    ],
)
def test_a_row_that_is_not_the_envelopes_row_is_refused(change, refusal):
    row = {**Finding(detail="d", verdict=Verdict.BLACK, section=3).to_dict(), **change}
    with pytest.raises(ValueError, match=refusal):
        Finding.from_dict(row)


def test_an_advisory_note_still_carries_a_code_a_reader_can_dispatch_on():
    """A note prints beside the findings, is tallied with neither of them, and is never a null code."""
    note = Finding(detail="the phrase occurs twice, and the cue uses the first")
    assert note.verdict is Verdict.NOTE and Verdict.NOTE.passing
    assert note.to_dict() == {
        **Verdict.NOTE.to_dict(),
        "section": None,
        "cue": None,
        "where": None,
        "detail": "the phrase occurs twice, and the cue uses the first",
    }
    assert note.text == "the phrase occurs twice, and the cue uses the first"
    assert Findings.of([note.verdict]) == Findings()


def test_no_row_a_reader_receives_carries_a_null_code_or_a_null_label():
    """The envelope types `code` and `label` as strings, so every row has both, whatever it judged."""
    for verdict in Verdict:
        row = Finding(detail="a sentence", verdict=verdict).to_dict()
        assert isinstance(row["code"], str) and isinstance(row["label"], str), verdict
        assert isinstance(row["certain"], bool)


def test_findings_count_the_two_kinds_and_add():
    """`build` adds the counts of five stages without holding any row."""
    assert Findings(1, 2) + Findings(3, 4) == Findings(4, 6)
    assert Findings() + Findings() == Findings(0, 0)
    assert Findings(1, 2).to_dict() == {"certain": 1, "uncertain": 2}


def test_a_passing_verdict_and_a_missing_one_are_counted_as_neither():
    """Only a certain or an uncertain verdict is a finding, which is what the exit code reads."""
    tally = Findings.of([Verdict.OK, Verdict.CHANGED, None, Verdict.BLACK, Verdict.THIN_CHANGE, Verdict.STALLED])
    assert tally == Findings(certain=2, uncertain=1)
    assert Findings.of([]) == Findings(0, 0)


def test_the_protocol_accepts_a_result_and_refuses_one_that_is_missing_a_half():
    """`StageResult` is runtime checkable, so a result that drops either half is caught."""

    @dataclass
    class Whole:
        rows: list[Verdict]

        @property
        def findings(self) -> Findings:
            return Findings.of(self.rows)

        def to_dict(self, root: Path) -> dict[str, object]:
            return {"rows": [r.to_dict() for r in self.rows]}

    @dataclass
    class Half:
        @property
        def findings(self) -> Findings:
            return Findings()

    whole = Whole([Verdict.BLACK, Verdict.OK])
    assert isinstance(whole, StageResult)
    assert whole.findings == Findings(certain=1)
    assert [Verdict.from_dict(row) for row in whole.to_dict(Path("/project"))["rows"]] == whole.rows
    assert not isinstance(Half(), StageResult)
