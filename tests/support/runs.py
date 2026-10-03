"""The machines and runs a test opens instead of the real ones, built one way for every stage test."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decktalk.errors import Cancel
from decktalk.events import Event, RunLog
from decktalk.machine import Machine, Toolchain
from decktalk.machine.run import Run
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
    spend: bool = False,
    max_cost: float | None = None,
    lines: list[Event] | None = None,
    **environ: str,
) -> Run:
    """One run opened straight on a machine that read nothing, with every line it emits kept in `lines`."""
    machine = Machine(environ=environ, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    if lines is not None:
        machine.events.subscribe(lines.append)
    return Run(machine, id=RUN_ID, cancel=Cancel(), spend=spend, max_cost=max_cost, root=root)


def a_voiced_run(root: Path, speech_providers: Mapping[str, Any], *, spend: bool = False) -> Run:
    """One run opened straight on a machine whose host handed it this voice table, as `Machine.of` takes it."""
    machine = Machine(
        environ={},
        tables={},
        config_path=root / "machine.toml",
        cwd=root,
        toolchain=Toolchain(),
        speech_providers=speech_providers,
    )
    return Run(machine, id=RUN_ID, cancel=Cancel(), spend=spend, root=root)


def a_sounding_run(
    root: Path, sounds: Mapping[str, Any], *, spend: bool = False, lines: list[Event] | None = None
) -> Run:
    """One run opened straight on a machine whose host handed it this sound table, as `Machine.of` takes it."""
    machine = Machine(
        environ={},
        tables={},
        config_path=root / "machine.toml",
        cwd=root,
        toolchain=Toolchain(),
        sound_providers=sounds,
    )
    if lines is not None:
        machine.events.subscribe(lines.append)
    return Run(machine, id=RUN_ID, cancel=Cancel(), spend=spend, root=root)


@dataclass
class Watched:
    """One run and every line it put on the stream, which is how a test reads what a stage reported."""

    run: Run
    lines: list[Event] = field(default_factory=list)

    def of[E: Event](self, kind: type[E]) -> list[E]:
        """Every line of one kind, in the order the stage emitted them, typed as that kind."""
        return [line for line in self.lines if isinstance(line, kind)]


def notes(run: Run) -> list[str]:
    """Every sentence the run puts on the stream from now on, which is where a reading that is not a code goes."""
    said: list[str] = []
    run.machine.events.subscribe(lambda event: said.append(event.message) if isinstance(event, RunLog) else None)
    return said
