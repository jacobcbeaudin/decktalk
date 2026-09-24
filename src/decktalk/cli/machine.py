"""The three commands about this computer: writing a project, fetching the toolchain and reporting.

None of the three opens a project, because there is nothing to open until `init` returns and because
`install` and `doctor` are about the machine whatever project a caller is standing in. They still
open a run, so `--events` is not silent through the two commands that download two hundred megabytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer._click import Context

from decktalk import machine as machines
from decktalk.cli import session as sessions
from decktalk.cli.app import DOCS, command
from decktalk.cli.options import Fix, Group
from decktalk.results import DoctorResult, InitResult, InstallResult

INIT_EPILOG = f"""\
Writes decktalk.toml, script.md, cues.json and a deck that builds with no
credential. The JSON object carries run, root, name, example, skills and
written. Docs: {DOCS}#init"""

INSTALL_EPILOG = f"""\
Fetches into this machine's cache, which doctor names. The JSON object
carries run, tools and cache. Docs: {DOCS}#install"""

DOCTOR_EPILOG = f"""\
Reads this machine and fetches nothing, and --measure writes the bias it
measured. The JSON object carries run, written, tools, cache, python,
platform, voice_key and bias_ms. Docs: {DOCS}#doctor"""


@command(group=Group.MACHINE, epilog=INIT_EPILOG)
def init(
    ctx: Context,
    directory: Annotated[Path, typer.Argument(metavar="DIR", help="Where to write the project.")],
    name: Annotated[str | None, typer.Option("--name", metavar="NAME", help="The project's name.")] = None,
    example: Annotated[
        str | None, typer.Option("--example", metavar="NAME", help="The packaged example to write.")
    ] = None,
    no_skills: Annotated[bool, typer.Option("--no-skills", help="Leave the packaged skills out.")] = False,
    defaults: Annotated[bool, typer.Option("--defaults", help="Take every default and ask nothing.")] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Write into a directory that already holds files.")
    ] = False,
) -> InitResult:
    """Create a project with a deck that already builds.

    On a terminal it asks for the name, the example and whether to write the skills. Without one it
    takes the defaults and reports what it chose.
    """
    session = sessions.of(ctx)
    root = directory.expanduser()
    chosen, picked, skills = _guided(session, root, name=name, example=example, no_skills=no_skills, ask=not defaults)
    if _occupied(root) and not overwrite and not _agreed(session, root):
        raise session.refuse(
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
            force=overwrite or _occupied(root),
        )


def _guided(
    session: sessions.Session,
    root: Path,
    *,
    name: str | None,
    example: str | None,
    no_skills: bool,
    ask: bool,
) -> tuple[str, str | None, bool]:
    """The three answers `init` needs, asked on a terminal and taken from the flags without one."""
    # The packaged examples ride in the wheel beside the skills, so the list is read by the one
    # command that offers them rather than by every import of the command line.
    from decktalk.template import STARTER, listed_names  # noqa: PLC0415

    chosen, picked, skills = name or root.name, example, not no_skills
    if not ask or not session.asks:
        return chosen, picked, skills
    chosen = session.ask("Project name", default=chosen)
    picked = session.ask(f"Example ({listed_names()})", default=picked or STARTER)
    skills = session.confirm("Write the packaged skills into the project?", default=skills)
    return chosen, picked, skills


def _occupied(root: Path) -> bool:
    """True when the directory holds anything at all, which is what makes `init` a different risk."""
    return root.is_dir() and any(root.iterdir())


def _agreed(session: sessions.Session, root: Path) -> bool:
    """Whether a person at a terminal said to write into a directory that already holds files."""
    return session.asks and session.confirm(f"{root.name} is not empty. Write the project into it?")


@command(group=Group.MACHINE, epilog=INSTALL_EPILOG)
def install(ctx: Context) -> InstallResult:
    """Fetch Chromium and ffmpeg before a build needs them.

    Nothing has to run this, because a build fetches what it needs. It exists for a container layer,
    a job that caches the download and a machine that will be offline later. A second run names what
    was already there and fetches nothing.
    """
    session = sessions.of(ctx)
    if _asks_for_sudo() and session.asks and not session.confirm(_SUDO_QUESTION, default=True):
        raise session.refuse(
            "installing Chromium's system libraries needs a password that was not given.",
            hint="Run decktalk install again when you can give one, or install the libraries yourself.",
        )
    with session.watching(session.machine.events):
        return session.machine.install(cancel=session.cancel)


_SUDO_QUESTION = "Installing Chromium's system libraries asks for your password. Carry on?"
"""The one question `install` asks, which is asked because the answer costs a password."""


def _asks_for_sudo() -> bool:
    """True on the platform where installing the browser also installs its system libraries."""
    import sys  # noqa: PLC0415  (the platform is read once, by the one command that asks about it)

    return sys.platform.startswith("linux")


@command(group=Group.MACHINE, epilog=DOCTOR_EPILOG)
def doctor(
    ctx: Context,
    measure: Annotated[
        bool, typer.Option("--measure", help="Measure this host's presentation bias, which drives a browser.")
    ] = False,
    fix: Fix = None,
) -> DoctorResult:
    """Report what is installed and what a run would use.

    It reports the machine where `status` reports the project. On a terminal it names what is
    missing and then offers to fetch it once.
    """
    session = sessions.of(ctx)
    with session.watching(session.machine.events):
        reported = session.machine.doctor(measure=measure, cancel=session.cancel)
    if reported.findings and _fixing(session, fix):
        session.machine.apply(reported.findings)
        with session.watching(session.machine.events):
            return session.machine.doctor(measure=measure, cancel=session.cancel)
    return reported


def _fixing(session: sessions.Session, fix: bool | None) -> bool:
    """Whether the safe fixes are applied, which the flag decides and a terminal may be asked."""
    if fix is not None:
        return fix
    return session.asks and session.confirm("Fetch what is missing now?", default=True)


__all__ = ["doctor", "init", "install"]
