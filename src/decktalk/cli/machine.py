"""The three commands about this computer: writing a project, fetching the toolchain and reporting.

None of the three opens a project, because there is nothing to open until `init` returns and because
`install` and `doctor` are about the machine whatever project a caller is standing in. They still
open a run, so `--events` is not silent through the two commands that download two hundred megabytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer import Context

from decktalk import machine as machines
from decktalk import template as templates
from decktalk.cli import session as sessions
from decktalk.cli.app import command
from decktalk.cli.options import Fix, Group, answering
from decktalk.errors import ApprovalRequired, InputError
from decktalk.results import DoctorResult, InitResult, InstallResult
from decktalk.template import STARTER, listed_names, names


@command(
    group=Group.MACHINE, epilog="Writes decktalk.toml, script.md, cues.json and a deck that builds with no credential."
)
def init(
    ctx: Context,
    directory: Annotated[Path, typer.Argument(metavar="DIR", help="Where to write the project.")],
    name: Annotated[str | None, answering("--name", metavar="NAME", help="The project's name.")] = None,
    example: Annotated[
        str | None,
        answering(
            "--example", metavar="NAME", help=f"The packaged project to write, {STARTER} by default: {listed_names()}."
        ),
    ] = None,
    no_skills: Annotated[bool, answering("--no-skills", help="Leave the packaged skills out.")] = False,
    overwrite: Annotated[
        bool, answering("--overwrite", help="Write into a directory that already holds files.")
    ] = False,
) -> InitResult:
    """Create a project with a deck that already builds.

    On a terminal it asks for the name, the example and whether to write the skills. Without one, or
    under `--no-input`, it takes the defaults and reports what it chose.
    """
    session = sessions.of(ctx)
    root = directory.expanduser()
    chosen, picked, skills = _guided(session, root, name=name, example=_example(example), no_skills=no_skills)
    if _occupied(root) and not session.approve(
        overwrite or None, f"{root.name} is not empty. Write the project into it?"
    ):
        raise ApprovalRequired(
            f"{root.name} already holds files, and writing a project over them could lose work.",
            hint=f"Run decktalk init {directory} --overwrite to write into it anyway.",
        )
    with session.watching(session.machine.events):
        return machines.init(
            root,
            machine=session.machine,
            name=chosen,
            example=picked,
            skills=skills,
            overwrite=overwrite or _occupied(root),
        )


def _example(name: str | None) -> str | None:
    """The `--example` value, refused as a usage error naming every example when nothing is packaged under it.

    The library refuses the same name as `INPUT`, because a call there is not a command line. Here
    the name came off the command line, which is where a refusal exits 2.
    """
    if name is None:
        return None
    try:
        return templates.example(name).name
    except InputError as refused:
        raise typer.BadParameter(f"{refused} {refused.hint}", param_hint="--example") from refused


def _guided(
    session: sessions.Session,
    root: Path,
    *,
    name: str | None,
    example: str | None,
    no_skills: bool,
) -> tuple[str, str | None, bool]:
    """The three answers `init` needs, asked on a terminal and taken from the flags without one."""
    chosen, picked, skills = name or root.name, example, not no_skills
    if not session.asks:
        return chosen, picked, skills
    chosen = session.ask("Project name", default=chosen)
    picked = session.ask("Example", default=picked or STARTER, choices=names())
    skills = session.confirm("Write the packaged skills into the project?", default=skills)
    return chosen, picked, skills


def _occupied(root: Path) -> bool:
    """True when the directory holds anything at all, which is what makes `init` a different risk."""
    return root.is_dir() and any(root.iterdir())


@command(group=Group.MACHINE, epilog="Fetches into this machine's cache, which doctor names.")
def install(ctx: Context) -> InstallResult:
    """Fetch Chromium and ffmpeg before a build needs them.

    Nothing has to run this, because a build fetches what it needs. It exists for a container layer,
    a job that caches the download and a machine that will be offline later. A second run names what
    was already there and fetches nothing.
    """
    session = sessions.of(ctx)
    if machines.installs_system_libraries() and session.asks and not session.confirm(_SUDO_QUESTION, default=True):
        raise ApprovalRequired(
            "installing Chromium's system libraries needs a password that was not given.",
            hint="Run decktalk install again when you can give one, or install the libraries yourself.",
        )
    with session.watching(session.machine.events):
        return session.machine.install(cancel=session.cancel)


_SUDO_QUESTION = "Installing Chromium's system libraries asks for your password. Carry on?"
"""The one question `install` asks, which is asked because the answer costs a password."""


@command(
    group=Group.MACHINE,
    epilog=(
        "Reads this machine first. On a terminal it then offers once to fetch what is missing. --fix fetches "
        "it without asking, under --json or --no-input too, and --no-fix fetches nothing, as do --no-input and "
        "--json without --fix."
    ),
)
def doctor(
    ctx: Context,
    fix: Fix = None,
) -> DoctorResult:
    """Report what is installed and what a run would use.

    It reports the machine where `status` reports the project. On a terminal it names what is
    missing and then offers to fetch it once.
    """
    session = sessions.of(ctx)
    with session.watching(session.machine.events):
        reported = session.machine.doctor(threshold=session.threshold, cancel=session.cancel)
    if reported.findings and session.approve(fix, "Fetch what is missing now?", default=True):
        with session.watching(session.machine.events):
            session.machine.apply(reported.findings)
            return session.machine.doctor(threshold=session.threshold, cancel=session.cancel)
    return reported


__all__ = ["doctor", "init", "install"]
