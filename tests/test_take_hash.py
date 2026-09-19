"""Golden take digests of the founder's voiced demo film.

Every ElevenLabs take is paid for once and cached by the digest that `text_hash` computes from the
provider identity (name, voice id, model, output format), the voice settings and the exact text sent
to the voice. The digests here are copied from `lay-demo/v11-voiced/build/narration/takes.json`, with
each section's script markdown and the text `parse_script` makes of it. If any of them changes, the
next `narrate` run pays to re-voice that section, so this test must never be updated without the
founder re-voicing the film. It needs no audio, no network and no API key, and runs in milliseconds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from decktalk.project import Voice
from decktalk.providers.elevenlabs import ElevenLabs
from decktalk.providers.speech import SpeechRequest
from decktalk.settings import ElevenLabsConfig, NarrationConfig
from decktalk.stages.narrate import Segment, parse_script, text_hash

GOLDEN = json.loads((Path(__file__).parent / "data" / "take_hash.json").read_text(encoding="utf-8"))
SECTIONS = GOLDEN["sections"]
IDS = [s["key"] for s in SECTIONS]


def _voice_id() -> str:
    """The founder's voice id, read from the environment so it never sits in the repository."""
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice_id:
        pytest.skip("ELEVENLABS_VOICE_ID is not set, so the take digests cannot be recomputed")
    return voice_id


def provider_key() -> str:
    """The provider part of the payload, built the way `narrate` builds it."""
    provider = ElevenLabs(api_key="", cfg=ElevenLabsConfig(), voice_id=GOLDEN["voice_id"])
    request = SpeechRequest(
        text="",
        model=GOLDEN["model"],
        voice_settings=Voice(**GOLDEN["voice"]).api_settings(),
        output_format=GOLDEN["output_format"],
    )
    return provider.cache_key(request)


def test_fixture_covers_every_voiced_section() -> None:
    assert IDS == ["00", "01", "02", "03", "04", "05", "06", "08", "09"]


def test_provider_key_is_byte_identical() -> None:
    assert provider_key() == f"elevenlabs\n{_voice_id()}\neleven_multilingual_v2\nmp3_44100_128"


def test_voice_settings_are_byte_identical() -> None:
    settings = Voice(**GOLDEN["voice"]).api_settings()
    assert json.dumps(settings, sort_keys=True) == (
        '{"similarity_boost": 0.75, "speed": 1.0, "stability": 0.55, "style": 0.0, "use_speaker_boost": true}'
    )


@pytest.mark.parametrize("section", SECTIONS, ids=IDS)
def test_script_syntax_yields_the_voiced_text(section: dict[str, str]) -> None:
    """The markdown of the film's script still parses to the exact text that was voiced."""
    (segment,) = parse_script(section["markdown"])
    assert segment.key == section["key"]
    assert segment.title == section["title"]
    assert segment.text == section["text"]


@pytest.mark.parametrize("section", SECTIONS, ids=IDS)
def test_take_hash_matches_the_paid_take(section: dict[str, str]) -> None:
    """The digest of the voiced text under the film's voice settings is the one in its take index."""
    segment = Segment(index=int(section["key"]), title=section["title"], slug="x", text=section["text"])
    settings = Voice(**GOLDEN["voice"]).api_settings()
    assert text_hash(segment, NarrationConfig(), provider_key(), settings) == section["hash"]
