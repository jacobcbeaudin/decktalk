"""What a take is named by: the digest of a voiced take, and the inputs a placeholder is sized at."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import TakeInputs, take_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.speech import canonical_text
from decktalk.stages.narrate import plan
from decktalk.stages.narrate.plan import placeholder_inputs, take_inputs
from decktalk.stages.narrate.state import take_states
from support.paths import DATA, SRC, TESTS
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
    project = load_project(tmp_path, MINIMAL_TOML, environ={"DECKTALK_VOICES_NARRATOR_ID": golden["voice"]})
    (parsed,) = parse_script(markdown)
    made = take_inputs(parsed.turns[0], project.voice_of(parsed.turns[0]))
    assert made.output_format == golden["output_format"]
    assert made.model == golden["model"]
    assert made.digest == digest, f"{film} section {section} would be voiced again"
    assert take_file(digest, project.voice_of(parsed.turns[0]).output.suffix) == f"{digest}.mp3"


def test_a_voice_a_host_registered_keeps_the_digest_its_takes_were_named_by(
    make_inputs: Callable[..., Inputs],
) -> None:
    """It declares no format, so it is asked for the one every take was asked for before adapters declared theirs."""
    project = make_inputs(toml=TOML.replace('provider = "elevenlabs"', 'provider = "house"'), name="house")
    (section, *_rest) = project.spoken()
    voice = project.voice_of(section.turns[0])
    made = take_inputs(section.turns[0], voice)
    assert made.output_format == "mp3_44100_128"
    assert (
        made.digest
        == TakeInputs.of(
            provider="house",
            voice=VOICE_ID,
            model="",
            output_format="mp3_44100_128",
            settings={"speed": 1.0},
            text=canonical_text(section.pieces),
        ).digest
    )
    assert take_file(made.digest, voice.output.suffix) == f"{made.digest}.mp3"
    spend = take_states(project, [section]).plan(spend=True).cost
    assert "declares no bill" in spend.sentence


TWO_VOICES = """
[project]
name = "t"

[narration]
voice = "narrator"

[voices.narrator]
id = "voice-one"

[voices.guest]
provider = "dtsp"
id = "af_heart"
speed = 1.1

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

TWO_SECTIONS = "## 1. Open\n\nThe narrator reads this.\n\n## 2. Guest\n\n[voice guest]\n\nThe guest reads this.\n"


def digests_of(project: Inputs) -> list[str | None]:
    """The voiced take each spoken section names, read as narrate reads it."""
    return [state.digest for state in take_states(project).values()]


def test_a_take_is_named_by_its_voices_values_and_never_by_its_name(tmp_path: Path) -> None:
    """Renaming a voice in its table, in `[narration] voice` and in every direction voices nothing."""
    before = load_project(tmp_path / "before", TWO_VOICES, script=TWO_SECTIONS, environ={})
    renamed = TWO_VOICES.replace("narrator", "host").replace("guest", "visitor")
    after = load_project(
        tmp_path / "after", renamed, script=TWO_SECTIONS.replace("[voice guest]", "[voice visitor]"), environ={}
    )
    assert digests_of(after) == digests_of(before)
    assert None not in digests_of(before)
    twins = TWO_VOICES.replace(
        '[voices.guest]\nprovider = "dtsp"\nid = "af_heart"\nspeed = 1.1', '[voices.guest]\nid = "voice-one"'
    )
    shared = load_project(
        tmp_path / "shared",
        twins,
        script="## 1. Open\n\nSame words.\n\n## 2. Guest\n\n[voice guest]\n\nSame words.\n",
        environ={},
    )
    first, second = digests_of(shared)
    assert first == second, "two names with one set of values read one sentence as one take"


def test_a_section_the_guest_reads_is_named_by_the_guests_voice(tmp_path: Path) -> None:
    project = load_project(tmp_path, TWO_VOICES, script=TWO_SECTIONS, environ={})
    guest = project.voices["guest"]
    expected = TakeInputs.of(
        provider="dtsp",
        voice="af_heart",
        model="kokoro-82m",
        output_format=guest.output.format,
        settings={"speed": 1.1},
        text="The guest reads this.",
    )
    assert digests_of(project)[1] == expected.digest


def test_every_name_the_plan_exports_is_read_somewhere_else() -> None:
    """A name the plan publishes and nothing reads is a second spelling of a sentence that can drift from the first."""
    plan_file = SRC / "stages" / "narrate" / "plan.py"
    others = [
        path.read_text(encoding="utf-8") for root in (SRC, TESTS) for path in root.rglob("*.py") if path != plan_file
    ]
    unread = [name for name in plan.__all__ if not any(re.search(rf"\b{name}\b", text) for text in others)]
    assert unread == []
