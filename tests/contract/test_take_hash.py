"""The golden take digests of the founder's own films, held against the script they were voiced from.

`tests/data/take_hash.json` carries, for every take the founder has really paid for, the markdown of
its section, the text that markdown parses to and the digest that text was bought under. So this file
holds the whole path from what an author writes to what names an audio file: the script parser, the
take inputs and the digest. If any of them moves, the next `narrate` run buys that take again.

It no longer skips. The voice id is one of the take inputs and it is a published name rather than a
secret, so it sits in the data file beside the digests it produced, and the digests that protect
every paid take are proved on every machine and in CI rather than on the founder's laptop alone.
Nothing here needs audio, a network or a credential, because a digest is arithmetic over text.

`tests/decktalk/artifacts/test_takes.py` holds the same digests against `TakeInputs` alone. This file
is the other half of the pair, and the half that reads the markdown, because a take is bought over
what the parser makes of what the author wrote.
"""

from __future__ import annotations

import json

import pytest

from decktalk.artifacts.takes import TakeInputs
from decktalk.inputs.script import parse_script
from support.paths import DATA

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
INPUTS = GOLDEN["inputs"]
TAKES = GOLDEN["takes"]
IDS = [f"{take['film']}-{take['section']}" for take in TAKES]

EXPECTED_FILMS = ("halfway", "halfway/hero")
"""The two films the founder has really paid to voice, which are the only source of a golden digest."""


def digest_of(text: str) -> str:
    """The digest the founder's takes were bought under, built from the inputs the data file names."""
    return TakeInputs.of(
        provider=INPUTS["provider"],
        voice=INPUTS["voice"],
        model=INPUTS["model"],
        output_format=INPUTS["output_format"],
        settings=INPUTS["settings"],
        text=text,
    ).digest


def test_the_data_file_says_what_it_is_for():
    """The file is never edited to make a test pass, so it carries the sentence that says so."""
    assert GOLDEN["source"].endswith("."), GOLDEN["source"]
    assert "never updated to make a test pass" in GOLDEN["rule"]


def test_the_golden_rows_cover_both_films():
    assert {take["film"] for take in TAKES} == set(EXPECTED_FILMS)
    assert len({take["hash"] for take in TAKES}) == len({take["text"] for take in TAKES})


def test_the_voice_id_is_a_published_name_and_not_a_credential():
    """It sits in a tracked file on purpose: two voices reading one sentence are two different takes."""
    assert INPUTS["voice"] and INPUTS["voice"].isalnum()
    assert digest_of("hello") != TakeInputs.of(**{**INPUTS, "voice": "someone-else", "text": "hello"}).digest


@pytest.mark.parametrize("take", TAKES, ids=IDS)
def test_the_markdown_still_parses_to_the_text_that_was_voiced(take: dict[str, object]):
    """The script parser is the first half of the path, so a change to it re-voices every film."""
    (segment,) = parse_script(str(take["markdown"]))
    assert segment.index == take["section"]
    assert segment.title == take["title"]
    assert segment.text == take["text"]


@pytest.mark.parametrize("take", TAKES, ids=IDS)
def test_the_digest_of_the_parsed_text_is_the_one_that_was_paid_for(take: dict[str, object]):
    """A digest here that moved means the next run buys that take again, so this may never be updated."""
    (segment,) = parse_script(str(take["markdown"]))
    assert digest_of(segment.text) == take["hash"], f"{take['film']} section {take['section']} would be voiced again"
