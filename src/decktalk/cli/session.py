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

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from typer import Context

from decktalk import project as projects
from decktalk.cli import output
from decktalk.cli.options import FailOn, When, pairs
from decktalk.errors import ApprovalRequired, Cancel, DeckTalkError, ErrorInfo
from decktalk.events import Events
from decktalk.files import json_text
from decktalk.findings import Code, Finding
from decktalk.machine import Machine, Threshold
from decktalk.project import Project
from decktalk.results import ErrorResult, Result, Spend, counted

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
        self.spend: bool | None = None
        self.max_cost: float | None = None
        self._said = False
        self.out, self.err = (
            Console(
                stderr=stderr,
                # None hands the choice to rich, which reads NO_COLOR, so the variable a reader sets to
                # ask every tool for no colour is honoured before any flag, `--color always` included.
                no_color=True if flags.color is When.NEVER else None,
                force_terminal=True if flags.color is When.ALWAYS else None,
                soft_wrap=True,
            )
            for stderr in (False, True)
        )
        self.terminal = Terminal(
            is_terminal=self.err.is_terminal,
            is_dumb=self.err.is_dumb_terminal,
            no_color=self.err.no_color,
            json=flags.json_out,
            events=flags.events,
            quiet=flags.quiet,
        )
        self._overrides: tuple[str, ...] = ()

    # ---- what the command opens -------------------------------------------------------------

    def overriding(self, overrides: Sequence[str]) -> None:
        """Hold this run's `--set` pairs, which are validated by the loader the first call opens."""
        self._overrides = tuple(overrides)

    def opened(self, overrides: Sequence[str] | None) -> Project:
        """This run's project, opened with its `--set` pairs, which the loader validates before a stage runs."""
        self.overriding(pairs(overrides))
        return self.project()

    @cached_property
    def machine(self) -> Machine:
        """This machine, read once, which is the only reading of the environment there is."""
        return Machine.from_environment(overrides=self._overrides)

    def project(self) -> Project:
        """The project this run is about, opened on this machine, which already carries this run's overrides.

        The machine is made from every `--set` pair, and a project opened on it starts from the
        machine's own overrides, so the project is given none of its own. A machine-scoped pair given
        to the project a second time would be refused, because a project may not set one.
        """
        return projects.open(self.flags.project, machine=self.machine)

    def fixes_wanted(self, findings: Sequence[Finding], fix: bool | None) -> list[Finding]:
        """The findings whose fixes the caller wants applied, asked once on a terminal, or none.

        A run with no terminal and no `fix` applies nothing, so an agent applies the fixes itself
        from the objects it holds.
        """
        offered = [found for found in findings if found.fix is not None]
        return offered if offered and self.approve(fix, f"Apply {counted(len(offered), 'fix', 'fixes')}?") else []

    # ---- the stream -------------------------------------------------------------------------

    @contextmanager
    def watching(self, events: Events, *, opening: bool = False, heard: set[str] | None = None) -> Iterator[None]:
        """Render this call's events for as long as it runs, and leave the stream as it was found.

        `opening` names the run and its events file on the first line of stderr, which is what lets
        an agent that backgrounds a build name its own events file while the run is live. `heard` is
        the set of notes already printed by an earlier call of the same command, which are not
        printed again.

        Under `--events` stderr carries the JSON lines and nothing else, because a reader parses every
        line of it. The log lines are among them, and `run.start` already names the events file.
        """
        renderers: list[output.Renderer] = []
        if self.terminal.events:
            renderers.append(output.Jsonl(self.err))
        else:
            renderers.append(output.Notes(self.err, verbose=self.flags.verbose, quiet=self.flags.quiet, heard=heard))
            if opening:
                renderers.append(output.Opening(self.err))
            if self.terminal.live and not self.terminal.quiet:
                renderers.append(output.Region(self.err))
            elif not self.terminal.quiet:
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

    def approve(self, flag: bool | None, question: str, *, default: bool = False) -> bool:
        """The caller's own answer when a flag gave one, else a person's on a terminal, else no."""
        if flag is not None:
            return flag
        return self.asks and self.confirm(question, default=default)

    def confirm(self, question: str, *, default: bool = False) -> bool:
        """Ask one yes or no question on stderr, which is only ever called when `asks` is true."""
        return bool(typer.confirm(question, default=default, err=True))

    def ask(self, question: str, *, default: str) -> str:
        """Ask one question with a default on stderr, which is only ever called when `asks` is true."""
        return str(typer.prompt(question, default=default, err=True))

    def say(self, message: str) -> None:
        """One sentence on stderr, which is where everything but the result goes, unless `--events` holds it."""
        if not self.flags.quiet and not self.terminal.events:
            self.err.print(message)

    # ---- the spend gate -----------------------------------------------------------------------

    def spending(self, *, spend: bool | None, max_cost: float | None) -> None:
        """Hold the two flags that decide what this run buys, where an unset `--spend` means ask."""
        self.spend, self.max_cost = spend, max_cost

    def spends(
        self,
        project: Project,
        *,
        price: Callable[[], Spend | None] | None = None,
        replacing: bool = False,
        storyboard: bool = False,
    ) -> bool:
        """Whether this run may buy what is missing, asked once before anything is bought.

        `--spend` and `--no-spend` answer it outright. Unset, the run is priced first, by `price` or
        else by the check that prices a voiced build. A run with nothing to buy is asked nothing and
        buys nothing, unless `replacing` says it was told to replace a voiced take or a bought sound,
        which buys it again.
        `--force` never reaches here, because it rebuilds what is free and so buys nothing. A run
        whose voice declares itself free is asked nothing either, and is let buy, because buying from
        it costs nothing. Spend gates money alone, so `--no-spend` still lets a free voice make its
        takes. Otherwise, on a terminal the checkpoint is the storyboard and the price: the run
        says what it will cost and where to look at what it is about to narrate, and then it asks.
        Without a terminal there is nobody to ask, so the run refuses and names the two flags that
        answer, and the refusal carries the price so that one call prices the run.
        """
        if self.spend is not None:
            return self.spend
        priced = (price or (lambda: self.price(project)))()
        if priced is not None and priced.free:
            return True
        if priced is not None and not priced.buys and not replacing:
            return False
        if not self.asks:
            raise ApprovalRequired(_spend_sentence(priced), hint=_spend_hint(self.command))
        if storyboard:
            self.say(self.storyboard_line(project))
        if priced is not None:
            self.say(priced.sentence)
        return self.confirm("Spend that now?")

    def price(self, project: Project, *, only: Sequence[int] | None = None) -> Spend | None:
        """What a voiced run of these sections would cost, read without opening a browser.

        The judgement that prices a run is `check`, so the price a refusal carries and the price
        `decktalk check --json` reports are one number worked out in one place.
        """
        try:
            return project.check(only=only, pages=False, frames=False).spend
        except DeckTalkError:
            # silent: the check run's own run.done line carries why it could not price.
            return None

    def sound_price(self, project: Project, *, only: Sequence[int] | None, replace_score: bool = False) -> Spend | None:
        """What buying this project's score would cost, read from its plan and its ledger.

        No run is opened and no client is built, so pricing takes no lock, writes no events file and
        can never buy anything. A run told to replace the score is priced at every item it selects.
        """
        from decktalk.stages import score  # noqa: PLC0415  (a stage is loaded by the call that needs it)

        try:
            return score.price(project._inputs, only=only, replace_score=replace_score)
        except DeckTalkError:
            # silent: the run that follows meets the same refusal and reports it.
            return None

    def storyboard_line(self, project: Project) -> str:
        """The storyboard this checkpoint points at, drawn now so that the path names a real page.

        It prints the path and never opens a browser, which is what the founder decided: a side
        effect no flag asked for cannot be honoured by a remote session.
        """
        written = project.storyboard()
        where = written.storyboard.as_posix() if written.storyboard else "nothing"
        return f"Storyboard {where}, {counted(len(written.panels), 'panel')}."

    # ---- how a command ends -------------------------------------------------------------------

    def judging(self, *, fail_on: FailOn, allow: frozenset[Code]) -> None:
        """Hold the threshold and the carried codes this run's exit code is worked out from."""
        self.fail_on, self.allowed = fail_on, allow

    @property
    def threshold(self) -> Threshold:
        """The threshold `--fail-on` and `--allow` name, which the exit code and `ok` are both read from."""
        return Threshold(stop_on=self.fail_on.stops_on, allow=self.allowed)

    def report(self, result: Result) -> int:
        """Write the result the way the terminal asked for it, and give back the exit code.

        A command that had to write its answer before it went on waiting, such as `serve`, calls
        this itself, so it is written once and the exit code is worked out however often it is asked
        for.
        """
        code = self.exit_code(result)
        if self._said:
            return code
        self._said = True
        # The library judged the result against the threshold its caller passed, and most commands
        # take none, so `ok` is read again from the exit code that `--fail-on` and `--allow` decide.
        judged = result.model_copy(update={"ok": code == 0})
        if self.flags.json_out:
            self._stdout(judged.model_dump_json(indent=2))
        else:
            output.render(judged, self.out)
        return code

    def document(self, contract: object) -> int:
        """Write one contract document on stdout, which is the one output that carries no envelope.

        A JSON Schema document inside an envelope is not that document, and a reserved key called
        `schema` set to 2 inside a document about schemas is unreadable.
        """
        self._said = True
        self._stdout(json_text(contract, indent=2))
        return 0

    def _stdout(self, text: str) -> None:
        """Write one JSON document on stdout whole, flushed so a reader that waits on it gets it now."""
        self.out.file.write(text + "\n")
        self.out.file.flush()

    def exit_code(self, result: Result) -> int:
        """0 found nothing at the threshold, 1 found something at it, and the code's own when it could not run."""
        if result.error is not None:
            return result.error.code.exit_code
        return FOUND_SOMETHING if self.threshold.fails(result.findings) else 0

    def failed(self, error: DeckTalkError) -> int:
        """Report a refusal as the one error block, or as the one error object under `--json`."""
        return self.reported(ErrorInfo.of(error))

    def reported(self, info: ErrorInfo) -> int:
        """Write one refusal, and give back the exit code its own code carries."""
        if self.flags.json_out:
            self._stdout(ErrorResult(ok=False, error=info).model_dump_json(indent=2))
        elif self.terminal.events:
            # Every line of stderr is one JSON object under `--events`, so the refusal is one as well.
            self.err.file.write(ErrorResult(ok=False, error=info).model_dump_json() + "\n")
            self.err.file.flush()
        else:
            output.error_block(info, self.err)
        return info.code.exit_code

    def bug(self, failure: BaseException) -> int:
        """Report anything DeckTalk did not mean to raise, with its traceback under `-v` alone.

        The traceback is never written under `--events`, because every line of stderr is JSON there.
        """
        if self.flags.verbose and not self.terminal.events:
            self.err.print_exception()
        return self.reported(ErrorInfo.of_failure(failure))


def _spend_sentence(spend: Spend | None) -> str:
    """The sentence an approval refusal carries, with the price in it whenever the price is known."""
    if spend is None:
        return "This run may buy something and no terminal is here to approve it."
    return f"{spend.sentence} No terminal is here to approve it."


def _spend_hint(command: str) -> str:
    """The two whole commands that answer a spend refusal, which is what makes a hint a hint."""
    return (
        f"Run decktalk {command} --spend to approve that spend, or decktalk {command} --no-spend to "
        "play a placeholder wherever a take is missing."
    )


def of(ctx: Context) -> Session:
    """This run's session, which the root callback made and every command reads."""
    session = ctx.obj
    if not isinstance(session, Session):
        raise RuntimeError("a command was invoked with no session, which the root callback always makes")
    return session


__all__ = ["Globals", "Session", "Terminal", "of"]
