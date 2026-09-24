"""The envelope as types: what a caller reads back, and what the reader refuses."""

from __future__ import annotations

import json

import pytest

from decktalk.cli import main
from decktalk.cli.envelope import envelope_of, usage_error
from decktalk.cli.parser import BY_NAME
from decktalk.cli.schema import (
    ENVELOPE_KEYS,
    PAYLOADS,
    BuildPayload,
    BuildPlanPayload,
    Envelope,
    ServePayload,
    read_envelope,
)
from decktalk.errors import ErrorCode
from decktalk.jsonio import ShapeError
from decktalk.pipeline import Stage
from decktalk.verdicts import Finding, Findings, Verdict


def test_every_command_has_a_payload_type_and_nothing_else_does():
    """One word names the command and the key its payload sits under, so the two tables are one list."""
    assert sorted(PAYLOADS) == sorted(BY_NAME)


def test_an_envelope_reads_back_into_its_types_with_every_code_a_member():
    row = Finding(detail="the page threw", verdict=Verdict.PAGE_ERROR, section=2, where="deck/index.html")
    doc = envelope_of(
        "serve", "0.4.0", ok=False, exit_code=1, findings=Findings(certain=1), items=[row.to_dict()],
        payload={"urls": ["http://127.0.0.1:8123/"]},
    )  # fmt: skip
    envelope = Envelope.from_dict(json.loads(json.dumps(doc)))
    assert envelope.findings.items == [row] and envelope.findings.items[0].verdict is Verdict.PAGE_ERROR
    assert envelope.payload == ServePayload(urls=["http://127.0.0.1:8123/"]) and envelope.error is None


def test_a_build_payload_is_the_run_or_under_a_dry_run_the_plan():
    stages = [Stage.RECORD.value, Stage.ASSEMBLE.value]
    plan = envelope_of("build", "0.4.0", ok=True, exit_code=0, payload={"stages": stages, "missing": []})
    assert Envelope.from_dict(plan).payload == BuildPlanPayload(stages=[Stage.RECORD, Stage.ASSEMBLE], missing=[])
    run = {stage.value: None for stage in Stage} | {"stages": stages, "progress": "build/progress.jsonl"}
    read = Envelope.from_dict(envelope_of("build", "0.4.0", ok=True, exit_code=0, payload=run)).payload
    assert isinstance(read, BuildPayload) and read.stages == [Stage.RECORD, Stage.ASSEMBLE] and read.record is None


def test_an_error_envelope_carries_no_payload_and_its_code_reads_as_a_member(capsys):
    assert main(["--nope", "--json"]) == 2
    refused = read_envelope(capsys.readouterr().out)
    assert refused.error is not None and refused.error.code is ErrorCode.USAGE and refused.payload is None


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (lambda d: d.pop("written"), r"carries .* and an envelope carries"),
        (lambda d: d.update(extra=1), r"carries .* and an envelope carries"),
        (lambda d: d.update(error={**usage_error("x"), "code": "NOPE"}), r"\$\.error\.code is 'NOPE'"),
        (lambda d: d["findings"]["items"].append({"code": Verdict.OFF_CUE.name}), r"\$\.findings\.items\[0\]"),
        (lambda d: d.update(serve={"urls": "one"}), r"\$\.serve\.urls is str"),
    ],
)
def test_an_envelope_that_is_not_the_shape_the_contract_fixes_is_refused(change, refusal):
    doc = envelope_of("serve", "0.4.0", ok=True, exit_code=0, payload={"urls": []})
    change(doc)
    with pytest.raises(ShapeError, match=refusal):
        Envelope.from_dict(doc)


def test_the_envelope_keys_are_the_heads_fields_in_the_order_they_are_printed():
    doc = envelope_of("serve", "0.4.0", ok=True, exit_code=0, payload={"urls": []})
    assert list(doc) == [*ENVELOPE_KEYS, "serve"]


def test_a_payload_is_there_exactly_when_no_error_is():
    """A successful command always prints its payload, and a command that raised prints none."""
    ran = envelope_of("serve", "0.4.0", ok=True, exit_code=0, payload=None)
    with pytest.raises(ShapeError, match=r"\$\.serve is null"):
        Envelope.from_dict(ran)
    failed = envelope_of("serve", "0.4.0", ok=False, exit_code=3, error=usage_error("x"), payload={"urls": []})
    with pytest.raises(ShapeError, match="a payload beside an error"):
        Envelope.from_dict(failed)
