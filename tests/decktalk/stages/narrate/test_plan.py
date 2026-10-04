"""What a take is named by: the digest of a voiced take, and the inputs a placeholder is sized at."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import TakeInputs
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.speech import canonical_text
from decktalk.stages.narrate.plan import placeholder_inputs, take_inputs
from decktalk.stages.narrate.state import take_states
from support.paths import DATA
from support.projects import MINIMAL_TOML, load_project

from .conftest import TOML, VOICE_ID

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
"""The two golden films, with the digest of every take bought for them."""

ROWS = [(row["film"], row["section"], row["markdown"], row["hash"]) for row in GOLDEN["takes"]]
IDS = [f"{film}-{section}" for film, section, _markdown, _digest in ROWS]


@pytest.mark.parametrize(("film", "section", "markdown", "digest"), ROWS, ids=IDS)
def test_the_digest_of_a_paid_take_is_the_one_its_film_was_billed_for(
    film: str, section: int, markdown: str, digest: str
) -> None:
    """A digest that moved means the next voiced run buys that take again.

    `tests/data/take_hash.json` is therefore never updated to make this pass. The whole path is
    measured, from the markdown an author wrote through the script parser to the inputs the plan
    hands the digest, because every one of those steps decides what a take costs.
    """
    (parsed,) = parse_script(markdown)
    inputs = GOLDEN["inputs"]
    made = TakeInputs.of(
        provider=inputs["provider"],
        voice=inputs["voice"],
        model=inputs["model"],
        output_format=inputs["output_format"],
        settings=inputs["settings"],
        text=canonical_text(parsed.pieces),
    )
    assert made.digest == digest, f"{film} section {section} would be voiced again"


def test_a_placeholder_digest_moves_with_the_pace_it_was_sized_at(
    make_inputs: Callable[..., Inputs],
) -> None:
    faster = make_inputs(toml=TOML.replace("lead_seconds = 0.5", "placeholder_words_per_minute = 200"), name="faster")
    plain = make_inputs()
    (section,) = [s for s in plain.spoken() if s.number == 1]
    assert placeholder_inputs(plain, section).digest != placeholder_inputs(faster, section).digest


@pytest.mark.parametrize(("film", "section", "markdown", "digest"), ROWS, ids=IDS)
def test_a_project_at_the_defaults_names_each_paid_take_by_what_elevenlabs_declares(
    tmp_path: Path, film: str, section: int, markdown: str, digest: str
) -> None:
    """What `[elevenlabs]` declares at its defaults, identity and format, is what every take was paid under."""
    golden = GOLDEN["inputs"]
    project = load_project(tmp_path, MINIMAL_TOML, environ={})
    (parsed,) = parse_script(markdown)
    made = take_inputs(project, parsed, provider="elevenlabs", voice_id=golden["voice"], model=golden["model"])
    assert made.output_format == golden["output_format"]
    assert made.digest == digest, f"{film} section {section} would be voiced again"
    assert project.workspace.take_file(digest) == f"{digest}.mp3"


def test_a_voice_a_host_registered_keeps_the_digest_its_takes_were_named_by(
    make_inputs: Callable[..., Inputs],
) -> None:
    """It declares no format, so it is asked for the one every take was asked for before adapters declared theirs."""
    project = make_inputs(toml=TOML.replace('provider = "elevenlabs"', 'provider = "house"'), name="house")
    (section, *_rest) = project.spoken()
    made = take_inputs(project, section, provider="house", voice_id=VOICE_ID, model="m")
    assert made.output_format == "mp3_44100_128"
    assert (
        made.digest
        == TakeInputs.of(
            provider="house",
            voice=VOICE_ID,
            model="m",
            output_format="mp3_44100_128",
            settings={"speed": 1.0},
            text=canonical_text(section.pieces),
        ).digest
    )
    assert project.workspace.take_file(made.digest) == f"{made.digest}.mp3"
    spend = take_states(project, [section]).plan(spend=True).cost
    assert "declares no bill" in spend.sentence
