"""The page contract on the Python side: the vocabulary, the rules it rests on, and its generation.

`src/decktalk/page.py` is written by `scripts/build_runtime.py` from `contract.json`, which node
prints from `src/decktalk/runtime/src/contract.ts`. Two halves of that chain are checked here. The
half below `contract.json` is pure Python and runs everywhere, and the half above it needs the pinned
node tools, so it is skipped until `npm ci` has run.

The rules themselves are asserted against the generated module rather than against the TypeScript,
because a rule the contract keeps and the generator loses is exactly the failure this file is for.
`tests/decktalk/runtime/src/contract.test.ts` asserts the same rules at the source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import build_runtime
from decktalk import page
from decktalk.findings import CONTRACT_SUBJECTS, Code, Severity
from decktalk.page import ATTRS, CAPTURE_FPS, EXEMPT, FRAME_STEP_MS, Attr, Subject

ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT_JSON = ROOT / "src" / "decktalk" / "runtime" / "contract.json"
PAGE_MODULE = ROOT / "src" / "decktalk" / "page.py"
GENERATOR = ROOT / "scripts" / "build_runtime.py"
ESBUILD = ROOT / "node_modules" / ".bin" / "esbuild"

# How many rows the design froze for each subject a row is written on, which is the count a reviewer
# checks against the attribute table in the synthesis. An element and a container are one subject
# here, because both are rows an author writes many times a deck.
ROWS_PER_SUBJECT: dict[tuple[Subject, ...], int] = {
    (Subject.ELEMENT, Subject.CONTAINER): 18,
    (Subject.SLIDE,): 5,
    (Subject.SCENE,): 2,
}


# ---- the rules the table rests on ----------------------------------------------------------


def test_every_attribute_carries_a_code_or_is_named_in_the_closed_exemption_list():
    """An attribute survives when its value can change a verdict, and the rows that cannot are listed."""
    coded = {name for name, row in ATTRS.items() if row.code is not None}
    assert coded | set(EXEMPT) == set(ATTRS)
    assert coded & set(EXEMPT) == set()


def test_the_exemption_list_is_five_rows_and_each_says_why():
    """A closed list a test can count beats an open field claiming what a row affects."""
    assert len(EXEMPT) == 5
    assert set(EXEMPT) == {Attr.DESCRIBE, Attr.DESCRIBE_CLASS, Attr.DESCRIBE_OUT, Attr.HOLD, Attr.NAME}
    for name, why in EXEMPT.items():
        assert why.endswith("."), f"{name} is exempt without a whole sentence saying why"
        assert ATTRS[name].span == 0


def test_no_span_an_author_can_declare_reaches_the_ceiling():
    """A page range admitting a value that marks its own cue unmeasurable would delete a check."""
    for name, row in ATTRS.items():
        if row.span is not None:
            assert page.measurable(row.span), f"{name} declares a span at or above the ceiling"
        if row.range is not None and name is not Attr.HOLD:
            assert page.measurable(row.range.max), f"{name} publishes a range whose top is unmeasurable"
    for effect in page.ENTRANCES.values():
        assert page.measurable(effect.seconds)
    for effect in page.EXITS.values():
        assert page.measurable(effect.seconds)


def test_the_frame_step_is_the_capture_rate_written_the_other_way_round():
    """Every range on the page is written in whole frames, so the two numbers may never drift."""
    assert 1000 / CAPTURE_FPS == FRAME_STEP_MS


def test_a_staggers_whole_span_is_its_step_per_earlier_child_plus_one_entrance():
    """The arithmetic is exact, which is why the overrun it can cause is an error."""
    assert page.stagger_span(0.08, 4, 0.32) == pytest.approx(0.56)
    assert page.stagger_span(0.08, 0, 0.32) == 0
    assert not page.measurable(page.stagger_span(0.08, 4, 0.32))


def test_an_element_has_four_moments_in_the_order_it_meets_them():
    """It arrives, steps back, comes to the front and leaves, and each of those names a cue."""
    assert page.MOMENTS == (Attr.IN, Attr.BACK, Attr.FRONT, Attr.OUT)


def test_the_table_holds_the_rows_the_design_froze():
    """Eighteen rows on an element or a container, five on a slide and two on a scene."""
    for subjects, count in ROWS_PER_SUBJECT.items():
        rows = [name for name, row in ATTRS.items() if set(subjects) & set(row.on)]
        assert len(rows) == count, f"{subjects} has {len(rows)} rows and the design froze {count}"
    only_containers = {name for name, row in ATTRS.items() if row.on == (Subject.CONTAINER,)}
    assert only_containers == {Attr.STAGGER, Attr.SPOTLIGHT}


def test_no_code_spells_its_own_severity_and_every_one_is_a_sentence():
    """A reader dispatches on the code and reads the severity beside it, never out of the word."""
    for name, row in contract_codes().items():
        assert row["message"].endswith("."), f"{name} does not print a whole sentence"
        assert "?" not in row["message"]
        assert "warning" not in name.lower() and "certain" not in name.lower()
    assert Code.PAGE_STAGGER_OVERRUN.severity is Severity.ERROR, "the stagger arithmetic is exact"
    assert Code.PAGE_SWAP_APART.severity is Severity.WARNING
    assert Code.PAGE_THIN_DRAW.severity is Severity.WARNING


def test_an_attribute_publishes_the_name_of_the_code_that_judges_it():
    """The schema carries the code an agent dispatches on, never the sentence the page prints."""
    assert ATTRS[Attr.IN].model_dump(mode="json")["code"] == "PAGE_MOMENT_UNKNOWN"


# ---- the generation ------------------------------------------------------------------------


def test_the_page_module_is_what_the_committed_contract_says():
    """Everything downstream of `contract.json` is pure Python, so this half runs on every platform."""
    data = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    assert build_runtime.page_module(data) == PAGE_MODULE.read_text(encoding="utf-8")


@pytest.mark.skipif(not ESBUILD.exists(), reason="the pinned node tools are not installed, so run `npm ci`")
def test_the_committed_contract_is_what_the_typescript_says():
    """The one generator, run in the mode that changes nothing, over every artifact it owns."""
    done = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def contract_codes() -> dict[str, dict[str, str]]:
    """Every page code the TypeScript contract publishes, with its sentence, severity and side."""
    return json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))["codes"]


def test_the_page_codes_are_the_same_list_the_finding_codes_carry():
    """One `Code` enum is written by hand, and this is the check that keeps its page half honest."""
    written = {code.name for code in Code if code.subject in CONTRACT_SUBJECTS}
    assert written == set(contract_codes())


def test_the_page_codes_carry_the_severity_and_the_side_the_finding_codes_carry():
    """A result serialises what `findings.py` holds and the console prints what the page holds, so a
    reader who saw both would otherwise be told two different things about the same code."""
    for name, row in contract_codes().items():
        assert row["severity"] == Code[name].severity.value, f"{name} has a different severity in each registry"
        assert row["raisedBy"] == Code[name].raised_by.value, f"{name} is raised by two sides"


CONTRACT_CASES = ROOT / "tests" / "data" / "contract_cases.json"
"""The table of cases node runs against the TypeScript, and this runs against every function Python also computes."""


def contract_cases() -> list[tuple[str, list[object], object]]:
    """Every row of the shared case table whose function the generated Python publishes, named by that function."""
    table = json.loads(CONTRACT_CASES.read_text(encoding="utf-8"))
    named = [(name, rows) for name, rows in table.items() if isinstance(rows, list) and name in page.__all__]
    return [(name, row["args"], row["want"]) for name, rows in named for row in rows]


@pytest.mark.parametrize(("name", "args", "want"), contract_cases())
def test_the_contract_cases_hold(name: str, args: list[object], want: object) -> None:
    """The generated Python answers every case the TypeScript answers, so the two cannot drift apart."""
    tolerance = json.loads(CONTRACT_CASES.read_text(encoding="utf-8"))["tolerance"]
    got = getattr(page, name)(*args)
    if isinstance(want, bool | str):
        assert got == want
    else:
        assert got == pytest.approx(want, abs=tolerance)
