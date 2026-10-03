"""The real ElevenLabs path: the base the machine names, the reply that is read, and the key that is not printed.

The reply below is the shape `/text-to-speech/{voice}/with-timestamps` answers with, so `speak`
and `words_from_alignment` are the real ones and only the socket is stood in for.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from decktalk.errors import InputError, ProviderError
from decktalk.results import BillingBasis
from decktalk.secret import Secret
from decktalk.settings import ElevenLabsConfig, Settings
from decktalk.speech import (
    BEAT,
    HOST_OUTPUT,
    PROVIDERS,
    Billing,
    Output,
    Piece,
    SpeechContext,
    SpeechProvider,
    SpeechProviders,
    SpeechRequest,
    billing_of,
    output_of,
    renders_pauses,
)
from decktalk.speech import http as _http
from decktalk.speech.elevenlabs import (
    BREAK_MODELS,
    ElevenLabs,
    output,
    voice_settings,
    words_from_alignment,
)
from support.paths import DATA
from support.speech import NoSecrets, alignment

SENTINEL = "sk_sentinel_key_that_must_never_print"
VOICE = "Xb7hH8MSUJpSbSDYk0k2"
AUDIO = b"ID3 this is an mp3"
BASE = "https://api.elevenlabs.io/v1"

PIECES = (Piece("Hi", 0.7), Piece("there, world."))
"""What the script asks for: two runs of text with a timed pause between them."""

SPOKEN = 'Hi <break time="0.7s" />\n\nthere, world.'
"""What the voice is sent for those pieces, with the break tag it honours and never says."""

FORMAT = "mp3_44100_128"
"""The format `[elevenlabs] output_format` asks for by default, which every voiced take was bought in."""

MODEL = "eleven_multilingual_v2"
"""A model that reads `<break>`, which is the one a request with a timed pause may name."""


REPLY = {"audio_base64": base64.b64encode(AUDIO).decode(), "alignment": alignment(SPOKEN)}
"""One canned with-timestamps reply, in the shape and the spelling the service uses."""


class Answer(io.BytesIO):
    status = 200


def answers(monkeypatch: pytest.MonkeyPatch, reply: object, *, status: int = 200) -> list[dict[str, Any]]:
    """Answer every request with one canned reply, and give back what was asked, headers and all."""
    asked: list[dict[str, Any]] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> Answer:
        assert isinstance(request.data, bytes), "the client sends its JSON body as bytes"
        asked.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data),
                "headers": dict(request.headers),
                "timeout": timeout,
            }
        )
        body = json.dumps(reply).encode() if not isinstance(reply, bytes) else reply
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "no", Message(), io.BytesIO(body))
        return Answer(body)

    monkeypatch.setattr(_http, "urlopen", urlopen)
    return asked


def provider(**over: object) -> ElevenLabs:
    """The provider a project with these values would build, which is how a run builds one."""
    fields: dict[str, Any] = {
        "secrets": None,
        "base_url": BASE,
        "context_characters": 10,
        "speech_timeout_seconds": 180,
        **over,
    }
    return ElevenLabs(SpeechContext(**fields), Secret(SENTINEL, "ELEVENLABS_API_KEY"))


def request(**over: object) -> SpeechRequest:
    fields: dict[str, Any] = {"pieces": PIECES, "voice_id": VOICE, "model": MODEL, "output_format": FORMAT, **over}
    return SpeechRequest(**fields)


# ---- the base -----------------------------------------------------------------------------------


def test_the_base_url_the_machine_names_is_where_every_request_goes(monkeypatch):
    """The base URL is the machine's alone, so a local mock needs no switch beside it."""
    asked = answers(monkeypatch, REPLY)
    provider(base_url="http://127.0.0.1:8000/v1/").speak(request())
    assert asked[0]["url"].startswith("http://127.0.0.1:8000/v1/text-to-speech/")


# ---- the real speak -----------------------------------------------------------------------------


def test_one_section_read_aloud_comes_back_as_audio_and_a_time_for_every_word(monkeypatch):
    """The whole reason DeckTalk can cut on a word: the reply's character alignment becomes words."""
    asked = answers(monkeypatch, REPLY)
    audio, words = provider().speak(request(previous_text="before" * 5, next_text="after" * 5))
    assert audio == AUDIO
    assert [w.word for w in words] == ["Hi", "there", "world"]
    assert words[0].start == 0.0 and words[0].end == 0.2
    # The break tag is skipped rather than spoken, so the next word starts after it.
    assert words[1].start > words[0].end

    sent = asked[0]
    assert sent["url"] == f"{BASE}/text-to-speech/{VOICE}/with-timestamps?output_format=mp3_44100_128"
    assert sent["body"]["text"] == SPOKEN and sent["body"]["model_id"] == MODEL
    # The neighbours travel trimmed to the tuning, so prosody carries across a cut at a known cost.
    assert sent["body"]["previous_text"] == "beforebefore"[-10:]
    assert sent["body"]["next_text"] == "afterafter"[:10]
    assert sent["timeout"] == 180


@pytest.mark.parametrize("model", ["eleven_v3", "eleven_v4", "eleven_v4_turbo", "a_model_nobody_checked"])
def test_a_timed_pause_is_refused_on_a_model_that_reads_no_break_tag_before_anything_is_sent(monkeypatch, model):
    """v3 and v4 read no `<break>`, so the pause would be dropped after the take was bought."""
    asked = answers(monkeypatch, REPLY)
    with pytest.raises(InputError) as caught:
        provider().speak(request(model=model))
    assert asked == []
    assert model in str(caught.value) and "eleven_multilingual_v2" in (caught.value.hint or "")
    assert not renders_pauses("elevenlabs", model)


def test_a_beat_and_plain_text_are_sent_to_any_model_as_the_canonical_text(monkeypatch):
    """A beat is a dash the voice reads as one, so a model without `<break>` may still read a section with beats."""
    asked = answers(monkeypatch, REPLY)
    provider().speak(request(model="eleven_v3", pieces=(Piece("One", BEAT), Piece("two."))))
    assert asked[0]["body"]["text"] == "One —\n\ntwo."


@pytest.mark.parametrize("model", sorted(BREAK_MODELS))
def test_every_model_that_reads_a_break_tag_is_declared_to_render_a_pause(model):
    assert renders_pauses("elevenlabs", model)


def test_a_provider_a_host_registered_renders_its_own_pauses():
    """DeckTalk cannot say a host's own provider drops a pause, because it is handed the pieces as data."""
    assert renders_pauses("a-host-voice", "eleven_v3")


def test_the_voice_id_is_a_plain_name_in_the_request_and_the_url(monkeypatch):
    """It says which voice read the script, the way a model name says which model did."""
    asked = answers(monkeypatch, REPLY)
    provider().speak(request())
    assert VOICE in str(asked[0]["url"])


def test_no_provider_says_what_its_take_is_named_by():
    """The digest is taken in one place above the boundary, from what the adapter declares, so nothing spells a key."""
    assert "cache_key" not in dir(SpeechProvider)
    assert not hasattr(ElevenLabs, "cache_key")


def test_a_request_with_no_format_asks_the_service_for_its_own_default(monkeypatch):
    asked = answers(monkeypatch, REPLY)
    provider().speak(request(output_format=""))
    assert asked[0]["url"] == f"{BASE}/text-to-speech/{VOICE}/with-timestamps"


def test_elevenlabs_declares_its_format_and_names_a_take_by_the_codec_in_it():
    """The format is the service's token, and its codec is the file's suffix, so an mp3 take is `.mp3`."""
    assert output(ElevenLabsConfig()) == Output(format=FORMAT, suffix=".mp3")
    assert output(ElevenLabsConfig(output_format="mp3_22050_32")).suffix == ".mp3"
    assert output_of(Settings(), "elevenlabs") == Output(format=FORMAT, suffix=".mp3")
    assert output_of(Settings(), "a-host-voice") == HOST_OUTPUT == Output(format=FORMAT, suffix=".mp3")


def test_elevenlabs_declares_it_bills_per_character_at_the_rate_its_own_table_states():
    assert billing_of("elevenlabs") == Billing(BillingBasis.PER_CHARACTER, rate="dollars_per_1000_characters")
    assert hasattr(ElevenLabsConfig(), "dollars_per_1000_characters")
    assert billing_of("a-host-voice").by is BillingBasis.UNDECLARED


def test_the_normalised_alignment_is_read_when_the_written_one_is_absent(monkeypatch):
    """A service that could not align the text as written still aligned what it spoke."""
    answers(monkeypatch, {"audio_base64": base64.b64encode(AUDIO).decode(), "normalized_alignment": alignment("Hi")})
    _audio, words = provider().speak(request())
    assert [w.word for w in words] == ["Hi"]


def test_a_reply_with_no_audio_in_it_is_a_provider_failure_rather_than_an_empty_take(monkeypatch):
    """An empty take reads as silence in every later stage, so it is refused where it arrives."""
    answers(monkeypatch, {"alignment": alignment("Hi")})
    with pytest.raises(ProviderError) as caught:
        provider().speak(request())
    # The service answered and may have charged for it, so nothing asks again and the flag says so.
    assert caught.value.retryable is False


def test_a_refusal_quotes_the_service_and_never_the_key(monkeypatch):
    """The body is written by whatever host `base_url` names, so it is scrubbed before it is quoted."""
    answers(monkeypatch, json.dumps({"detail": f"invalid api key {SENTINEL}"}).encode(), status=401)
    with pytest.raises(ProviderError) as caught:
        provider().speak(request())
    message = str(caught.value)
    assert SENTINEL not in message and _http.CREDENTIAL in message
    assert "401" in message and caught.value.retryable is False


def test_a_service_that_asked_for_a_slower_pace_is_worth_trying_again(monkeypatch):
    answers(monkeypatch, b'{"detail": "slow down"}', status=429)
    with pytest.raises(ProviderError) as caught:
        provider().speak(request())
    assert caught.value.retryable is True


def test_a_busy_voice_is_asked_again_as_often_as_the_provider_was_told(monkeypatch):
    monkeypatch.setattr(_http, "pause", lambda _seconds: None)
    asked = answers(monkeypatch, b'{"detail": "busy"}', status=429)
    with pytest.raises(ProviderError):
        provider(retries=2).speak(request())
    assert len(asked) == 3


def test_the_key_is_revealed_in_one_place_and_survives_no_printing_of_the_provider():
    speech = provider()
    for shown in (repr(speech), str(speech), repr(speech.api_key), str(speech.api_key)):
        assert SENTINEL not in shown
    assert speech._headers()["xi-api-key"] == SENTINEL


# ---- the words ----------------------------------------------------------------------------------


def test_a_break_tag_is_skipped_and_punctuation_is_stripped_from_a_word():
    words = words_from_alignment(*_columns("a, <break/> b."))
    assert [w.word for w in words] == ["a", "b"]


def _columns(text: str) -> tuple[list[str], list[float], list[float]]:
    rows = alignment(text)
    return (
        list(rows["characters"]),
        list(rows["character_start_times_seconds"]),
        list(rows["character_end_times_seconds"]),
    )


# ---- the registry -------------------------------------------------------------------------------


def test_the_registry_builds_the_cloud_voice():

    class Env:
        def require(self, *names: str) -> list[Secret]:
            return [Secret(SENTINEL, name) for name in names]

    context = SpeechContext(secrets=Env(), base_url=BASE, context_characters=1500, speech_timeout_seconds=90)
    speech = SpeechProviders(factories=PROVIDERS).provider("elevenlabs", context)
    assert speech.name == "elevenlabs" and isinstance(speech, ElevenLabs)
    assert speech.context.speech_timeout_seconds == 90


def test_a_provider_name_decktalk_does_not_know_is_refused_with_the_ones_it_does():
    context = SpeechContext(
        secrets=NoSecrets(),
        base_url=BASE,
        context_characters=1,
        speech_timeout_seconds=1,
    )
    with pytest.raises(InputError) as caught:
        SpeechProviders(factories=PROVIDERS).provider("a-local-voice", context)
    assert "dtsp, elevenlabs" in f"{caught.value} {caught.value.hint}"


def test_the_settings_its_table_renders_at_its_defaults_are_the_ones_every_paid_take_was_bought_under():
    """`[elevenlabs]` and `[voice] speed` under the service's names are what the golden digests were taken over."""
    golden = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))["inputs"]
    assert voice_settings(ElevenLabsConfig(), 1.0) == golden["settings"]


def test_the_speaker_boost_key_is_the_one_the_service_reads():
    rendered = voice_settings(ElevenLabsConfig(speaker_boost=False), 1.0)
    assert rendered["use_speaker_boost"] is False
    assert "speaker_boost" not in rendered
