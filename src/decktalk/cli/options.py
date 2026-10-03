"""The flags, declared once, and the rule that decides which command carries which family.

A command's own parameters are written in its own signature. The flags several commands share are
not copied into each of them: they are derived from the command's return annotation, which is the
result model, so a command that reports findings takes `--fail-on` and `--allow` by being the sort
of command that reports findings, and a command that can spend takes `--spend/--no-spend` and
`--max-cost` by being the sort of command that spends.

The model says which sort it is. `Result.reports_findings` and `Result.spends` are declared beside
the fields they belong with, so a result added without either fact takes the globals alone and there
is no list here to keep in step with the models.

Each family is one flag over a vocabulary the product already publishes, rather than a flag per
value. `--allow` takes finding codes, `--skip` takes stages, `--set` takes settings keys, and
`--section` takes section numbers, so an agent that has read `decktalk schema` can already write
every one of them and a new code, stage or setting costs no help row at all.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, get_args

import typer

from decktalk.errors import InputError
from decktalk.findings import DOCS as REFERENCE
from decktalk.findings import Code, Severity
from decktalk.pipeline import Stage
from decktalk.project import section_numbers
from decktalk.results import Result

DOCS = f"{REFERENCE}/cli"
"""Where the generated reference page lives, which every command's help closes with."""

PROMPT_FLAGS: set[str] = set()
"""Every flag that answers a prompt, which is what a refused `--yes` names back at its caller.

It is filled by `answering` as each command module declares its options, so a flag says it answers
a prompt where it is declared and there is no second list to keep in step with the commands.
"""


def answering(*decls: str, **options: Any) -> Any:  # noqa: ANN401  (typer's own OptionInfo is untyped)
    """A `typer.Option` whose flags answer one of its command's prompts, recorded in `PROMPT_FLAGS`."""
    PROMPT_FLAGS.update(flag for decl in decls for flag in decl.split("/") if flag.startswith("--"))
    return typer.Option(*decls, **options)


class Group(Enum):
    """The five headings the command tree is read under, in the order a reader meets them."""

    MACHINE = "Set up this machine"
    PROJECT = "Read the project, buying nothing"
    CONTRACTS = "Read the settings and the schemas"
    STAGE = "Run one stage, in this order"
    WHOLE = "Run them all, or cut one piece out"


class Panel(Enum):
    """The headings one command's own options are read under, in the order a reader meets them."""

    SPEND = "Spend"
    SELECTION = "Selection"
    FINDINGS = "Findings"
    REDOING = "Redoing work"
    THIS_RUN = "This run only"
    WRITING = "Writing"


class FailOn(Enum):
    """The threshold a run fails on, which is the least severe finding that fails it, or never.

    Each threshold is spelled from the severity it is the threshold for, because a second spelling of
    those words would be a second vocabulary for one idea.
    """

    ERROR = Severity.ERROR.value
    WARNING = Severity.WARNING.value
    NEVER = "never"

    @property
    def stops_on(self) -> Severity | None:
        """The least severe finding that stops a build, which is the same line the exit code draws.

        A build that carried on past a finding its exit code fails on would spend on a film the
        caller has already said is wrong, and one that stopped short of it would refuse a film the
        caller said is fine, so the threshold is one decision read in two places.
        """
        return {FailOn.ERROR: Severity.ERROR, FailOn.WARNING: Severity.WARNING, FailOn.NEVER: None}[self]


class When(Enum):
    """When colour is written, which is the one thing `--color` decides."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


# The shared families, each written once and derived onto the commands that carry it. A hidden
# global is on every command so that it works after the command name as well as before it, and the
# command's own help closes by naming them in one line instead of eight rows of their own.
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
        rich_help_panel=Panel.SELECTION.value,
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
        help="error fails on an error, warning fails on any finding, never fails on none. Default error.",
    ),
]
Fix = Annotated[
    bool | None,
    answering(
        "--fix/--no-fix",
        rich_help_panel=Panel.FINDINGS.value,
        help="Apply every safe fix, or apply none. An unsafe fix is printed either way and never applied.",
    ),
]
Spend = Annotated[
    bool | None,
    answering(
        "--spend/--no-spend",
        rich_help_panel=Panel.SPEND.value,
        help=(
            "Buy what is missing without asking, or buy nothing and play a placeholder where a voiced take is missing. "
            "A free voice such as dtsp makes its takes either way. Unset, a terminal is asked and a run without one "
            "is refused."
        ),
    ),
]
MaxCost = Annotated[
    float | None,
    answering(
        "--max-cost",
        metavar="N",
        rich_help_panel=Panel.SPEND.value,
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
        rich_help_panel=Panel.SELECTION.value,
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
    answering(
        "--replace-voiced",
        rich_help_panel=Panel.REDOING.value,
        help="Set aside each voiced take: voice it again with --spend, or play a placeholder with --no-spend.",
    ),
]
ReplaceScore = Annotated[
    bool,
    answering(
        "--replace-score",
        rich_help_panel=Panel.REDOING.value,
        help="Buy each bought sound again with --spend. Without --spend it keeps every bought sound.",
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
)
"""Every flag that works on every command, before or after the command name, with its default."""

YES: tuple[str, Any, Any] = ("yes", Yes, False)
"""`--yes`, which every command recognises and refuses, because each of its prompts has a flag of its own."""


def shortest(annotation: object) -> str:
    """The shortest spelling of one declared flag, such as `-p` for `-p, --project`.

    Typer reads the first spelling of an `Annotated` option as its default, so it is read from there too.
    """
    info = get_args(annotation)[1]
    return min((one for one in (info.default, *info.param_decls) if isinstance(one, str)), key=len)


_EVERY = [shortest(annotation) for _, annotation, _ in GLOBALS]
SHARED_LINE = f"{', '.join(_EVERY[:-1])} and {_EVERY[-1]} work on every command."
"""The one line a command's help spends on the flags every command carries, each by its shortest name."""

FINDING_FAMILY: tuple[tuple[str, Any, Any], ...] = (
    ("fail_on", Fail, FailOn.ERROR),
    ("allow", Allow, None),
)
"""The two flags a command that reports judgements carries, derived from its result model."""

SPEND_FAMILY: tuple[tuple[str, Any, Any], ...] = (
    ("spend", Spend, None),
    ("max_cost", MaxCost, None),
)
"""The two flags a command that can buy something carries, derived from its result model."""

SHARED: frozenset[str] = frozenset(name for name, _, _ in (*GLOBALS, YES, *FINDING_FAMILY, *SPEND_FAMILY))
"""Every parameter name the wrapper takes off a command's own call, so a command reads none of them."""


def shared_for(result: object) -> list[inspect.Parameter]:
    """The parameters this command shares with others, read off the result its signature returns.

    The families come first so that they sit in their own panels above the hidden globals, and the
    globals come last because a reader who wants them reads the line that names them all at once.
    """
    families = [*GLOBALS, YES]
    if isinstance(result, type) and issubclass(result, Result):
        if result.reports_findings:
            families = [*FINDING_FAMILY, *families]
        if result.spends:
            families = [*SPEND_FAMILY, *families]
    return [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default)
        for name, annotation, default in families
    ]


def restated(annotation: object, **changes: object) -> object:
    """The same flag with some of its declaration changed for one command, leaving the shared one untouched."""
    base, info, *rest = get_args(annotation)
    mine = copy.copy(info)
    for name, value in changes.items():
        setattr(mine, name, value)
    return Annotated[base, mine, *rest]


def sections_of(values: Sequence[str] | None) -> tuple[int, ...] | None:
    """Every section a run of `--section` values names, in order and without repeats.

    The text is parsed by the library, which owns the one spelling of a selection, and a refusal is
    a usage error here because the value came off the command line.
    """
    if not values:
        return None
    try:
        return section_numbers(",".join(values))
    except InputError as refused:
        raise typer.BadParameter(str(refused), param_hint="--section") from refused


def one_section(values: Sequence[str] | None) -> int:
    """The single section a command that cuts one piece out needs, refused when it is not one."""
    sections = sections_of(values)
    if sections is None or len(sections) != 1:
        raise typer.BadParameter("names exactly one section, such as --section 3.", param_hint="--section")
    return sections[0]


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
    "SHARED",
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
    "Overrides",
    "Panel",
    "Project",
    "Quiet",
    "ReplaceScore",
    "ReplaceVoiced",
    "Sections",
    "Skip",
    "Spend",
    "Verbose",
    "When",
    "Yes",
    "one_section",
    "pairs",
    "restated",
    "sections_of",
    "shared_for",
]
