"""The sound seam: its own table of adapters beside the voices, and what each adapter declares."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from decktalk.errors import InputError
from decktalk.results import SoundKind
from decktalk.speech.sound import (
    SOUND_DECLARED,
    SOUNDS,
    SoundContext,
    SoundFactory,
    SoundProvider,
    SoundProviders,
    endpoint,
)
from support.speech import NoSecrets


@dataclass
class Quiet:
    """A sound provider that answers every request with nothing and spends nothing."""

    name: str = "house"

    def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        return b""

    def music(self, body: Mapping[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        return b""


def a_context(**over: Any) -> SoundContext:
    fields: dict[str, Any] = {"secrets": NoSecrets(), "base_url": "", "timeout_seconds": 1}
    return SoundContext(**{**fields, **over})


def test_the_machine_decides_how_often_a_request_is_sent_again() -> None:
    made: list[SoundContext] = []

    def house(context: SoundContext) -> SoundProvider:
        made.append(context)
        return Quiet()

    SoundProviders(factories={"house": house}, retries=3).provider("house", a_context(retries=0))
    assert made[0].retries == 3


def test_a_sound_provider_name_the_table_does_not_hold_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(InputError) as caught:
        SoundProviders(factories=SOUNDS).provider("a-local-sound", a_context())
    assert "not a sound provider this machine answers for" in str(caught.value)
    assert "elevenlabs" in (caught.value.hint or "")


def test_an_empty_table_answers_for_no_sound_provider() -> None:
    with pytest.raises(InputError) as caught:
        SoundProviders(factories={}).provider("elevenlabs", a_context())
    assert "none" in (caught.value.hint or "")


def test_the_shipped_sound_table_is_one_adapter_and_declares_its_key() -> None:
    assert sorted(SOUNDS) == sorted(SOUND_DECLARED) == ["elevenlabs"]
    assert SOUND_DECLARED["elevenlabs"].key_variable == "ELEVENLABS_API_KEY"
    assert SOUND_DECLARED["elevenlabs"].table == "elevenlabs"


def test_a_sound_provider_a_host_registered_names_its_requests_by_its_own_name() -> None:
    assert endpoint("house", SoundKind.MUSIC) == "house:music"
    assert endpoint("house", SoundKind.EFFECT) == "house:effect"


def house(_context: SoundContext) -> SoundProvider:
    """A sound provider a host wrote itself, which bills nobody DeckTalk knows of."""
    return Quiet()


def spellings(name: str) -> list[str]:
    """Ways of writing a shipped name that a reader takes for the shipped sound provider itself."""
    return [name.upper(), name.title(), f" {name}", f"{name}\n", "".join(chr(ord(c) + 0xFEE0) for c in name)]


@pytest.mark.parametrize("name", sorted(SOUND_DECLARED))
def test_a_host_sound_factory_under_a_shipped_name_is_refused(name: str) -> None:
    """A sound is priced at the rate its provider's name declares, so only the shipped code answers under it."""
    with pytest.raises(
        ValueError, match=rf"{name!r} is the name of a sound provider DeckTalk ships.*Register it under another"
    ):
        SoundProviders(factories={**SOUNDS, name: house})


@pytest.mark.parametrize(
    ("name", "spelled"), [(name, spelled) for name in sorted(SOUND_DECLARED) for spelled in spellings(name)]
)
def test_a_spelling_of_a_shipped_sound_name_is_refused_too(name: str, spelled: str) -> None:
    with pytest.raises(
        ValueError, match=re.escape(f"{spelled!r} is the name of a sound provider DeckTalk ships, {name!r}")
    ):
        SoundProviders(factories={spelled: house})


def test_the_shipped_sound_providers_register_under_their_own_names_beside_a_hosts() -> None:
    assert dict(SoundProviders(factories=SOUNDS).factories) == dict(SOUNDS)
    assert sorted(SoundProviders(factories={**SOUNDS, "house": house}).factories) == sorted([*SOUNDS, "house"])


def test_the_shipped_sound_tables_cannot_be_changed_in_place() -> None:
    with pytest.raises(TypeError):
        SOUNDS["other"] = house  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        SOUND_DECLARED["other"] = SOUND_DECLARED["elevenlabs"]  # ty: ignore[invalid-assignment]


def test_a_hosts_sound_table_changed_after_it_registered_changes_no_provider() -> None:
    table: dict[str, SoundFactory] = {"house": house}
    registered = SoundProviders(factories=table)
    table[next(iter(SOUND_DECLARED))] = house
    assert list(registered.factories) == ["house"]
