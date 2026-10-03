"""The founder's thesis, made mechanical: nothing is a knob unless an agent can find it and read it.

    "I give a CLI, a very structured set of instructions that can be told through the
    self-documenting code, and then an agent can go look at it, look at my Midjourney knobs, and
    figure out how to implement this."

So the tool is an instruction set and the settings and the page attributes are its parameters, each
named, documented, ranged and defaulted. A surface an agent cannot discover, read and act on from
`--help` and the published schemas alone is a surface that fails the thesis, and this file is the
one place that sentence is a test rather than an intention.

Four surfaces are walked and each is total in both directions. The command line has to publish a
command for every result the library returns and help for every command and every option it takes.
The settings tree has to publish, for every key, a sentence, a default, a safe range, a unit, a
scope and a nature. `tests/decktalk/test_settings.py` holds that record, the generator check
`build_settings_schema.py --check` holds the committed schema to the same keys, and this file holds
only the range rule neither of them does. The finding codes and the error codes have to publish a
sentence, a certainty where one applies and a documentation address that follows the published
pattern. The page contract has to publish, for every attribute, what it
is written on, what values it takes, its default, the code that names it and what it affects, or
name it in the exemption list with the sentence saying why no value of it can change a verdict.
"""

from __future__ import annotations

import pytest

from decktalk import page
from decktalk.cli import catalog
from decktalk.errors import ErrorCode
from decktalk.findings import Code, RaisedBy
from decktalk.results import RESULTS, Result
from decktalk.settings import KEYS, NUMBERS

DOCS = "https://docs.decktalk.ai"
"""Where every published address resolves, which is the one host a printed URL may name."""

SCHEMA_VERSION = 1
"""The shape version every result publishes, which is the founder's decided contract."""


# ---- the settings, which are the knobs a project turns --------------------------------------


NUMERIC = (int, float)
"""The annotations whose range is a number rather than the type itself, which is what needs bounds.

A boolean publishes its whole range by being a boolean, and a string that names a host or a path has
no range a loader could refuse, so neither carries bounds and neither is less discoverable for it. A
number without a range is the case this rule exists for. A unit is not required of one, because a
ratio and a factor are dimensionless and their range is what says how far they may be turned.
"""


def test_every_numeric_settings_key_publishes_a_safe_range():
    """A number with no range is a knob an agent cannot turn safely, because nothing says how far is too far.

    The rest of each key's record, its sentence, default, scope and nature, is held once in
    `tests/decktalk/test_settings.py`, and the published schema is held to the keys by
    `build_settings_schema.py --check`. This is the one rule of the record that neither holds.
    """
    unranged = sorted(key.id for key in KEYS if key.annotation in NUMERIC and key.bounds is None)
    assert unranged == [], f"these numeric keys publish no safe range: {unranged}"


@pytest.mark.parametrize("number", NUMBERS, ids=[number.id for number in NUMBERS])
def test_every_published_number_says_why_it_is_not_a_knob(number):
    """A number that is deliberately not a knob publishes its formula, so a reader stops looking for one."""
    assert number.sentence and number.sentence.endswith("."), number.id
    assert number.nature is not None, number.id
    assert number.formula, f"{number.id} publishes no formula, so nothing says what it is computed from."


# ---- the codes, which are what an agent dispatches on ---------------------------------------


@pytest.mark.parametrize("code", list(Code), ids=[code.name for code in Code])
def test_every_finding_code_publishes_a_sentence_a_certainty_and_an_address(code: Code):
    """A code is the token an agent dispatches on, so each one answers what it means and where to read."""
    assert code.sentence and code.sentence.endswith("."), code.name
    assert code.certainty is not None, code.name
    assert code.raised_by in tuple(RaisedBy), code.name
    assert code.url == f"{DOCS}/reference/findings/{code.value}", code.name


@pytest.mark.parametrize("code", list(ErrorCode), ids=[code.name for code in ErrorCode])
def test_every_error_code_publishes_a_sentence_an_exit_code_and_an_address(code: ErrorCode):
    """A refusal an agent cannot read is a refusal it retries, so every code says what it is and what it exits."""
    assert code.sentence and code.sentence.endswith("."), code.name
    assert code.exit_code > 0, code.name
    assert code.url == f"{DOCS}/reference/errors/{code.value}", code.name


# ---- the page attributes, which are the knobs an author writes in the markup -----------------


@pytest.mark.parametrize("attr", list(page.ATTRS), ids=[attr.value for attr in page.ATTRS])
def test_every_page_attribute_publishes_what_it_is_and_what_it_affects(attr):
    """The page is the second knob surface, so every attribute reads like a parameter with a range."""
    row = page.ATTRS[attr]
    assert row.summary and row.summary.endswith("."), attr.value
    assert row.on, attr.value
    assert row.kind is not None, attr.value
    assert row.affects, f"{attr.value} affects nothing it publishes, so nothing tells an author what it does."


@pytest.mark.parametrize("attr", list(page.ATTRS), ids=[attr.value for attr in page.ATTRS])
def test_every_page_attribute_carries_a_code_or_is_exempt_with_its_sentence(attr):
    """An attribute whose value can change a verdict has a code, and one that cannot says why it has none."""
    row = page.ATTRS[attr]
    if row.code is None:
        assert attr in page.EXEMPT, f"{attr.value} names no code and is not in the exemption list."
        assert page.EXEMPT[attr].endswith("."), attr.value
    else:
        assert attr not in page.EXEMPT, f"{attr.value} names a code and is excused from naming one."
        assert row.code.name in Code.__members__, attr.value


# ---- the results, which are what a command publishes -----------------------------------------


def test_every_result_declares_the_shape_version_the_contract_fixes():
    """One number tells a reader which contract it is reading, and it is the same number on every result."""
    for name, model in RESULTS.items():
        assert model.model_fields["schema_"].default == SCHEMA_VERSION, name
        assert issubclass(model, Result), name


# ---- the command line, which is the instruction set ------------------------------------------


def test_every_command_and_every_option_carries_its_own_help():
    """An option with no help is a knob an agent can pass and cannot read, which is the thesis failing.

    The catalog walks a group's subcommands too, so the options of `config set` are held as well.
    """
    bare: list[str] = []
    for row in catalog.walk():
        if not row["purpose"]:
            bare.append(row["command"])
        bare += [f"{row['command']} {param['opts'][0]}" for param in row["params"] if not param["help"]]
    assert bare == [], bare
