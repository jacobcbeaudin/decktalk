"""The shared vocabulary of judgement: what a verdict is certain about, and what a row reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from decktalk.verdicts import Finding, Findings, SkipReason, StageResult, Verdict


def test_every_verdict_is_certain_uncertain_or_passing():
    """A verdict the tables print has to fall in exactly one of the three groups."""
    for verdict in Verdict:
        groups = [verdict.certain, verdict.passing]
        assert groups.count(True) <= 1, verdict
    uncertain = [v for v in Verdict if not v.certain and not v.passing]
    assert {v.name for v in uncertain} == {"BLACK_UNSURE", "SPOKEN_SYMBOL", "THIN_CHANGE"}


def test_an_uncertain_label_ends_in_a_question_mark_and_a_certain_one_does_not():
    """The label carries the certainty to a reader, and the property carries it to the code."""
    for verdict in Verdict:
        if verdict.passing:
            continue
        assert verdict.value.endswith("?") is not verdict.certain, verdict


def test_the_member_name_is_the_code_and_the_value_is_the_label():
    """A verdict is matched by its code and never by its label, so a rename of a label breaks nobody."""
    assert Verdict.SPEECH_AT_CUT.name == "SPEECH_AT_CUT"
    assert Verdict.SPEECH_AT_CUT.value == "SPEECH AT CUT"
    assert Verdict.SPEECH_AT_CUT.to_dict() == {"code": "SPEECH_AT_CUT", "label": "SPEECH AT CUT", "certain": True}
    assert Verdict.THIN_CHANGE.to_dict() == {"code": "THIN_CHANGE", "label": "THIN CHANGE?", "certain": False}


def test_a_skip_reason_names_itself():
    """`verify` and `preflight` name the shared reasons identically, so the code is the value."""
    for reason in SkipReason:
        assert reason.name == reason.value


def test_a_finding_carries_the_verdict_opened_out():
    """The row a command reports is the row a reader receives, with no second spelling."""
    finding = Finding(
        detail="the picture landed 0.40s after its word",
        verdict=Verdict.OFF_CUE,
        section=3,
        cue="3.2",
        where="build/out/lesson.mp4",
    )
    assert finding.to_dict() == {
        "code": "OFF_CUE",
        "label": "OFF CUE",
        "certain": True,
        "section": 3,
        "cue": "3.2",
        "where": "build/out/lesson.mp4",
        "detail": "the picture landed 0.40s after its word",
    }
    assert finding.text == "3.2: the picture landed 0.40s after its word"
    assert json.loads(json.dumps(finding.to_dict()))["code"] == "OFF_CUE"


def test_an_advisory_note_still_carries_a_code_a_reader_can_dispatch_on():
    """A note prints beside the findings, is tallied with neither of them, and is never a null code."""
    note = Finding(detail="the phrase occurs twice, and the cue uses the first")
    assert note.verdict is Verdict.NOTE and Verdict.NOTE.passing
    assert note.to_dict() == {
        "code": "NOTE",
        "label": "note",
        "certain": False,
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
            return {"rows": [r.name for r in self.rows]}

    @dataclass
    class Half:
        @property
        def findings(self) -> Findings:
            return Findings()

    whole = Whole([Verdict.BLACK, Verdict.OK])
    assert isinstance(whole, StageResult)
    assert whole.findings == Findings(certain=1)
    assert whole.to_dict(Path("/project")) == {"rows": ["BLACK", "OK"]}
    assert not isinstance(Half(), StageResult)
