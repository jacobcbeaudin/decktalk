"""The golden take digests a one-voice project's own `decktalk.toml` resolves to, read the way narrate reads it.

`tests/contract/test_take_hash.py` holds the digests of the takes really paid for, built from the
take inputs its data file spells out. This file holds the half of the path that one never reads: a
project on disk, its `decktalk.toml`, its `script.md` and the environment it is opened with, loaded
by `Inputs.load` as a run loads it, and the digest the narrate plan names for each section's take.
A config reshape that changed what a project resolves to would pass that file and still buy every
take again, and it fails here.

`tests/data/config_take_digests.json` holds the project frame, the script, one case per voice a
project can be read in today and the digest of every section under each. The cases cover both
shipped providers at their defaults and tuned, a non-default model and format, a changed speed and
ElevenLabs stability, a section with a beat and a timed pause, two sections with the same text, and
the voice id named by `DECKTALK_VOICE_ID` over the file. The digests were written from the code
they pin and are never updated. A change to how a voice is configured rewrites a case's `config`
and `environ` into the new shape stating the same voice, and leaves its `digests` alone.

Renaming the project, retitling a section or renumbering one moves no digest, and neither does
writing the same values in another key order. Nothing here needs audio, a network or a credential.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomlkit

from decktalk.inputs import Inputs
from decktalk.stages.narrate.state import take_states
from support.paths import DATA

GOLDEN = json.loads((DATA / "config_take_digests.json").read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]
CASE_IDS = [case["case"] for case in CASES]
RENAMES = GOLDEN["renames"]
RENAME_IDS = [rename["rename"] for rename in RENAMES]

PAID = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
"""The digests of the takes really paid for, which the defaults case reaches from a config alone."""


def project_file(case: dict[str, Any], frame: str = GOLDEN["project"]) -> str:
    """The case's `decktalk.toml`: the project and its sections, then the voice it is read in."""
    return f"{frame}\n{case['config']}"


def digests(root: Path, toml: str, script: str, environ: dict[str, str]) -> list[str]:
    """The take each spoken section's run would make, in script order, read as narrate reads the project."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "decktalk.toml").write_text(toml, encoding="utf-8")
    (root / "script.md").write_text(script, encoding="utf-8")
    inputs = Inputs.load(root, environ=environ)
    plan = take_states(inputs).plan(spend=True)
    named = [take.digest for take in plan.takes]
    assert all(isinstance(digest, str) for digest in named), f"a section names no take: {named}"
    return [str(digest) for digest in named]


def reordered(table: dict[str, object]) -> dict[str, object]:
    """The same TOML values with every table's keys in reverse order, and every array left in its own order."""

    def kept(value: object) -> object:
        if isinstance(value, dict):
            return reordered(value)
        if isinstance(value, list):
            return [kept(item) for item in value]
        return value

    return {key: kept(table[key]) for key in reversed(list(table))}


def renamed(text: str, pairs: list[list[str]]) -> str:
    for old, new in pairs:
        assert old in text, f"{old!r} is not in the text it renames"
        text = text.replace(old, new)
    return text


def golden(case: dict[str, Any]) -> list[str]:
    return list(case["digests"].values())


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_project_resolves_to_the_digests_its_takes_were_named_by(case: dict[str, Any], tmp_path: Path):
    """A digest that moved here means a one-voice project buys every take again, so this is never updated."""
    found = digests(tmp_path, project_file(case), GOLDEN["script"], case["environ"])
    assert found == golden(case), f"{case['case']} would be voiced again"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
@pytest.mark.parametrize("rename", RENAMES, ids=RENAME_IDS)
def test_renaming_nothing_the_voice_reads_moves_a_digest(case: dict[str, Any], rename: dict[str, Any], tmp_path: Path):
    """A take is named by what it sounds like, so a renamed project, title or section number buys nothing."""
    toml = project_file(case, renamed(GOLDEN["project"], rename["project"]))
    found = digests(tmp_path, toml, renamed(GOLDEN["script"], rename["script"]), case["environ"])
    assert found == golden(case)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_same_values_in_another_key_order_name_the_same_takes(case: dict[str, Any], tmp_path: Path):
    toml = project_file(case)
    shuffled = tomlkit.dumps(reordered(tomllib.loads(toml)))
    assert shuffled.index("[voice]") < shuffled.index("[project]"), "the rewrite kept the original order"
    assert digests(tmp_path, shuffled, GOLDEN["script"], case["environ"]) == golden(case)


def test_the_golden_cases_cover_every_shape_a_one_voice_project_takes_today():
    """Both providers, tuned and not, the override, and sections that pause and repeat, so none moves unseen."""
    configs = [tomllib.loads(case["config"]) for case in CASES]
    providers = {config["voice"]["provider"] for config in configs}
    assert providers == {"elevenlabs", "dtsp"}
    assert any("speed" in config["voice"] for config in configs)
    assert any("stability" in config.get("elevenlabs", {}) for config in configs)
    assert {name for config in configs for name in ("elevenlabs", "dtsp") if "model" in config.get(name, {})} == (
        providers
    )
    assert sum("DECKTALK_VOICE_ID" in case["environ"] for case in CASES) >= 2
    script = GOLDEN["script"]
    assert "[beat]" in script and "[pause " in script
    for case in CASES:
        named = golden(case)
        assert len(named) == 3 and named[1] == named[2], "the two sections with the same text share one take"
        assert len(set(named)) == 2


def test_the_defaults_reach_the_digest_of_a_take_really_paid_for():
    """The paid film was read in this voice at every default, so its first take is named from the config alone."""
    (defaults,) = [case for case in CASES if case["case"] == "elevenlabs-defaults"]
    first = PAID["takes"][0]
    assert first["markdown"] in GOLDEN["script"]
    assert golden(defaults)[0] == first["hash"]
