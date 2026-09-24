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

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from decktalk import page
from decktalk.page import ATTRS, CAPTURE_FPS, EXEMPT, FRAME_STEP_MS, Attr, PageWarning, Subject

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


def generator():
    """`scripts/build_runtime.py` imported by path, because the scripts directory is not a package."""
    spec = importlib.util.spec_from_file_location("build_runtime", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_a_reduced_render_never_scales_a_span_past_the_ceiling():
    """The reduced motion scale multiplies a declared span, so it is clamped by the same one number."""
    assert page.measurable(page.scaled(page.ENTRANCES["draw"].seconds, 4))
    assert page.scaled(0.1, 2) == pytest.approx(0.2)


def test_a_staggers_whole_span_is_its_step_per_earlier_child_plus_one_entrance():
    """The arithmetic is exact, which is why the overrun it can cause is a certain finding."""
    assert page.stagger_span(0.08, 4, 0.32) == pytest.approx(0.56)
    assert page.stagger_span(0.08, 0, 0.32) == 0
    assert not page.measurable(page.stagger_span(0.08, 4, 0.32))


def test_a_moment_qualifies_into_the_id_the_project_file_carries():
    """The page owns a name local to its slide, and the wire id is what `cues.json` is keyed by."""
    assert page.wire_id("4.1", "expand") == "4.1:expand"
    assert page.MOMENTS == (Attr.IN, Attr.BACK, Attr.FRONT, Attr.OUT)


def test_the_table_holds_the_rows_the_design_froze():
    """Eighteen rows on an element or a container, five on a slide and two on a scene."""
    for subjects, count in ROWS_PER_SUBJECT.items():
        rows = [name for name, row in ATTRS.items() if set(subjects) & set(row.on)]
        assert len(rows) == count, f"{subjects} has {len(rows)} rows and the design froze {count}"
    only_containers = {name for name, row in ATTRS.items() if row.on == (Subject.CONTAINER,)}
    assert only_containers == {Attr.STAGGER, Attr.STEPS}


def test_no_code_spells_its_own_certainty_and_every_one_is_a_sentence():
    """A reader dispatches on the code and reads the certainty beside it, never out of the word."""
    for code in PageWarning:
        assert code.message.endswith("."), f"{code.name} does not print a whole sentence"
        assert "?" not in code.message
        assert "certain" not in code.name.lower()
    assert PageWarning.PAGE_STAGGER_OVERRUN.certain, "the stagger arithmetic is exact, so its finding is certain"
    assert not PageWarning.PAGE_SWAP_APART.certain
    assert not PageWarning.PAGE_THIN_DRAW.certain


# ---- the generation ------------------------------------------------------------------------


def test_the_page_module_is_what_the_committed_contract_says():
    """Everything downstream of `contract.json` is pure Python, so this half runs on every platform."""
    data = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    assert generator().page_module(data) == PAGE_MODULE.read_text(encoding="utf-8")


@pytest.mark.skipif(not ESBUILD.exists(), reason="the pinned node tools are not installed, so run `npm ci`")
def test_the_committed_contract_is_what_the_typescript_says():
    """The one generator, run in the mode that changes nothing, over every artifact it owns."""
    done = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_page_codes_are_the_same_list_the_finding_codes_carry():
    """One `Code` enum is written by hand, and this is the check that keeps its page half honest."""
    findings = pytest.importorskip("decktalk.findings", reason="the finding codes land with the core models")
    written = {name for name in findings.Code.__members__ if name.startswith("PAGE_")}
    assert written == set(PageWarning.__members__)
