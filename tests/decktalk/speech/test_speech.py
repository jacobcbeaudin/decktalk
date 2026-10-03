"""The speech layer's registry, whose voices travel on the run and never in a context variable, and the voice
in force a project is read in.

A context variable is read by whatever thread asks, so one whose default is a provider table hands
the shipped paid voice to any thread a pool started without copying the context. The speech layer
therefore keeps no such variable, and these tests read its source to hold that.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

import decktalk.speech
from decktalk.artifacts.takes import TakeInputs
from decktalk.inputs.script import parse_script
from decktalk.results import BillingBasis
from decktalk.settings import Settings
from decktalk.settings.layers import load, value_of
from decktalk.speech import (
    DECLARED,
    HOST_OUTPUT,
    PROVIDERS,
    Output,
    VoiceInForce,
    canonical_text,
)
from support.paths import DATA, REPO, SRC, TESTS

SPEECH = Path(decktalk.speech.__file__).parent


def module_level_context_vars(path: Path) -> list[str]:
    """Every name a module binds at its top level to a `ContextVar`, by annotation or by value."""
    found: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign):
            spelled = ast.unparse(node.annotation) + (ast.unparse(node.value) if node.value else "")
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            spelled, targets = ast.unparse(node.value), node.targets
        else:
            continue
        if "ContextVar" in spelled:
            found += [f"{path.name}:{ast.unparse(target)}" for target in targets]
    return found


def test_no_module_in_the_speech_layer_holds_a_context_variable() -> None:
    """A provider table reaches a stage on its run, so no thread can be handed one it was never given."""
    held = [name for path in sorted(SPEECH.glob("*.py")) for name in module_level_context_vars(path)]
    assert held == []


ELEVENLABS_IDENTITY = {
    "stability": 0.55,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 1.0,
}
"""What the shipped cloud voice is sent at its defaults, under its own names."""

PROJECTS: dict[str, dict[str, Any]] = {
    "elevenlabs": {},
    "a-model-that-drops-a-pause": {
        "voice": {"speed": 1.1},
        "elevenlabs": {"model": "eleven_v3", "stability": 0.4},
    },
    "a-free-voice": {"voice": {"provider": "dtsp"}},
    "a-host-voice": {"voice": {"provider": "house"}},
}
"""One project of each kind of voice: the cloud voice, a model of it that drops a pause, the free one, a host's."""

EXPECTED: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "provider": "elevenlabs",
        "model": "eleven_multilingual_v2",
        "output": Output("mp3_44100_128", ".mp3"),
        "billing": BillingBasis.PER_CHARACTER,
        "price_key": "elevenlabs.dollars_per_1000_characters",
        "renders_pauses": True,
        "base_url": "https://api.elevenlabs.io/v1",
        "key_variable": "ELEVENLABS_API_KEY",
        "start_hint": "Start the voice server at the address [elevenlabs] base_url names",
        "identity": ELEVENLABS_IDENTITY,
    },
    "a-model-that-drops-a-pause": {
        "provider": "elevenlabs",
        "model": "eleven_v3",
        "output": Output("mp3_44100_128", ".mp3"),
        "billing": BillingBasis.PER_CHARACTER,
        "price_key": "elevenlabs.dollars_per_1000_characters",
        "renders_pauses": False,
        "base_url": "https://api.elevenlabs.io/v1",
        "key_variable": "ELEVENLABS_API_KEY",
        "start_hint": "Start the voice server at the address [elevenlabs] base_url names",
        "identity": {**ELEVENLABS_IDENTITY, "stability": 0.4, "speed": 1.1},
    },
    "a-free-voice": {
        "provider": "dtsp",
        "model": "kokoro-82m",
        "output": Output("mp3", ".mp3"),
        "billing": BillingBasis.FREE,
        "price_key": None,
        "renders_pauses": True,
        "base_url": "http://127.0.0.1:8765",
        "key_variable": None,
        "start_hint": "Start decktalk-voice at the address [dtsp] base_url names",
        "identity": {"speed": 1.0},
    },
    "a-host-voice": {
        "provider": "house",
        "model": "",
        "output": HOST_OUTPUT,
        "billing": BillingBasis.UNDECLARED,
        "price_key": None,
        "renders_pauses": True,
        "base_url": "",
        "key_variable": None,
        "start_hint": "Start the voice [voice] provider = 'house' answers from",
        "identity": {"speed": 1.0},
    },
}
"""What each project's voice in force answers, written out once."""

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))


def settings_of(project: dict[str, Any], **environ: str) -> Settings:
    """The settings a project holding these tables resolves to on a machine that sets nothing."""
    return load(project=project, machine={}, environ=environ).settings


@pytest.mark.parametrize("kind", list(PROJECTS))
def test_the_voice_in_force_answers_each_question_for_each_kind_of_voice(kind: str) -> None:
    """Every field is what the project's settings and the closed set say, with the id the environment names."""
    voice = VoiceInForce.of(settings_of(PROJECTS[kind], DECKTALK_VOICE_ID="v"))
    expected = EXPECTED[kind]
    assert voice.id == "v"
    assert {name: getattr(voice, name) for name in expected if name != "identity"} == {
        name: value for name, value in expected.items() if name != "identity"
    }
    assert dict(voice.identity) == expected["identity"]


@pytest.mark.parametrize("take", GOLDEN["takes"], ids=[f"{t['film']}-{t['section']}" for t in GOLDEN["takes"]])
def test_the_golden_takes_are_named_by_the_voice_in_force_at_the_defaults(take: dict[str, Any]) -> None:
    """A project at the defaults that names the golden voice names every paid take by the digest it was bought under."""
    voice = VoiceInForce.of(settings_of({}, DECKTALK_VOICE_ID=GOLDEN["inputs"]["voice"]))
    (section,) = parse_script(take["markdown"])
    named = TakeInputs.of(
        provider=voice.provider,
        voice=voice.id,
        model=voice.model,
        output_format=voice.output.format,
        settings=dict(voice.identity),
        text=canonical_text(section.pieces),
    )
    assert named.digest == take["hash"]


def test_a_provider_nobody_declared_gets_the_hosts_defaults_and_is_never_refused() -> None:
    """A name outside the closed set is a host's, so the value is built and only a build of it can refuse."""
    voice = VoiceInForce.of(settings_of({"voice": {"provider": "nosuch", "speed": 0.9}}))
    assert (voice.provider, voice.id, voice.model, voice.output) == ("nosuch", "", "", HOST_OUTPUT)
    assert (voice.billing, voice.price_key, voice.key_variable) == (BillingBasis.UNDECLARED, None, None)
    assert dict(voice.identity) == {"speed": 0.9}
    assert voice.renders_pauses
    assert voice.base_url == ""


def test_equal_settings_give_an_equal_voice_whose_identity_cannot_be_changed() -> None:
    """The value is a pure function of the settings, and what a take is named by cannot be edited after."""
    voice = VoiceInForce.of(settings_of({}))
    assert voice == VoiceInForce.of(settings_of({}))
    with pytest.raises(TypeError):
        voice.identity["speed"] = 2.0  # ty: ignore[invalid-assignment]


def test_the_price_key_names_the_rate_the_bill_is_read_at() -> None:
    """The rate is the value at the key the voice names, so cost never learns a table or a rate key."""
    settings = settings_of({"elevenlabs": {"dollars_per_1000_characters": 0.42}})
    voice = VoiceInForce.of(settings)
    assert voice.price_key is not None
    assert value_of(settings, voice.price_key) == settings.elevenlabs.dollars_per_1000_characters == 0.42


SHIPPED_TABLE_WRITE = re.compile(r"setitem\((PROVIDERS|DECLARED)\b|\b(PROVIDERS|DECLARED)\[[^\]]+\]\s*=")
"""A test writing into a shipped voice table, by pytest's `setitem` or by assignment."""


def test_the_shipped_tables_cannot_be_changed_in_place() -> None:
    """A fake voice reaches a run on its machine, so no test can leave a shipped table changed for the next."""
    with pytest.raises(TypeError):
        PROVIDERS["elevenlabs"] = PROVIDERS["dtsp"]  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        DECLARED["dtsp"] = DECLARED["elevenlabs"]  # ty: ignore[invalid-assignment]


def test_no_test_patches_the_shipped_voice_tables() -> None:
    """A write into a read-only table fails inside pytest's undo, far from its cause, so none is written."""
    here = Path(__file__).resolve()
    writes = [
        f"{path.relative_to(REPO)}:{number}"
        for path in sorted(TESTS.rglob("*.py"))
        if path.resolve() != here
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if SHIPPED_TABLE_WRITE.search(line)
    ]
    assert writes == []


VOICE_TABLE_READ = re.compile(r"settings\.voice\b")
"""A read of the project's `[voice]` table, by any attribute or by binding the table itself."""


def voice_table_reads(src: Path) -> dict[str, list[int]]:
    """Every module under `src` outside the settings package that reads `[voice]`, with the lines that do."""
    reads: dict[str, list[int]] = {}
    for path in sorted(src.rglob("*.py")):
        if path.relative_to(src).parts[0] == "settings":
            continue
        lines = [
            n
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if VOICE_TABLE_READ.search(line)
        ]
        if lines:
            reads[path.relative_to(src).as_posix()] = lines
    return reads


def test_only_the_voice_in_force_reads_the_voice_table() -> None:
    """Every reader asks the value a project resolved once, so no two readers can resolve `[voice]` apart."""
    reads = voice_table_reads(SRC)
    assert list(reads) == ["speech/__init__.py"]
    (of,) = [
        node
        for node in ast.walk(ast.parse((SRC / "speech" / "__init__.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "of"
    ]
    assert of.end_lineno is not None
    assert [line for line in reads["speech/__init__.py"] if not of.lineno <= line <= of.end_lineno] == []
