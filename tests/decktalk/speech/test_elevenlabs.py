"""The real ElevenLabs path: the base that is checked, the reply that is read, and the key that is not printed.

The reply below is the shape `/text-to-speech/{voice}/with-timestamps` answers with, so `synthesize`
and `words_from_alignment` are the real ones and only the socket is stood in for.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error

import pytest

from decktalk.errors import InputError, ProviderError
from decktalk.secret import Secret
from decktalk.speech import PROVIDERS, SpeechRequest, VoiceContext, get_provider
from decktalk.speech import http as _http
from decktalk.speech.elevenlabs import ALLOW_ANY_API_BASE, ElevenLabs, check_api_base, words_from_alignment

SENTINEL = "sk_sentinel_key_that_must_never_print"
VOICE = "Xb7hH8MSUJpSbSDYk0k2"
AUDIO = b"ID3 this is an mp3"
BASE = "https://api.elevenlabs.io/v1"

SPOKEN = 'Hi <break time="0.7s" /> there, world.'
"""What the script asks for, with the break tag the voice honours and never says."""


def alignment(text: str, *, per_char: float = 0.1) -> dict[str, object]:
    """A character alignment of `text` at a fixed pace, which is what the endpoint answers with."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * per_char, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * per_char, 3) for i in range(len(text))],
    }


REPLY = {"audio_base64": base64.b64encode(AUDIO).decode(), "alignment": alignment(SPOKEN)}
"""One canned with-timestamps reply, in the shape and the spelling the service uses."""


class Answer(io.BytesIO):
    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def answers(monkeypatch: pytest.MonkeyPatch, reply: object, *, status: int = 200) -> list[dict[str, object]]:
    """Answer every request with one canned reply, and give back what was asked, headers and all."""
    asked: list[dict[str, object]] = []

    def urlopen(request, *, timeout):  # noqa: ANN001, ANN202  (urllib's own signature)
        asked.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode()),
                "headers": dict(request.headers),
                "timeout": timeout,
            }
        )
        body = json.dumps(reply).encode() if not isinstance(reply, bytes) else reply
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "no", {}, io.BytesIO(body))  # type: ignore[arg-type]
        return Answer(body)

    monkeypatch.setattr(_http, "urlopen", urlopen)
    return asked


def provider(**over: object) -> ElevenLabs:
    """The provider a project with these four values would build, which is how a run builds one."""
    fields: dict[str, object] = {
        "api_key": Secret(SENTINEL, "ELEVENLABS_API_KEY"),
        "api_base": BASE,
        "context_chars": 10,
        "speech_timeout_seconds": 180,
        "sound_timeout_seconds": 30,
        **over,
    }
    return ElevenLabs(**fields)  # type: ignore[arg-type]


def request(**over: object) -> SpeechRequest:
    fields: dict[str, object] = {"text": SPOKEN, "voice_id": VOICE, "model": "eleven_v3", **over}
    return SpeechRequest(**fields)  # type: ignore[arg-type]


# ---- the base -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "https://api.elevenlabs.io/v1",
        "https://api.us.elevenlabs.io/v1",
        "https://ELEVENLABS.IO/v1",
        "https://elevenlabs.io",
    ],
)
def test_an_https_elevenlabs_host_passes(base):
    assert check_api_base(base, environ={}) == base


@pytest.mark.parametrize(
    "base",
    [
        "http://api.elevenlabs.io/v1",  # not https
        "https://evil.test/v1",  # another host
        "https://elevenlabs.io.evil.test/v1",  # the domain as a prefix of another
        "https://notelevenlabs.io/v1",  # the domain as a suffix without its dot
        "https://api.elevenlabs.io@evil.test/v1",  # userinfo that reads like the right host
        "https://evil.test/api.elevenlabs.io/v1",  # the domain in the path
        "api.elevenlabs.io/v1",  # no scheme
        "",
    ],
)
def test_anything_else_is_an_input_error_that_names_the_override(base):
    with pytest.raises(InputError) as caught:
        check_api_base(base, environ={})
    assert ALLOW_ANY_API_BASE in f"{caught.value} {caught.value.hint}"


def test_the_environment_override_allows_any_base():
    assert check_api_base("http://127.0.0.1:8000/v1", environ={ALLOW_ANY_API_BASE: "1"}) == "http://127.0.0.1:8000/v1"
    for off in ("", "0", "false", "no"):
        with pytest.raises(InputError):
            check_api_base("http://127.0.0.1:8000/v1", environ={ALLOW_ANY_API_BASE: off})


def test_the_provider_refuses_a_foreign_base_before_any_request(monkeypatch):
    """The refusal names the rule and the switch, and never the value, which may hold a path token."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    with pytest.raises(InputError) as caught:
        provider(api_base="https://evil.test/v1/SUPERSECRETTOKEN")
    said = f"{caught.value} {caught.value.hint}"
    assert "evil.test" not in said and "SUPERSECRETTOKEN" not in said
    monkeypatch.setenv(ALLOW_ANY_API_BASE, "1")
    assert provider(api_base="https://evil.test/v1").checked_base.endswith("/v1")


# ---- the real synthesize ------------------------------------------------------------------------


def test_one_section_read_aloud_comes_back_as_audio_and_a_time_for_every_word(monkeypatch):
    """The whole reason DeckTalk can cut on a word: the reply's character alignment becomes words."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    asked = answers(monkeypatch, REPLY)
    audio, words = provider().synthesize(request(previous_text="before" * 5, next_text="after" * 5))
    assert audio == AUDIO
    assert [w.word for w in words] == ["Hi", "there", "world"]
    assert words[0].start == 0.0 and words[0].end == 0.2
    # The break tag is skipped rather than spoken, so the next word starts after it.
    assert words[1].start > words[0].end

    sent = asked[0]
    assert sent["url"] == f"{BASE}/text-to-speech/{VOICE}/with-timestamps?output_format=mp3_44100_128"
    assert sent["body"]["text"] == SPOKEN and sent["body"]["model_id"] == "eleven_v3"
    # The neighbours travel trimmed to the tuning, so prosody carries across a cut at a known cost.
    assert sent["body"]["previous_text"] == "beforebefore"[-10:]
    assert sent["body"]["next_text"] == "afterafter"[:10]
    assert sent["timeout"] == 180


def test_the_voice_id_is_a_plain_name_in_the_request_the_url_and_the_hash(monkeypatch):
    """It says which voice read the script, the way a model name says which model did."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    asked = answers(monkeypatch, REPLY)
    speech = provider()
    speech.speak(request())
    assert VOICE in str(asked[0]["url"])
    key = speech.cache_key(request())
    assert key == f"elevenlabs\n{VOICE}\neleven_v3\nmp3_44100_128"
    assert speech.cache_key(request(voice_id="another")) != key


def test_the_normalised_alignment_is_read_when_the_written_one_is_absent(monkeypatch):
    """A service that could not align the text as written still aligned what it spoke."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    answers(monkeypatch, {"audio_base64": base64.b64encode(AUDIO).decode(), "normalized_alignment": alignment("Hi")})
    _audio, words = provider().synthesize(request())
    assert [w.word for w in words] == ["Hi"]


def test_a_reply_with_no_audio_in_it_is_a_provider_failure_rather_than_an_empty_take(monkeypatch):
    """An empty take reads as silence in every later stage, so it is refused where it arrives."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    answers(monkeypatch, {"alignment": alignment("Hi")})
    with pytest.raises(ProviderError) as caught:
        provider().synthesize(request())
    assert caught.value.retryable is True


def test_a_refusal_quotes_the_service_and_never_the_key(monkeypatch):
    """The body is written by whatever host `api_base` names, so it is scrubbed before it is quoted."""
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    answers(monkeypatch, json.dumps({"detail": f"invalid api key {SENTINEL}"}).encode(), status=401)
    with pytest.raises(ProviderError) as caught:
        provider().synthesize(request())
    message = str(caught.value)
    assert SENTINEL not in message and _http.CREDENTIAL in message
    assert "401" in message and caught.value.retryable is False


def test_a_service_that_asked_for_a_slower_pace_is_worth_trying_again(monkeypatch):
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)
    answers(monkeypatch, b'{"detail": "slow down"}', status=429)
    with pytest.raises(ProviderError) as caught:
        provider().synthesize(request())
    assert caught.value.retryable is True


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
        list(rows["characters"]),  # type: ignore[arg-type]
        list(rows["character_start_times_seconds"]),  # type: ignore[arg-type]
        list(rows["character_end_times_seconds"]),  # type: ignore[arg-type]
    )


# ---- the registry -------------------------------------------------------------------------------


def test_the_registry_builds_the_one_provider_decktalk_ships(monkeypatch):
    monkeypatch.delenv(ALLOW_ANY_API_BASE, raising=False)

    class Env:
        def require(self, *names: str) -> list[Secret]:
            return [Secret(SENTINEL, name) for name in names]

    context = VoiceContext(
        secrets=Env(), api_base=BASE, context_chars=1500, speech_timeout_seconds=90, sound_timeout_seconds=30
    )
    speech = get_provider("elevenlabs", context)
    assert speech.name == "elevenlabs" and isinstance(speech, ElevenLabs)
    assert speech.speech_timeout_seconds == 90 and speech.sound_timeout_seconds == 30


def test_a_provider_name_decktalk_does_not_know_is_refused_with_the_ones_it_does():
    context = VoiceContext(
        secrets=None,  # type: ignore[arg-type]
        api_base=BASE,
        context_chars=1,
        speech_timeout_seconds=1,
        sound_timeout_seconds=1,
    )
    with pytest.raises(InputError) as caught:
        get_provider("a-local-voice", context)
    assert "elevenlabs" in f"{caught.value} {caught.value.hint}"
    assert sorted(PROVIDERS) == ["elevenlabs"], "the shipped list is one provider, as the founder decided"
