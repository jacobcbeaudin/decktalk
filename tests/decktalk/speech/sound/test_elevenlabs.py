"""The ElevenLabs sound adapter: the endpoint it buys from, the key it sends and the base it refuses.

The adapter is the real one and only the socket is stood in for, so the URL, the key header, the
timeout and the bytes it hands back are the ones a paid run would see.
"""

from __future__ import annotations

import io
import json
import urllib.request
from typing import Any

import pytest

from decktalk.results import SoundKind
from decktalk.secret import Secret
from decktalk.settings import ElevenLabsConfig
from decktalk.speech import http as _http
from decktalk.speech.sound import SOUNDS, SoundContext, SoundProviders, endpoint
from decktalk.speech.sound.elevenlabs import MUSIC_PATH, PUBLISHED_BASE, SOUND_PATH, ElevenLabsSound

SENTINEL = "sk_sentinel_sound_key_that_must_never_print"
AUDIO = b"ID3 a sound"
BASE = "https://api.eu.residency.elevenlabs.io/v1"
FORMAT = "mp3_44100_128"


class Env:
    """A `.env` that holds every variable it is asked for, each set to the sentinel."""

    def require(self, *names: str) -> list[Secret]:
        return [Secret(SENTINEL, name) for name in names]


class Answer(io.BytesIO):
    status = 200


def answers(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Answer every request with the same audio, and give back what was asked, headers and all."""
    asked: list[dict[str, Any]] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> Answer:
        assert isinstance(request.data, bytes)
        sent = {"url": request.full_url, "body": json.loads(request.data), "headers": dict(request.headers)}
        asked.append({**sent, "timeout": timeout})
        return Answer(AUDIO)

    monkeypatch.setattr(_http, "urlopen", urlopen)
    return asked


def a_context(**over: Any) -> SoundContext:
    fields: dict[str, Any] = {"secrets": Env(), "base_url": BASE, "timeout_seconds": 42}
    return SoundContext(**{**fields, **over})


def test_an_effect_is_bought_from_the_sound_endpoint_of_the_base_it_was_built_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = answers(monkeypatch)
    sound = SoundProviders(factories=SOUNDS).provider("elevenlabs", a_context())
    assert sound.effect({"text": "a chime"}, output_format=FORMAT) == AUDIO
    (sent,) = asked
    assert sent["url"] == f"{BASE}{SOUND_PATH}?output_format={FORMAT}"
    assert sent["body"] == {"text": "a chime"}
    assert sent["headers"]["Xi-api-key"] == SENTINEL
    assert sent["timeout"] == 42


def test_music_is_bought_from_the_music_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = answers(monkeypatch)
    sound = ElevenLabsSound.for_context(a_context())
    assert sound.music({"prompt": "strings"}, output_format=FORMAT) == AUDIO
    assert asked[0]["url"] == f"{BASE}{MUSIC_PATH}?output_format={FORMAT}"


def test_the_key_is_never_printed_by_the_adapter() -> None:
    assert SENTINEL not in repr(ElevenLabsSound.for_context(a_context()))


def test_the_base_the_machine_names_is_used_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = answers(monkeypatch)
    sound = SoundProviders(factories=SOUNDS).provider("elevenlabs", a_context(base_url="http://127.0.0.1:9/v1/"))
    sound.effect({"text": "a tap"}, output_format=FORMAT)
    assert asked[0]["url"] == f"http://127.0.0.1:9/v1{SOUND_PATH}?output_format={FORMAT}"


def test_the_endpoint_a_digest_names_is_the_published_one_whatever_base_is_set() -> None:
    """At the default base the published endpoint is the URL every bought sound's digest was taken over."""
    assert PUBLISHED_BASE == ElevenLabsConfig().base_url
    assert endpoint("elevenlabs", SoundKind.EFFECT) == f"{PUBLISHED_BASE}{SOUND_PATH}"
    assert endpoint("elevenlabs", SoundKind.AMBIENCE) == f"{PUBLISHED_BASE}{SOUND_PATH}"
    assert endpoint("elevenlabs", SoundKind.MUSIC) == f"{PUBLISHED_BASE}{MUSIC_PATH}"
