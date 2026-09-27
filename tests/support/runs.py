"""The machines and runs a test opens instead of the real ones, built one way for every stage test."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from decktalk.errors import Cancel
from decktalk.events import Event, Log
from decktalk.machine import Machine, Run, Toolchain
from decktalk.results import Voicing
from decktalk.settings import ToolsConfig

RUN_ID = "r1"
"""The id of every run `a_run` opens, which a test that reads a result's run compares against."""


def a_machine(root: Path, **environ: str) -> Machine:
    """A machine that read nothing, with its tool cache under the test's own directory."""
    return Machine(
        environ=environ,
        tables={},
        config_path=root / "config.toml",
        cwd=root,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(root / "cache"))),
    )


def a_run(
    root: Path,
    *,
    voice: Voicing = Voicing.PLACEHOLDER,
    max_cost: float | None = None,
    lines: list[Event] | None = None,
    **environ: str,
) -> Run:
    """One run opened straight on a machine that read nothing, with every line it emits kept in `lines`."""
    machine = Machine(environ=environ, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    if lines is not None:
        machine.events.subscribe(lines.append)
    return Run(machine, id=RUN_ID, cancel=Cancel(), voice=voice, max_cost=max_cost, root=root)


@dataclass
class Watched:
    """One run and every line it put on the stream, which is how a test reads what a stage reported."""

    run: Run
    lines: list[Event] = field(default_factory=list)

    def of(self, event: str) -> list[Event]:
        """Every line of one kind, in the order the stage emitted them."""
        return [line for line in self.lines if line.event == event]


def notes(run: Run) -> list[str]:
    """Every sentence the run puts on the stream from now on, which is where a reading that is not a code goes."""
    said: list[str] = []
    run.machine.events.subscribe(lambda event: said.append(event.message) if isinstance(event, Log) else None)
    return said
