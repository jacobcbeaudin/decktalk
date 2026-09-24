"""The table: one handler per command, and nothing the parser names left without one."""

from __future__ import annotations

from typing import get_type_hints

from decktalk.cli import authoring, dispatch, machine, video
from decktalk.cli.envelope import Outcome
from decktalk.cli.parser import BY_NAME, COMMANDS


def test_a_handler_exists_for_every_command_and_none_exists_for_anything_else():
    assert sorted(dispatch.HANDLERS) == sorted(BY_NAME)


def test_every_handler_takes_its_own_command_s_options_and_gives_back_an_outcome():
    """A handler that read another command's options would read a flag its own command never took."""
    for name, handler in dispatch.HANDLERS.items():
        hints = get_type_hints(handler)
        assert hints["opts"] is BY_NAME[name].options, name
        assert hints["return"] is Outcome, name


def test_each_group_of_the_command_table_lives_in_its_own_module():
    """The help screen's groups and the modules are the same split, so a reader follows one map."""
    homes = {
        "one machine": machine,
        "before a build": authoring,
        "the pipeline": video,
        "the whole run": video,
    }
    for command in COMMANDS:
        assert dispatch.HANDLERS[command.name] is getattr(homes[command.group], command.name), command.name
