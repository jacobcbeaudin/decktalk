"""One run of one command: the flags in force, the streams it writes to, and how it ends.

A command is handed one of these and reads its flags from it, opens the project or the machine
through it, and returns its result. The session decides the rendering from the terminal, attaches
the renderers to the library's event stream for the length of the call, works out the exit code
from the judgements and the threshold, and turns a refusal into the one error block.

The prompts live here too, because every one of them has the same shape: on a terminal it asks, and
without one it either takes the safe default or refuses and names the flag that would have answered.
A prompt an agent cannot answer and a flag that does not exist are the same failure.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from typer._click import Context

from decktalk import project as projects
from decktalk.cli import output
from decktalk.cli.options import FailOn, When
from decktalk.errors import ApprovalRequired, Cancel, DeckTalkError, ErrorCode, ErrorInfo
from decktalk.events import Events
from decktalk.findings import Certainty, Code
from decktalk.machine import Machine
from decktalk.project import Project
from decktalk.results import ErrorResult, Result, Spend, Voicing

NO_COLOR = "NO_COLOR"
"""The variable a reader sets to ask every tool for no colour, which is honoured before any flag."""

FOUND_SOMETHING = 1
"""What a run exits with when it judged something at or above the threshold `--fail-on` set."""


@dataclass(frozen=True)
class Globals:
    """Every flag that works on every command, in force for this run."""

    project: Path | None = None
    json_out: bool = False
    events: bool = False
    color: When = When.AUTO
    no_input: bool = False
    verbose: bool = False
    quiet: bool = False
    yes: bool = False

    def merged(self, values: dict[str, Any]) -> Globals:
        """These flags with the ones written after the command name on top.

        A flag is taken from the command when it is not its default, which is what lets
        `decktalk --json build` and `decktalk build --json` mean the same thing.
        """
        blank = Globals()
        stated = {name: value for name, value in values.items() if value is not None and value != getattr(blank, name)}
        return replace(self, **stated)


@dataclass(frozen=True)
class Terminal:
    """What is on the other end of the two streams, which is what chooses every rendering."""

    is_terminal: bool
    is_dumb: bool
    no_color: bool
    json: bool
    events: bool
    quiet: bool

    @property
    def live(self) -> bool:
        """True when a transient region may animate, which needs a real terminal and one stream to itself.

        `--events` turns it off because a live region and a line stream cannot share one stream
        without control characters landing in the stream.
        """
        return self.is_terminal and not self.is_dumb and not self.no_color and not self.json and not self.events


class Session:
    """One command in progress, with its flags, its two consoles and its answer."""

    def __init__(self, flags: Globals, *, command: str) -> None:
        self.flags = flags
        self.command = command
        self.cancel = Cancel()
        self.allowed: frozenset[Code] = frozenset()
        self.fail_on = FailOn.CERTAIN
        self.no_voice = False
        self.spend = False
        self.max_cost: float | None = None
        self._said = False
        self.out = Console(
            file=sys.stdout,
            no_color=flags.color is When.NEVER,
            force_terminal=True if flags.color is When.ALWAYS else None,
            soft_wrap=True,
        )
        self.err = Console(
            stderr=True,
            no_color=flags.color is When.NEVER,
            force_terminal=True if flags.color is When.ALWAYS else None,
            soft_wrap=True,
        )
        self.terminal = Terminal(
            is_terminal=self.err.is_terminal,
            is_dumb=self.err.is_dumb_terminal,
            no_color=self.err.no_color or bool(os.environ.get(NO_COLOR)),
            json=flags.json_out,
            events=flags.events,
            quiet=flags.quiet,
        )
        self._machine: Machine | None = None
        self._overrides: tuple[str, ...] = ()

    # ---- what the command opens -------------------------------------------------------------

    def overriding(self, overrides: Sequence[str]) -> None:
        """Hold this run's `--set` pairs, which are validated by the loader the first call opens."""
        self._overrides = tuple(overrides)

    @property
    def machine(self) -> Machine:
        """This machine, read once, which is the only reading of the environment there is."""
        if self._machine is None:
            self._machine = Machine.from_environment(overrides=_split(self._overrides))
        return self._machine

    def project(self) -> Project:
        """The project this run is about, opened on this machine with this run's overrides."""
        return projects.open(self.flags.project, machine=self.machine, overrides=self._overrides)

    # ---- the stream -------------------------------------------------------------------------

    @contextmanager
    def watching(self, events: Events, *, opening: bool = False) -> Iterator[None]:
        """Render this call's events for as long as it runs, and leave the stream as it was found.

        `opening` names the run and its events file on the first line of stderr, which is what lets
        an agent that backgrounds a build name its own events file while the run is live.
        """
        renderers: list[Any] = [output.Notes(self.err, verbose=self.flags.verbose, quiet=self.flags.quiet)]
        if opening:
            renderers.append(output.Opening(self.err))
        if self.terminal.events:
            renderers.append(output.Jsonl(self.err))
        elif self.terminal.quiet:
            pass
        elif self.terminal.live:
            renderers.append(output.Region(self.err))
        else:
            renderers.append(output.Lines(self.err))
        for renderer in renderers:
            renderer.open()
        subscriptions = [events.subscribe(renderer) for renderer in renderers]
        try:
            yield
        finally:
            for subscription in subscriptions:
                subscription.close()
            for renderer in renderers:
                renderer.close()

    # ---- the prompts ------------------------------------------------------------------------

    @property
    def asks(self) -> bool:
        """True when this run may ask a person a question, which needs a terminal and no `--no-input`."""
        return self.terminal.is_terminal and not self.flags.no_input and not self.flags.json_out

    def confirm(self, question: str, *, default: bool = False) -> bool:
        """Ask one yes or no question on stderr, which is only ever called when `asks` is true."""
        return bool(typer.confirm(question, default=default, err=True))

    def ask(self, question: str, *, default: str) -> str:
        """Ask one question with a default on stderr, which is only ever called when `asks` is true."""
        return str(typer.prompt(question, default=default, err=True))

    def refuse(self, message: str, *, hint: str) -> ApprovalRequired:
        """The refusal a run makes when a prompt had no terminal and no flag answered it."""
        return ApprovalRequired(message, hint=hint)

    def say(self, message: str) -> None:
        """One sentence on stderr, which is where everything but the result goes."""
        if not self.flags.quiet:
            self.err.print(message)

    # ---- the spend gate -----------------------------------------------------------------------

    def spending(self, *, no_voice: bool, spend: bool, max_cost: float | None) -> None:
        """Hold the three flags that decide what this run buys, which every paid call reads."""
        self.no_voice, self.spend, self.max_cost = no_voice, spend, max_cost

    def voicing(self, project: Project, *, storyboard: bool = False) -> Voicing:
        """What this run does about the voice, asked once before anything is bought.

        On a terminal the checkpoint is the storyboard and the price: the run says what it will cost
        and where to look at what it is about to narrate, and then it asks. Without a terminal there
        is nobody to ask, so the run refuses and names the two flags that answer, and the refusal
        carries the price so that one call prices the run.
        """
        if self.no_voice:
            return Voicing.PLACEHOLDER
        if self.spend:
            return Voicing.PAID
        priced = self.price(project)
        if not self.asks:
            raise self.refuse(_spend_sentence(priced), hint=_spend_hint(self.command))
        if storyboard:
            self.say(self.storyboard_line(project))
        if priced is not None:
            self.say(f"Voicing costs up to ${priced.ceiling_dollars:.2f} at ${priced.dollars:.2f} for what changed.")
        if self.confirm("Spend that now?"):
            return Voicing.PAID
        return Voicing.PLACEHOLDER

    def price(self, project: Project) -> Spend | None:
        """What a voiced run of this project would cost, read without opening a browser.

        The judgement that prices a run is `check`, so the price a refusal carries and the price
        `decktalk check --json` reports are one number worked out in one place.
        """
        try:
            return project.check(pages=False, frames=False).spend
        except DeckTalkError:
            return None

    def storyboard_line(self, project: Project) -> str:
        """The storyboard this checkpoint points at, drawn now so that the path names a real page.

        It prints the path and never opens a browser, which is what the founder decided: a side
        effect no flag asked for cannot be honoured by a remote session.
        """
        written = project.storyboard()
        where = written.storyboard.as_posix() if written.storyboard else "nothing"
        return f"Storyboard {where}, {len(written.panels)} panels."

    # ---- how a command ends -------------------------------------------------------------------

    def judging(self, *, fail_on: FailOn, allow: frozenset[Code]) -> None:
        """Hold the threshold and the carried codes this run's exit code is worked out from."""
        self.fail_on, self.allowed = fail_on, allow

    def report(self, result: Result) -> int:
        """Write the result the way the terminal asked for it, and give back the exit code.

        A command that had to write its answer before it went on waiting, such as `serve`, calls
        this itself, so it is written once and the exit code is worked out however often it is asked
        for.
        """
        if self._said:
            return self.exit_code(result)
        self._said = True
        if self.flags.json_out:
            self.out.file.write(result.model_dump_json(indent=2) + "\n")
            self.out.file.flush()
        else:
            output.render(result, self.out)
        return self.exit_code(result)

    def document(self, contract: object) -> int:
        """Write one contract document on stdout, which is the one output that carries no envelope.

        A JSON Schema document inside an envelope is not that document, and a reserved key called
        `schema` set to 2 inside a document about schemas is unreadable.
        """
        self._said = True
        self.out.file.write(json.dumps(contract, indent=2, sort_keys=False, default=str) + "\n")
        self.out.file.flush()
        return 0

    def exit_code(self, result: Result) -> int:
        """0 found nothing, 1 found something at the threshold, and the code's own when it could not run."""
        if result.error is not None:
            return result.error.code.exit_code
        judged = [found for found in result.findings if found.code not in self.allowed]
        if self.fail_on is FailOn.NEVER or not judged:
            return 0
        if self.fail_on is FailOn.ANY:
            return FOUND_SOMETHING
        return FOUND_SOMETHING if any(found.certainty is Certainty.CERTAIN for found in judged) else 0

    def failed(self, error: DeckTalkError) -> int:
        """Report a refusal as the one error block, or as the one error object under `--json`."""
        return self.reported(ErrorInfo.of(error))

    def reported(self, info: ErrorInfo) -> int:
        """Write one refusal, and give back the exit code its own code carries."""
        if self.flags.json_out:
            self.out.file.write(ErrorResult(ok=False, error=info).model_dump_json(indent=2) + "\n")
            self.out.file.flush()
        else:
            output.error_block(info, self.err)
        return info.code.exit_code

    def bug(self, failure: BaseException) -> int:
        """Report anything DeckTalk did not mean to raise, with its traceback under `-v` alone."""
        if self.flags.verbose:
            self.err.print_exception()
        info = ErrorInfo(
            code=ErrorCode.INTERNAL,
            message=f"{type(failure).__name__}: {failure}",
            hint="Run the command again with -v for the traceback, and open an issue with it.",
            docs=ErrorCode.INTERNAL.url,
        )
        return self.reported(info)


def _spend_sentence(spend: Spend | None) -> str:
    """The sentence an approval refusal carries, with the price in it whenever the price is known."""
    if spend is None:
        return "this run would voice narration and no terminal is here to approve it."
    sends = f"sends {spend.characters} characters and " if spend.characters else ""
    return (
        f"voicing {len(spend.sections)} sections {sends}costs up to ${spend.ceiling_dollars:.2f}, "
        "and no terminal is here to approve it."
    )


def _spend_hint(command: str) -> str:
    """The two whole commands that answer a spend refusal, which is what makes a hint a hint."""
    return (
        f"Run decktalk {command} --spend to approve that spend, or decktalk {command} --no-voice to "
        "finish with placeholder narration."
    )


def _split(overrides: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Each `--set` pair as its two halves, which is how a machine takes them."""
    return tuple((pair.partition("=")[0], pair.partition("=")[2]) for pair in overrides)


def of(ctx: Context) -> Session:
    """This run's session, which the root callback made and every command reads."""
    session = ctx.obj
    if not isinstance(session, Session):
        raise RuntimeError("a command was invoked with no session, which the root callback always makes")
    return session


__all__ = ["Globals", "Session", "Terminal", "of"]
