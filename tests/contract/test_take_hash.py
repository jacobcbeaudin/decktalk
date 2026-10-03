"""The golden take digests of the founder's own films, held against the script they were voiced from.

`tests/data/take_hash.json` carries, for every take the founder has really paid for, the markdown of
its section, the text that markdown parses to and the digest that text was bought under. So this file
holds the whole path from what an author writes to what names an audio file: the script parser, the
canonical text of its pieces, the take inputs and the digest. If any of them moves, the next
`narrate` run buys that take again.

`tests/data/take_hash_pauses.json` carries the same three columns for sections that pause, from the
gen4 `uv-tutorial` and `halfway` scripts and from synthetic sections that hold every kind of pause a
script can ask for. Its digests were computed by the code from before pauses were data, so they say
what a take of each section is named today, and no change to how pauses are carried may move them.

It no longer skips. The voice id is one of the take inputs and it is a published name rather than a
secret, so it sits in the data file beside the digests it produced, and the digests that protect
every paid take are proved on every machine and in CI rather than on the founder's laptop alone.
Nothing here needs audio, a network or a credential, because a digest is arithmetic over text, and
the one request the ElevenLabs adapter is asked to send goes to a stand-in for the socket.

This file is the one place the golden digests are held, and it reads the markdown rather than the
text, because a take is bought over what the parser makes of what the author wrote.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.request
from typing import Any

import pytest

from decktalk.artifacts.takes import TakeInputs
from decktalk.inputs.script import parse_script
from decktalk.secret import Secret
from decktalk.speech import SpeechRequest, VoiceContext, canonical_text
from decktalk.speech import http as speech_http
from decktalk.speech.elevenlabs import ElevenLabs
from support.paths import DATA
from support.speech import NoSecrets

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
INPUTS = GOLDEN["inputs"]
TAKES = GOLDEN["takes"]
IDS = [f"{take['film']}-{take['section']}" for take in TAKES]

PAUSES = json.loads((DATA / "take_hash_pauses.json").read_text(encoding="utf-8"))["takes"]
PAUSE_IDS = [row["case"] for row in PAUSES]

EVERY = [*TAKES, *PAUSES]
EVERY_ID = [*IDS, *PAUSE_IDS]

EXPECTED_FILMS = ("halfway", "halfway/hero")
"""The two films the founder has really paid to voice, which are the only source of a golden digest."""


def digest_of(text: str, *, voice: str = INPUTS["voice"]) -> str:
    """The digest the founder's takes were bought under, built from the inputs the data file names."""
    return TakeInputs.of(
        provider=INPUTS["provider"],
        voice=voice,
        model=INPUTS["model"],
        output_format=INPUTS["output_format"],
        settings=INPUTS["settings"],
        text=text,
    ).digest


def rendered(markdown: str) -> str:
    """The canonical text of the one section `markdown` holds, which is what its take is named by."""
    (segment,) = parse_script(markdown)
    return canonical_text(segment.pieces)


class Reply(io.BytesIO):
    status = 200


def sent_by_elevenlabs(markdown: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The text the ElevenLabs adapter puts on the wire for this section, read off a stand-in socket."""
    sent: list[dict[str, Any]] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> Reply:  # noqa: ARG001  (the signature urllib calls)
        assert isinstance(request.data, bytes)
        sent.append(json.loads(request.data))
        return Reply(json.dumps({"audio_base64": base64.b64encode(b"mp3").decode(), "alignment": {}}).encode())

    monkeypatch.setattr(speech_http, "urlopen", urlopen)
    context = VoiceContext(
        secrets=NoSecrets(),
        api_base="https://api.elevenlabs.io/v1",
        context_chars=0,
        speech_timeout_seconds=1,
    )
    (segment,) = parse_script(markdown)
    request = SpeechRequest(pieces=segment.pieces, voice_id=INPUTS["voice"], model=INPUTS["model"])
    ElevenLabs(context, Secret("not-a-key", "ELEVENLABS_API_KEY")).speak(request)
    (body,) = sent
    return body["text"]


def test_the_golden_rows_cover_both_films():
    assert {take["film"] for take in TAKES} == set(EXPECTED_FILMS)
    assert len({take["hash"] for take in TAKES}) == len({take["text"] for take in TAKES})


def test_the_pause_rows_hold_every_kind_of_pause():
    """A timed pause, a beat, a hand-written tag and a dash the author wrote, so none can move unseen."""
    texts = "".join(row["text"] for row in PAUSES)
    assert '<break time="3s" />' in texts and '<break time="2.5s" />' in texts and " —\n\n" in texts
    assert any("<break" in row["markdown"] for row in PAUSES)
    assert any(row["case"].startswith("uv-tutorial-") for row in PAUSES)
    assert len({row["hash"] for row in PAUSES}) == len({row["text"] for row in PAUSES})


def test_the_voice_id_is_a_published_name_and_not_a_credential():
    """It sits in a tracked file on purpose: two voices reading one sentence are two different takes."""
    assert INPUTS["voice"] and INPUTS["voice"].isalnum()
    assert digest_of("hello") != digest_of("hello", voice="someone-else")


@pytest.mark.parametrize("take", EVERY, ids=EVERY_ID)
def test_the_markdown_still_parses_to_the_text_that_was_voiced(take: dict[str, Any]):
    """The script parser is the first half of the path, so a change to it re-voices every film."""
    (segment,) = parse_script(take["markdown"])
    assert rendered(take["markdown"]) == take["text"]
    if "section" in take:
        assert segment.index == take["section"]
        assert segment.title == take["title"]


@pytest.mark.parametrize("take", EVERY, ids=EVERY_ID)
def test_the_digest_of_the_parsed_text_is_the_one_that_was_paid_for(take: dict[str, Any]):
    """A digest here that moved means the next run buys that take again, so this may never be updated."""
    assert digest_of(rendered(take["markdown"])) == take["hash"], f"{take} would be voiced again"


@pytest.mark.parametrize("take", EVERY, ids=EVERY_ID)
def test_elevenlabs_is_sent_exactly_the_text_the_take_is_named_by(take: dict[str, Any], monkeypatch):
    """The adapter renders the pauses back into the very text the digest is over, so the bill and the name agree."""
    assert sent_by_elevenlabs(take["markdown"], monkeypatch) == take["text"]


def test_a_break_tag_written_mid_paragraph_is_the_pause_it_names():
    """One way to pause: a hand-written tag parses to the same pieces, text and take as `[pause N]`."""
    by_tag = parse_script('## 1. Mid\n\nThink about it. <break time="1s" /> Then go on.\n')
    by_direction = parse_script("## 1. Mid\n\nThink about it. [pause 1] Then go on.\n")
    assert by_tag[0].pieces == by_direction[0].pieces
    assert rendered('## 1. Mid\n\nThink about it. <break time="1000ms"/> Then go on.\n') == canonical_text(
        by_direction[0].pieces
    )
