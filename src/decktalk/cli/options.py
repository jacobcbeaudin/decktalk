"""The flags, declared once, and the rule that decides which command carries which family.

A command's own parameters are written in its own signature. The flags several commands share are
not copied into each of them: they are derived from the command's return annotation, which is the
result model, so a command that reports findings takes `--fail-on` and `--allow` by being the sort
of command that reports findings, and a command that can spend takes `--no-voice`, `--spend` and
`--max-cost` by being the sort of command that spends.

Each family is one flag over a vocabulary the product already publishes, rather than a flag per
value. `--allow` takes finding codes, `--skip` takes stages, `--set` takes settings keys, and
`--section` takes section numbers, so an agent that has read `decktalk schema` can already write
every one of them and a new code, stage or knob costs no help row at all.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer

from decktalk.errors import InputError
from decktalk.findings import Certainty, Code
from decktalk.pipeline import Stage
from decktalk.project import section_numbers
from decktalk.results import (
    AssembleResult,
    BuildResult,
    CheckResult,
    CueResult,
    DoctorResult,
    NarrateResult,
    RecordResult,
    Result,
    SoundscapeResult,
    StoryboardResult,
    VerifyResult,
)

DOCS = "https://docs.decktalk.ai/reference/cli"
"""Where the generated reference page lives, which every command's help closes with."""


class Group(Enum):
    """The five headings the command tree is read under, in the order a reader meets them."""

    MACHINE = "Set up this machine"
    PROJECT = "Read the project, spending nothing"
    CONTRACTS = "Read the knobs and the contracts"
    STAGE = "Run one stage, in this order"
    WHOLE = "Run them all, or cut one piece out"


class Panel(Enum):
    """The headings one command's own options are read under, in the order a reader meets them."""

    SPENDING = "Spending"
    SCOPE = "Scope"
    FINDINGS = "Findings"
    REDOING = "Redoing work"
    THIS_RUN = "This run only"
    WRITING = "Writing"


class FailOn(Enum):
    """The threshold a run fails on, which names a threshold and never the value of a field.

    The lowest threshold is spelled from the certainty it is the threshold for, because a second
    spelling of that word would be a second vocabulary for one idea.
    """

    CERTAIN = Certainty.CERTAIN.value
    ANY = "any"
    NEVER = "never"


class When(Enum):
    """When colour is written, which is the one thing `--color` decides."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class Where(Enum):
    """Which settings file a `config` write lands in."""

    PROJECT = "project"
    MACHINE = "machine"


JUDGES: frozenset[type[Result]] = frozenset(
    {
        AssembleResult,
        BuildResult,
        CheckResult,
        CueResult,
        DoctorResult,
        NarrateResult,
        RecordResult,
        SoundscapeResult,
        StoryboardResult,
        VerifyResult,
    }
)
"""Every result whose command can report a judgement, which is what `--fail-on` and `--allow` act on.

`StatusResult` is not here on purpose: `status` reports what is on disk and judges nothing, so it
would be a third judge beside `check` and `verify` if it could fail on a finding.
"""

SPENDS: frozenset[type[Result]] = frozenset({BuildResult, NarrateResult, SoundscapeResult})
"""Every result whose command can buy something, which is what the three spending flags act on.

`CheckResult` carries a `spend` and is not here, because pricing a run is not buying one.
"""

# The shared families, each written once and derived onto the commands that carry it. A hidden
# global is on every command so that it works after the command name as well as before it, and the
# command's own help closes by naming them in one line instead of spending eight rows on them.
Project = Annotated[
    Path | None,
    typer.Option(
        "-p",
        "--project",
        metavar="DIR",
        hidden=True,
        help="The project directory. Default: DECKTALK_PROJECT, else the current directory.",
    ),
]
Json = Annotated[
    bool,
    typer.Option("--json", hidden=True, help="Print one JSON object on stdout and nothing else there."),
]
Events = Annotated[
    bool,
    typer.Option("--events", hidden=True, help="Print one JSON line per progress event on stderr."),
]
Color = Annotated[
    When,
    typer.Option("--color", metavar="WHEN", hidden=True, help="auto, always or never. Default: auto."),
]
NoInput = Annotated[
    bool,
    typer.Option(
        "--no-input",
        hidden=True,
        help="Never prompt. Take the safe default, or refuse and name the flag that would have answered.",
    ),
]
Verbose = Annotated[
    bool,
    typer.Option("-v", "--verbose", hidden=True, help="Debug lines on stderr, and the traceback of a bug."),
]
Quiet = Annotated[
    bool,
    typer.Option("-q", "--quiet", hidden=True, help="Warnings and errors only on stderr."),
]
Yes = Annotated[
    bool,
    typer.Option("--yes", hidden=True, help="Recognised and refused. Every prompt here has a flag of its own."),
]
Sections = Annotated[
    list[str] | None,
    typer.Option(
        "--section",
        metavar="N",
        rich_help_panel=Panel.SCOPE.value,
        help="Only these sections: 3, 3,5 or 7-9. Repeats.",
    ),
]
Allow = Annotated[
    list[Code] | None,
    typer.Option(
        "--allow",
        metavar="CODE",
        rich_help_panel=Panel.FINDINGS.value,
        help="Carry on past this finding code. Repeats.",
    ),
]
Fail = Annotated[
    FailOn,
    typer.Option(
        "--fail-on",
        metavar="WHEN",
        rich_help_panel=Panel.FINDINGS.value,
        help="certain fails on a certain finding, any fails on any finding, never fails on none. Default certain.",
    ),
]
Fix = Annotated[
    bool | None,
    typer.Option(
        "--fix/--no-fix",
        rich_help_panel=Panel.FINDINGS.value,
        help="Apply every safe fix, or apply none. An unsafe fix is printed either way and never applied.",
    ),
]
NoVoice = Annotated[
    bool,
    typer.Option(
        "--no-voice",
        rich_help_panel=Panel.SPENDING.value,
        help="Placeholder narration: no API key and no spend.",
    ),
]
Spend = Annotated[
    bool,
    typer.Option(
        "--spend",
        rich_help_panel=Panel.SPENDING.value,
        help="Voice what needs it without asking first.",
    ),
]
MaxCost = Annotated[
    float | None,
    typer.Option(
        "--max-cost",
        metavar="N",
        rich_help_panel=Panel.SPENDING.value,
        help="Refuse before the first call if the most this run can cost is over N US dollars.",
    ),
]
Overrides = Annotated[
    list[str] | None,
    typer.Option(
        "--set",
        metavar="KEY=VALUE",
        rich_help_panel=Panel.THIS_RUN.value,
        help="Override one setting here. Repeats. See config explain.",
    ),
]
Skip = Annotated[
    list[Stage] | None,
    typer.Option(
        "--skip",
        metavar="STAGE",
        rich_help_panel=Panel.SCOPE.value,
        help="Run every stage but this one. Repeats.",
    ),
]
Force = Annotated[
    bool,
    typer.Option(
        "--force",
        rich_help_panel=Panel.REDOING.value,
        help="Build again from nothing, keeping every voiced take.",
    ),
]
ReplaceVoiced = Annotated[
    bool,
    typer.Option(
        "--replace-voiced",
        rich_help_panel=Panel.REDOING.value,
        help="Voice a section again and discard the take it replaces, which is the one flag here that destroys money.",
    ),
]

GLOBALS: tuple[tuple[str, Any, Any], ...] = (
    ("project", Project, None),
    ("json_out", Json, False),
    ("events", Events, False),
    ("color", Color, When.AUTO),
    ("no_input", NoInput, False),
    ("verbose", Verbose, False),
    ("quiet", Quiet, False),
    ("yes", Yes, False),
)
"""Every flag that works on every command, before or after the command name, with its default."""

FINDING_FAMILY: tuple[tuple[str, Any, Any], ...] = (
    ("fail_on", Fail, FailOn.CERTAIN),
    ("allow", Allow, None),
)
"""The two flags a command that reports judgements carries, derived from its result model."""

SPEND_FAMILY: tuple[tuple[str, Any, Any], ...] = (
    ("no_voice", NoVoice, False),
    ("spend", Spend, False),
    ("max_cost", MaxCost, None),
)
"""The three flags a command that can buy something carries, derived from its result model."""

SHARED: frozenset[str] = frozenset(name for name, _, _ in (*GLOBALS, *FINDING_FAMILY, *SPEND_FAMILY))
"""Every parameter name the wrapper takes off a command's own call, so a command reads none of them."""


def shared_for(result: object) -> list[inspect.Parameter]:
    """The parameters this command shares with others, read off the result its signature returns.

    The families come first so that they sit in their own panels above the hidden globals, and the
    globals come last because a reader who wants them reads the line that names them all at once.
    """
    families = list(GLOBALS)
    if isinstance(result, type) and issubclass(result, Result):
        if result in JUDGES:
            families = [*FINDING_FAMILY, *families]
        if result in SPENDS:
            families = [*SPEND_FAMILY, *families]
    return [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default)
        for name, annotation, default in families
    ]


def sections_of(values: Sequence[str] | None) -> tuple[int, ...] | None:
    """Every section a run of `--section` values names, in order and without repeats.

    The text is parsed by the library, which owns the one spelling of a selection, and a refusal is
    a usage error here because the value came off the command line.
    """
    if not values:
        return None
    found: list[int] = []
    for value in values:
        try:
            found.extend(section_numbers(value))
        except InputError as refused:
            raise typer.BadParameter(str(refused), param_hint="--section") from refused
    return tuple(dict.fromkeys(found))


def one_section(values: Sequence[str] | None) -> int:
    """The single section a command that cuts one piece out needs, refused when it is not one."""
    sections = sections_of(values)
    if sections is None or len(sections) != 1:
        raise typer.BadParameter("names exactly one section, such as --section 3.", param_hint="--section")
    return sections[0]


def allowed(codes: Iterable[Code] | None) -> frozenset[Code]:
    """The finding codes a run carries on past, which the exit code is worked out without."""
    return frozenset(codes or ())


def pairs(values: Sequence[str] | None) -> tuple[str, ...]:
    """Every `--set` override, each refused here when it is not a pair at all.

    The key and the value are handed to the settings loader as they were written, because the fifth
    layer is the fourth layer's mapping with this pair on top and a second reader would drift.
    """
    written = tuple(values or ())
    for value in written:
        if "=" not in value or not value.partition("=")[0]:
            raise typer.BadParameter(f"{value!r} is not a KEY=VALUE pair.", param_hint="--set")
    return written


__all__ = [
    "GLOBALS",
    "JUDGES",
    "SHARED",
    "SPENDS",
    "Allow",
    "Color",
    "Events",
    "Fail",
    "FailOn",
    "Fix",
    "Force",
    "Group",
    "Json",
    "MaxCost",
    "NoInput",
    "NoVoice",
    "Overrides",
    "Panel",
    "Project",
    "Quiet",
    "ReplaceVoiced",
    "Sections",
    "Skip",
    "Spend",
    "Verbose",
    "When",
    "Where",
    "Yes",
    "allowed",
    "one_section",
    "pairs",
    "sections_of",
    "shared_for",
]
