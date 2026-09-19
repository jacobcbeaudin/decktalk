"""The exceptions DeckTalk raises on purpose, and the closed list of codes the envelope reports them by."""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.errors import ConfigError, DeckTalkError, ErrorCode, MissingInputError, ProviderError, ToolError

WIRE = json.loads((Path(__file__).resolve().parents[1] / "data" / "vocabulary.json").read_text(encoding="utf-8"))


def test_the_codes_are_the_six_the_contract_lists_and_nothing_else():
    assert [code.value for code in ErrorCode] == WIRE["error_codes"]
    for code in ErrorCode:
        assert code != code.value, "a plain enum, so a raw code never compares equal to one"


def test_every_error_class_carries_its_own_code_and_a_bare_error_is_internal():
    assert [kind.code for kind in (ConfigError, MissingInputError, ProviderError, ToolError)] == [
        ErrorCode.CONFIG,
        ErrorCode.MISSING_INPUT,
        ErrorCode.PROVIDER,
        ErrorCode.TOOL,
    ]
    assert DeckTalkError("bare").code is ErrorCode.INTERNAL


def test_a_subclass_inherits_the_code_of_the_class_it_is_a_kind_of():
    class CarriesAResult(ConfigError):
        pass

    assert CarriesAResult("x").code is ErrorCode.CONFIG


def test_an_error_carries_the_next_action_the_file_and_the_line_when_the_raiser_knows_them():
    error = ConfigError("unknown key", hint="see the reference", path=Path("decktalk.toml"), line=4)
    assert (str(error), error.hint, error.path, error.line) == (
        "unknown key",
        "see the reference",
        Path("decktalk.toml"),
        4,
    )
    assert (ToolError("gone").hint, ToolError("gone").path, ToolError("gone").line) == (None, None, None)
