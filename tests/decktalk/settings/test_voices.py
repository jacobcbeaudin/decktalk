"""The voices a project declares: their names, the default, each voice's keys over its provider's, and the refusals."""

from __future__ import annotations

import itertools
import re
from typing import Any, get_type_hints

import pytest

from decktalk.errors import InputError
from decktalk.settings import (
    TEMPLATES,
    VOICE_NAME,
    DtspConfig,
    ElevenLabsConfig,
    Settings,
    VoiceConfig,
    key_of,
)
from decktalk.settings.layers import key_warnings, load
from decktalk.settings.voices import VOICE_VARIABLE
from decktalk.tomlmap import variable


def voices_of(project: dict[str, Any], environ: dict[str, str] | None = None, overrides: tuple[str, ...] = ()):
    """The voices a project holding these tables resolves to on a machine that sets nothing."""
    return load(machine={}, project=project, environ=environ or {}, overrides=overrides).settings.voices


def refusal(project: dict[str, Any], environ: dict[str, str] | None = None, overrides: tuple[str, ...] = ()):
    """The refusal a project holding these tables earns when it loads."""
    with pytest.raises(InputError) as refused:
        voices_of(project, environ, overrides)
    return refused.value


@pytest.mark.parametrize("name", ["Guest", "guest_2", "guest-2", "2guest"])
def test_a_voice_name_is_a_lower_case_letter_then_lower_case_letters_and_digits(name: str) -> None:
    """Every name spells one shell variable, and no two names spell the same one."""
    refused = refusal({"voices": {"narrator": {}, name: {}}})
    assert str(refused) == (
        f"decktalk.toml: [voices.{name}] is not a voice name, because a voice name is a lower-case letter "
        "followed by lower-case letters and digits."
    )
    assert refused.hint == (
        "Rename the table, such as [voices.guest], and every [voice NAME] line in the script that names it."
    )


@pytest.mark.parametrize("name", ["guest", "host2"])
def test_a_voice_name_of_letters_then_digits_is_read(name: str) -> None:
    voices = voices_of({"narration": {"voice": name}, "voices": {name: {"id": "v"}}})
    assert list(voices) == [name]
    assert re.fullmatch(VOICE_NAME, name)


def test_a_voices_entry_that_is_not_a_table_is_refused_with_the_shape() -> None:
    """A key written straight under [voices] is the likeliest slip, and it names no voice."""
    refused = refusal({"voices": {"provider": "dtsp"}})
    assert str(refused) == (
        "decktalk.toml: [voices] provider is not a table, and [voices] holds one table per voice, such as "
        "[voices.narrator]."
    )


def test_the_default_voice_must_be_one_the_project_declares() -> None:
    refused = refusal({"narration": {"voice": "hots"}, "voices": {"host": {}, "guest": {}}})
    assert str(refused) == (
        "[narration] voice is 'hots' in decktalk.toml, and decktalk.toml declares no [voices.hots] table. "
        "The voices are guest, host. Did you mean 'host'?"
    )
    assert refused.hint == "Name one of the voices in [narration] voice, or write the table it names."


def test_the_default_left_at_its_own_value_names_no_file() -> None:
    """Once any voice table is written, the default needs its own, even when nobody named the default."""
    refused = refusal({"voices": {"host": {}}})
    assert str(refused).startswith(
        "[narration] voice is 'narrator', its default, and decktalk.toml declares no [voices.narrator] table."
    )


def test_a_project_that_declares_no_voice_is_read_by_the_default_at_every_default() -> None:
    voices = voices_of({})
    assert list(voices) == ["narrator"]
    (narrator,) = voices.values()
    assert narrator.name == "narrator"
    assert narrator.own == VoiceConfig()
    assert narrator.table == ElevenLabsConfig()
    assert narrator.stated == ()


def test_the_default_comes_first_and_every_other_voice_by_name() -> None:
    voices = voices_of({"narration": {"voice": "zed"}, "voices": {"guest": {}, "zed": {}, "amy": {}}})
    assert list(voices) == ["zed", "amy", "guest"]


def test_a_voice_sets_a_key_of_its_providers_table_for_itself_alone() -> None:
    voices = voices_of(
        {
            "voices": {"narrator": {"stability": 0.3}, "guest": {"id": "g"}},
            "elevenlabs": {"stability": 0.6},
        }
    )
    assert voices["narrator"].value("stability") == 0.3
    assert voices["narrator"].stated == ("stability",)
    assert voices["guest"].value("stability") == 0.6
    assert voices["guest"].table == ElevenLabsConfig(stability=0.6)


def test_a_key_the_voice_states_beats_its_providers_whatever_the_layer() -> None:
    """Specific beats general: a voice's own value wins over its provider's, even one set by a higher layer."""
    project = {"voices": {"narrator": {"stability": 0.4}, "guest": {}}}
    voices = voices_of(project, {"DECKTALK_ELEVENLABS_STABILITY": "0.2"})
    assert voices["guest"].value("stability") == 0.2
    assert voices["narrator"].value("stability") == 0.4


def test_a_voice_value_is_read_through_its_keys_own_type() -> None:
    """An int and a float name a take apart, so a voice's value meets the type its key declares."""
    voices = voices_of(
        {"voices": {"narrator": {"speed": 1}, "guest": {}}},
        {"DECKTALK_VOICES_GUEST_STABILITY": "0.4"},
    )
    speed = voices["narrator"].value("speed")
    assert speed == 1.0 and isinstance(speed, float)
    assert voices["guest"].value("stability") == 0.4
    refused = refusal({"voices": {"narrator": {}}}, {"DECKTALK_VOICES_NARRATOR_STABILITY": "4"})
    assert "DECKTALK_VOICES_NARRATOR_STABILITY" in str(refused)
    assert "voices.narrator.stability" in str(refused)


def test_a_key_of_another_providers_table_is_refused() -> None:
    refused = refusal({"voices": {"narrator": {"provider": "dtsp", "stability": 0.4}}})
    assert str(refused) == (
        "decktalk.toml: [voices.narrator] stability is a key of [elevenlabs], and the voice narrator is read by "
        "dtsp, which has no such key."
    )
    assert refused.hint == "Take stability out of [voices.narrator], or set its provider to elevenlabs."


@pytest.mark.parametrize(
    ("provider", "key", "value", "text", "hint"),
    [
        (
            "elevenlabs",
            "dollars_per_1000_characters",
            0.3,
            "decktalk.toml: [voices.narrator] dollars_per_1000_characters is what [elevenlabs] charges, which every "
            "voice of elevenlabs is billed at, so it belongs in [elevenlabs] alone.",
            "Move dollars_per_1000_characters into [elevenlabs].",
        ),
        (
            "elevenlabs",
            "base_url",
            "http://elsewhere",
            "decktalk.toml: [voices.narrator] base_url is machine-scoped, so it belongs in this machine's file under "
            "[elevenlabs].",
            "Remove it from decktalk.toml and run decktalk config set elevenlabs.base_url <value> --scope machine.",
        ),
        (
            "dtsp",
            "handshake_seconds",
            9,
            "decktalk.toml: [voices.narrator] handshake_seconds is machine-scoped, so it belongs in this machine's "
            "file under [dtsp].",
            "Remove it from decktalk.toml and run decktalk config set dtsp.handshake_seconds <value> --scope machine.",
        ),
    ],
    ids=["the-rate", "a-base-url", "a-handshake"],
)
def test_the_rate_and_the_machine_keys_belong_to_the_provider_table_alone(
    provider: str, key: str, value: object, text: str, hint: str
) -> None:
    refused = refusal({"voices": {"narrator": {"provider": provider, key: value}}})
    assert str(refused) == text
    assert refused.hint == hint


def test_a_host_voice_states_no_provider_key() -> None:
    refused = refusal({"voices": {"narrator": {"provider": "house", "model": "m"}}})
    assert str(refused) == (
        "decktalk.toml: [voices.narrator] model is a key of a provider table, and the voice narrator is read by "
        "'house', a provider a host registered, which owns no table."
    )
    assert refused.hint == "Take model out of [voices.narrator]."


def test_a_host_voice_has_its_own_keys_and_no_table() -> None:
    (narrator,) = voices_of({"voices": {"narrator": {"provider": "house", "speed": 0.9}}}).values()
    assert narrator.table is None
    assert narrator.value("speed") == 0.9


@pytest.mark.parametrize(
    ("environ", "overrides", "source"),
    [
        ({"DECKTALK_VOICES_GEST_ID": "v"}, (), "DECKTALK_VOICES_GEST_ID"),
        ({}, ("voices.gest.speed=1.1",), "--set voices.gest.speed"),
    ],
    ids=["a-variable", "a-set"],
)
def test_an_override_of_a_voice_the_project_does_not_declare_is_refused(
    environ: dict[str, str], overrides: tuple[str, ...], source: str
) -> None:
    """A renamed voice leaves its variable naming nothing, and a silent override would buy in the wrong voice."""
    refused = refusal({"voices": {"narrator": {}, "guest": {}}}, environ, overrides)
    assert str(refused) == (
        f"{source} sets a key of the voice 'gest', and decktalk.toml declares no [voices.gest] table. "
        "The voices are narrator, guest. Did you mean 'guest'?"
    )
    assert refused.hint == "Unset it, or name a voice the project declares."


def test_an_override_of_a_declared_voice_reaches_it() -> None:
    voices = voices_of(
        {"voices": {"narrator": {}, "guest": {"provider": "dtsp"}}},
        {"DECKTALK_VOICES_GUEST_ID": "af_heart"},
        ("voices.guest.speed=1.1",),
    )
    assert voices["guest"].own == VoiceConfig(provider="dtsp", id="af_heart", speed=1.1)


def test_a_voice_variable_with_no_project_open_is_never_refused() -> None:
    """Doctor and install read the machine alone, which declares no voice, so no variable can name one wrongly."""
    voices = load(machine={}, project={}, environ={"DECKTALK_VOICES_GUEST_ID": "x"}).settings.voices
    assert list(voices) == ["narrator"]


def test_an_unknown_key_in_a_voice_table_is_warned_about_with_the_nearest() -> None:
    said = key_warnings({"voices": {"guest": {"stabilty": 0.4, "id": "g"}}}, "decktalk.toml")
    assert said == ["decktalk.toml: [voices.guest]: ignoring unknown key 'stabilty' (did you mean 'stability'?)."]


def test_a_key_two_tables_share_has_one_type_and_one_range() -> None:
    """A voice's model is one key whichever provider reads it, so it is held to one type and one range."""
    model = key_of("voices.guest.model")
    assert model is not None
    tables = (ElevenLabsConfig, DtspConfig)
    assert [get_type_hints(table)["model"] for table in tables] == [model.annotation] * len(tables)
    assert [table.__dataclass_fields__["model"].metadata["bounds"] for table in tables] == [model.bounds] * len(tables)
    assert model.default is None
    assert model.description == (
        "This voice's own model, over [elevenlabs or dtsp] model, which every voice of that provider reads "
        "unless it sets its own."
    )


def test_the_family_publishes_the_three_own_keys_then_the_chosen_keys_of_each_provider() -> None:
    assert [key.id for key in TEMPLATES] == [
        f"voices.<name>.{name}"
        for name in (
            "provider",
            "id",
            "speed",
            "model",
            "stability",
            "similarity_boost",
            "style",
            "speaker_boost",
            "output_format",
        )
    ]


NAMES = ["a", "speed", "id", "model", "guest2", "stability", "x9"]
"""Seven voice names, some of them the names of keys, which must still spell distinct variables."""


def test_every_voice_variable_reads_back_to_its_voice_and_its_key() -> None:
    """A voice name has no underscore, so the first one after VOICES_ ends the name and starts the key."""
    keys = [key.name for key in TEMPLATES]
    spelled = {variable(f"voices.{name}.{key}"): (name, key) for name, key in itertools.product(NAMES, keys)}
    assert len(spelled) == len(NAMES) * len(keys)
    for name_of, (name, key) in spelled.items():
        found = VOICE_VARIABLE.fullmatch(name_of)
        assert found is not None
        assert (found[1].lower(), found[2].lower()) == (name, key)


def test_a_bare_settings_tree_holds_no_voice_until_it_is_loaded() -> None:
    assert dict(Settings().voices) == {}
