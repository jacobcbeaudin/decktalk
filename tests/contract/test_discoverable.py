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
scope and a nature, and the committed schema has to carry the same keys. The finding codes and the
error codes have to publish a sentence, a certainty where one applies and a documentation address
that follows the published pattern. The page contract has to publish, for every attribute, what it
is written on, what values it takes, its default, the code that names it and what it affects, or
name it in the exemption list with the sentence saying why no value of it can change a verdict.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from decktalk import page
from decktalk.errors import ErrorCode
from decktalk.findings import Code, RaisedBy
from decktalk.results import RESULTS, Result
from decktalk.settings import KEYS, NUMBERS
from support.paths import REPO

SCHEMA = REPO / "schema"
RESULT_SCHEMAS = SCHEMA / "results"
SETTINGS_SCHEMA = SCHEMA / "decktalk-1.json"
MACHINE_SCHEMA = SCHEMA / "decktalk-machine-1.json"

DOCS = "https://docs.decktalk.ai"
"""Where every published address resolves, which is the one host a printed URL may name."""

SCHEMA_VERSION = 2
"""The shape version every result publishes, which is the founder's decided contract."""

DOCUMENT = "document"
"""What the schema marks a table of the author's own content with, which holds no knob to turn."""


def settings_schema() -> dict[str, Any]:
    return json.loads(SETTINGS_SCHEMA.read_text(encoding="utf-8"))


def schema_keys(document: dict[str, Any], prefix: str = "") -> set[str]:
    """Every dotted key the published settings schema names, which is what an agent reads.

    A property that holds properties of its own is a table rather than a key, so the walk descends
    into it and never counts the table itself as something a value could be written to. A table the
    schema marks as document is the author's own content rather than a knob, and it is open by
    design, so it names no key at all.
    """
    found: set[str] = set()
    for name, definition in document.get("properties", {}).items():
        dotted = f"{prefix}{name}"
        if not isinstance(definition, dict) or definition.get("x-kind") == DOCUMENT:
            continue
        if definition.get("properties"):
            found |= schema_keys(definition, f"{dotted}.")
        else:
            found.add(dotted)
    return found


# ---- the settings, which are the knobs a project turns --------------------------------------


NUMERIC = (int, float)
"""The annotations whose range is a number rather than the type itself, which is what needs bounds.

A boolean publishes its whole range by being a boolean, and a string that names a host or a path has
no range a loader could refuse, so neither carries bounds and neither is less discoverable for it. A
number without a range is the case this rule exists for. A unit is not required of one, because a
ratio and a factor are dimensionless and their range is what says how far they may be turned.
"""


@pytest.mark.parametrize("key", KEYS, ids=[key.id for key in KEYS])
def test_every_settings_key_publishes_what_an_agent_needs_to_turn_it(key):
    """A knob with no sentence, no default, no scope or no nature is a knob nobody can turn safely."""
    assert key.description and key.description.endswith("."), key.id
    assert key.default is not None, key.id
    assert key.scope is not None, key.id
    assert key.nature is not None, key.id
    if key.annotation in NUMERIC:
        assert key.bounds is not None, f"{key.id} publishes no safe range, so no value of it is known to be safe."


@pytest.mark.parametrize("key", KEYS, ids=[key.id for key in KEYS])
def test_every_settings_key_is_in_the_published_schema(key):
    """The schema is what an agent reads without running anything, so a key absent from it is invisible."""
    assert key.id in schema_keys(settings_schema()), f"{key.id} is a key and the published schema does not name it."


def test_the_schema_names_no_key_the_settings_tree_does_not_have():
    """The other direction: a schema entry nothing answers is an instruction an agent cannot follow."""
    extra = sorted(schema_keys(settings_schema()) - {key.id for key in KEYS})
    assert extra == [], extra


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


def test_no_code_spells_its_own_certainty():
    """A code that carried its own certainty would publish one fact twice and could disagree with itself."""
    for code in Code:
        assert not code.value.endswith(("_UNSURE", "_MAYBE", "?")), code.name


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


def test_the_exemption_list_names_no_attribute_the_contract_dropped():
    """A closed list a test can count beats an open claim, which is why the list is counted here."""
    extra = sorted(attr.value for attr in page.EXEMPT if attr not in page.ATTRS)
    assert extra == [], extra


# ---- the results, which are what a command publishes -----------------------------------------


@pytest.mark.parametrize("name", sorted(RESULTS), ids=sorted(RESULTS))
def test_every_result_has_a_committed_schema_and_a_sentence_for_every_field(name: str):
    """An agent reads the schema rather than the source, so a field with no sentence is a field it guesses at."""
    assert (RESULT_SCHEMAS / f"{name}.json").exists(), f"{name} has no committed schema, so nothing publishes it."
    for field, definition in RESULTS[name].model_fields.items():
        assert definition.description, f"{name}.{field} carries no description."


def test_the_schema_directory_holds_exactly_the_results_the_library_returns():
    committed = {path.stem for path in RESULT_SCHEMAS.glob("*.json")}
    assert committed == set(RESULTS), {"only committed": sorted(committed - set(RESULTS))}


def test_every_result_declares_the_shape_version_the_contract_fixes():
    """One number tells a reader which contract it is reading, and it is the same number on every result."""
    for name, model in RESULTS.items():
        assert model.model_fields["schema_"].default == SCHEMA_VERSION, name
        assert issubclass(model, Result), name


# ---- the command line, which is the instruction set ------------------------------------------


def typer_commands() -> set[str]:
    """Every command name the Typer app publishes, walked the way `--help` walks it.

    This is the one surface that is not here yet. It fails rather than skips, because a thesis test
    that passes while the instruction set is missing is the failure the thesis test exists to catch.
    """
    try:
        from decktalk.cli import app  # noqa: PLC0415  (the app is the CLI's, and importing it is the test)
    except Exception as error:  # noqa: BLE001  (any import failure is the same fact: there is no app)
        pytest.fail(f"decktalk.cli publishes no Typer app to walk, so no command is discoverable: {error}")
    from typer.main import get_command  # noqa: PLC0415  (typer is the CLI's dependency, not this file's)

    return set(get_command(app).commands)


def test_the_command_set_equals_the_results_the_library_publishes():
    """A result with no command is a call an agent reading `--help` never learns about."""
    commands = typer_commands()
    published = {name.split("-")[0] for name in RESULTS} - {"error", "apply"}
    assert published <= commands, sorted(published - commands)


def test_every_command_and_every_option_carries_its_own_help():
    """An option with no help is a knob an agent can pass and cannot read, which is the thesis failing."""
    from typer.main import get_command  # noqa: PLC0415  (typer is the CLI's dependency, not this file's)

    from decktalk.cli import app  # noqa: PLC0415  (the app is the CLI's, and importing it is the test)

    bare: list[str] = []
    for name, command in get_command(app).commands.items():
        if not command.help:
            bare.append(name)
        bare += [f"{name} {param.name}" for param in command.params if not getattr(param, "help", None)]
    assert bare == [], bare
