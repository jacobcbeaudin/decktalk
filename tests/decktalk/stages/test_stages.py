"""What every stage shares: how a selection is read, and what a voice is read from.

These helpers are the only code the stage package holds above its own stages, so what they
promise is held here rather than in each of the twelve modules that call them.

The voice helpers read `[voice]` and the speech provider's own table and never name a vendor. The
voiced-take rule is held here from the other side of `tests/contract/test_take_hash.py`: the settings
and the model a take's digest is taken over, assembled from the provider's own table at its
defaults, are byte for byte the inputs every voiced take was bought under.
"""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.artifacts import TakeInputs
from decktalk.stages import selects
from support.paths import DATA
from support.projects import MINIMAL_TOML, load_project

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))["inputs"]
"""The inputs every voiced take of the golden films was bought under, which no regroup may move."""


def test_a_run_that_names_no_section_selects_every_one() -> None:
    wanted = selects(None)
    assert wanted(1) and wanted(9)


def test_a_run_that_names_sections_selects_those_alone() -> None:
    wanted = selects([3, 5])
    assert wanted(3) and wanted(5)
    assert not wanted(4)


def test_an_empty_selection_selects_no_section() -> None:
    """Only a caller that passes no selection at all means every section, so an empty one never widens."""
    assert not selects([])(2)
    assert not selects(())(1)


# ---- the voice ----------------------------------------------------------------------------------


def test_the_digest_inputs_from_the_providers_table_are_the_ones_every_paid_take_was_bought_under(
    tmp_path: Path,
) -> None:
    """The ElevenLabs fields sit in `[elevenlabs]` and speed in `[voice]`, and the JSON they make has not moved."""
    project = load_project(tmp_path, MINIMAL_TOML, environ={})
    identity = dict(project.voice.identity)
    assert project.voice.model == GOLDEN["model"]
    assert identity == GOLDEN["settings"]
    built = TakeInputs.of(
        provider=project.settings.voice.provider,
        voice=GOLDEN["voice"],
        model=project.voice.model,
        output_format=project.settings.elevenlabs.output_format,
        settings=identity,
        text="hello",
    )
    expected = TakeInputs.of(**GOLDEN, text="hello")
    assert built.settings == expected.settings == json.dumps(GOLDEN["settings"], sort_keys=True)
    assert built.payload == expected.payload


def test_the_providers_own_fields_reach_the_digest_from_its_own_table(tmp_path: Path) -> None:
    project = load_project(tmp_path, MINIMAL_TOML + "\n[elevenlabs]\nstability = 0.4\n[voice]\nspeed = 1.1\n")
    assert dict(project.voice.identity)["stability"] == 0.4
    assert dict(project.voice.identity)["speed"] == 1.1


def test_a_voice_with_no_table_is_sent_no_vendors_model_and_no_vendors_fields(tmp_path: Path) -> None:
    """Changing `[voice] provider` never carries ElevenLabs's model or its fields into another voice's digest."""
    project = load_project(tmp_path, MINIMAL_TOML + '\n[voice]\nprovider = "house"\n', environ={})
    assert project.voice.model == ""
    assert dict(project.voice.identity) == {"speed": 1.0}


def test_the_model_is_the_one_the_providers_own_table_names(tmp_path: Path) -> None:
    project = load_project(tmp_path, MINIMAL_TOML + '\n[elevenlabs]\nmodel = "eleven_turbo_v2_5"\n', environ={})
    assert project.voice.model == "eleven_turbo_v2_5"
