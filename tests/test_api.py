"""`decktalk.__all__` is the whole supported Python API, and nothing else is promised.

One word names the command, the Python call, the type that call returns and the `--json` key, so
this file reads the command table and asks the package for each of the four.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest

import decktalk
from decktalk.cli.parser import BY_NAME
from decktalk.verdicts import Findings

# `init`, `install` and `doctor` act on a machine rather than on a project, and `serve` runs a
# server until it is stopped, so the contract keeps all four to the command line.
COMMAND_LINE_ONLY = {"init", "install", "doctor", "serve"}
CALLS = sorted(set(BY_NAME) - COMMAND_LINE_ONLY)


def test_all_is_sorted_and_every_name_in_it_exists():
    assert decktalk.__all__ == sorted(decktalk.__all__)
    assert len(set(decktalk.__all__)) == len(decktalk.__all__)
    for name in decktalk.__all__:
        assert hasattr(decktalk, name), name


@pytest.mark.parametrize("command", CALLS)
def test_every_command_is_one_python_call_of_the_same_name(command):
    assert command in decktalk.__all__, command
    assert callable(getattr(decktalk, command)), command


@pytest.mark.parametrize("command", CALLS)
def test_every_call_returns_the_result_type_named_after_its_command(command):
    """`decktalk X()` returns `XResult`, and that class is exported beside the function."""
    expected = f"{command.title().replace('_', '')}Result"
    returned = get_type_hints(getattr(decktalk, command))["return"]
    assert returned.__name__ == expected
    assert expected in decktalk.__all__
    assert getattr(decktalk, expected) is returned
    # It is a StageResult, so the CLI counts findings and prints an envelope knowing nothing else.
    assert get_type_hints(returned.findings.fget)["return"] is Findings
    assert get_type_hints(returned.to_dict).get("root") is Path


def test_the_public_api_names_the_vocabulary_a_caller_needs():
    for name in ("Project", "Voice", "Word", "Settings", "load_settings", "SpeechProvider", "__version__"):
        assert name in decktalk.__all__, name
    for name in ("DeckTalkError", "ConfigError", "MissingInputError", "ProviderError", "ToolError"):
        assert name in decktalk.__all__, name
    for name in ("Takes", "CueTimes", "RecordingLog"):
        assert name in decktalk.__all__, name
    assert decktalk.Takes.load(Path("nowhere.json")) is None


def test_the_certainty_of_a_verdict_and_the_code_of_an_error_are_exported_types():
    """A verdict's certainty and an error's code are compared by member, so a caller needs both types."""
    assert decktalk.Verdict.OFF_CUE.certainty is decktalk.Certainty.CERTAIN
    assert decktalk.ConfigError.code is decktalk.ErrorCode.CONFIG


def test_the_module_docstring_lists_what_all_holds():
    """The docstring is the first thing a reader meets, so it names every name `__all__` holds."""
    doc = decktalk.__doc__ or ""
    for name in decktalk.__all__:
        if name != "__version__":
            assert name in doc, name


def test_the_cli_reaches_nothing_the_api_hides():
    """A command that a project runs is a call, so a script never has to shell out to get it."""
    assert set(CALLS) <= set(decktalk.__all__)
    assert set(COMMAND_LINE_ONLY) & set(decktalk.__all__) == set()
