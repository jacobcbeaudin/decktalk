"""The sound seam: its own table of adapters beside the voices, and what each adapter declares."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from decktalk.errors import InputError
from decktalk.results import SoundKind
from decktalk.speech.sound import SOUND_DECLARED, SOUNDS, SoundContext, SoundProvider, Sounds, endpoint
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
    fields: dict[str, Any] = {"secrets": NoSecrets(), "api_base": "", "timeout_seconds": 1}
    return SoundContext(**{**fields, **over})


def test_the_machine_decides_where_the_key_may_go_and_how_often_a_request_is_sent_again() -> None:
    made: list[SoundContext] = []

    def house(context: SoundContext) -> SoundProvider:
        made.append(context)
        return Quiet()

    Sounds(factories={"house": house}, allow_any_api_base=True, retries=3).provider(
        "house", a_context(allow_any_api_base=False)
    )
    assert (made[0].allow_any_api_base, made[0].retries) == (True, 3)


def test_a_sound_provider_name_the_table_does_not_hold_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(InputError) as caught:
        Sounds(factories=SOUNDS).provider("a-local-sound", a_context())
    assert "not a sound provider this machine answers for" in str(caught.value)
    assert "elevenlabs" in (caught.value.hint or "")


def test_an_empty_table_answers_for_no_sound_provider() -> None:
    with pytest.raises(InputError) as caught:
        Sounds(factories={}).provider("elevenlabs", a_context())
    assert "none" in (caught.value.hint or "")


def test_the_shipped_sound_table_is_one_adapter_and_declares_its_key() -> None:
    assert sorted(SOUNDS) == sorted(SOUND_DECLARED) == ["elevenlabs"]
    assert SOUND_DECLARED["elevenlabs"].key_variable == "ELEVENLABS_API_KEY"
    assert SOUND_DECLARED["elevenlabs"].table == "elevenlabs"


def test_a_sound_provider_a_host_registered_names_its_requests_by_its_own_name() -> None:
    assert endpoint("house", SoundKind.MUSIC) == "house:music"
    assert endpoint("house", SoundKind.EFFECT) == "house:effect"
