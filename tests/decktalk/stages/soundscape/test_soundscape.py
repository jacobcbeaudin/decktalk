"""The stage that buys the music, the ambience bed and the effects a project describes.

Every request here is money, so what these tests hold is the gate and the ledger: a run that nobody
approved buys nothing, a request that has not moved is not sent again, every file lands under the
workspace, and the record of what was bought is written after every paid call.

The service is a fake sound provider, either handed to the stage at `client_for` or registered in
the run's own sound table, so the requests a run would send are read here rather than sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from decktalk.errors import ApprovalRequired, Cancelled, InputError
from decktalk.events import Event, Progress, SoundCharged, Unit
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media import audio
from decktalk.pipeline import Stage
from decktalk.results import Billing, Layer, SoundKind, SoundStatus, SpendState
from decktalk.speech.sound import SoundContext
from decktalk.stages import soundscape as stage
from decktalk.stages.soundscape import ledger as ledger_module
from decktalk.stages.soundscape import soundscape
from decktalk.stages.soundscape.ledger import LEDGER_FILE, Ledger
from support.fakes import FakeVoice
from support.projects import write_project
from support.runs import a_run, a_sounding_run, a_voiced_run

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
ambience = true

[[section]]
number = 2
page = "deck/index.html"
scene = "2"

[mix]
music = "build/soundscape/music.mp3"

[[mix.effects]]
file = "build/soundscape/chime.mp3"
section = 1
cue = "1.1:open"

[soundscape.ambience]
text = "a quiet room"

[soundscape.effects.chime]
text = "a bright chime"

[soundscape.music]
prompt = "warm strings"
duration_seconds = 30
"""

NOTHING_TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
"""


@dataclass
class FakeService:
    """A sound provider, with every request it was sent and the bytes it answered with."""

    name: str = "house"
    sounds: list[dict[str, Any]] = field(default_factory=list)
    music_bodies: list[dict[str, Any]] = field(default_factory=list)
    answer: bytes = b"audio"

    def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        self.sounds.append(dict(body))
        return self.answer

    def music(self, body: Mapping[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        self.music_bodies.append(dict(body))
        return self.answer


def an_inputs(root: Path, toml: str = TOML) -> Inputs:
    write_project(root, toml)
    return Inputs.load(root, environ={})


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    """The sound service faked at the one seam the stage builds it through."""
    fake = FakeService()
    monkeypatch.setattr(stage, "client_for", lambda _run, _inputs: fake)
    return fake


@pytest.fixture
def joined(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[Path], Path]]:
    """Every crossfade join the stage asked for, with the file it wrote in place of ffmpeg's."""
    calls: list[tuple[list[Path], Path]] = []

    def join(parts: list[Path], out: Path, **_kwargs: object) -> None:
        calls.append((list(parts), out))
        out.write_bytes(b"joined")

    monkeypatch.setattr(audio, "crossfade_join", join)
    return calls


# ---- what the run plans -----------------------------------------------------------------------


def test_a_project_with_no_soundscape_generates_nothing_and_says_so(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, NOTHING_TOML)
    run = a_run(tmp_path)
    said: list[Event] = []
    run.machine.events.subscribe(said.append)
    result = soundscape(inputs, run)
    assert result.items == ()
    assert result.spend.characters == 0
    assert any("nothing to generate" in getattr(line, "message", "") for line in said)


def test_the_items_are_the_ambience_the_effects_and_the_music_in_the_order_the_table_declares_them(
    tmp_path: Path,
) -> None:
    result = soundscape(an_inputs(tmp_path), a_run(tmp_path))
    assert [(item.name, item.kind) for item in result.items] == [
        ("ambience", SoundKind.AMBIENCE),
        ("chime", SoundKind.EFFECT),
        ("music", SoundKind.MUSIC),
    ]


def test_a_run_nobody_approved_plans_every_item_and_writes_nothing(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path)
    result = soundscape(inputs, a_run(tmp_path))
    assert {item.status for item in result.items} == {SoundStatus.PLANNED}
    assert result.written == ()
    assert not (inputs.workspace.soundscape_dir / LEDGER_FILE).exists()


def test_an_item_that_is_only_planned_and_has_no_audio_is_a_missing_file(tmp_path: Path) -> None:
    result = soundscape(an_inputs(tmp_path), a_run(tmp_path))
    assert {found.code for found in result.findings} == {Code.FILE_MISSING}
    assert not result.ok
    first = result.findings[0]
    assert first.location.where == "ambience"
    assert first.location.file == Path("build/soundscape/ambience.mp3")
    assert "25 seconds of audio" in first.message


RATED = (
    TOML.replace(
        "[soundscape.ambience]\n",
        "[soundscape.ambience]\nprice_per_minute = 0.6\n",
    )
    .replace(
        "[soundscape.effects.chime]",
        "[soundscape.effects]\nprice_per_minute = 1.2\n\n[soundscape.effects.chime]",
    )
    .replace(
        "duration_seconds = 30\n",
        "duration_seconds = 30\nprice_per_minute = 0.3\n",
    )
)
"""The test project with a rate stated for each kind: a cent, two cents and half a cent a second."""

EFFECT = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[soundscape.effects]
price_per_minute = 0.12

[soundscape.effects.whoosh]
text = "a whoosh"
duration_seconds = 10
"""
"""One ten-second effect at twelve cents a minute, which is two cents."""


def test_a_ten_second_effect_is_priced_per_second_at_the_rate_its_table_states(tmp_path: Path) -> None:
    priced = stage.price(an_inputs(tmp_path, EFFECT))
    assert priced.dollars == priced.ceiling_dollars == 0.02
    assert (priced.billing, priced.seconds, priced.characters) == (Billing.PER_SECOND, 10.0, 0)
    assert priced.price_per_second == pytest.approx(0.002)
    assert (priced.price_key, priced.price_layer, priced.averaged) == (
        "soundscape.effects.price_per_minute",
        Layer.PROJECT,
        False,
    )
    assert priced.sections == (1,)
    assert priced.sentence == "This run costs $0.02 for about 10 seconds of audio at $0.002 per second of audio."


def test_kinds_bought_at_different_rates_are_priced_exactly_and_said_to_average(tmp_path: Path) -> None:
    """The sum is exact, and the one rate a price states is what that sum comes to per second, said as an average."""
    priced = soundscape(an_inputs(tmp_path, RATED), a_run(tmp_path)).spend
    assert priced.seconds == 55.5
    assert priced.averaged
    assert priced.price_per_second * priced.seconds == pytest.approx(0.41)
    assert priced.sentence == (
        "This run costs $0.41 for about 56 seconds of audio at an average of $0.007387 per second of audio."
    )


def test_a_cap_over_a_rate_nobody_stated_names_that_rates_key(tmp_path: Path) -> None:
    unstated = RATED.replace("duration_seconds = 30\nprice_per_minute = 0.3\n", "duration_seconds = 30\n")
    with pytest.raises(ApprovalRequired) as refused:
        soundscape(an_inputs(tmp_path, unstated), a_run(tmp_path, spend=True, max_cost=5.0))
    assert "soundscape.music.price_per_minute" in (refused.value.hint or "")


@pytest.mark.usefixtures("fake_ffmpeg")
def test_sound_is_asked_for_in_its_own_format(tmp_path: Path) -> None:
    """The format is the soundscape's own key, so a take's format moving never changes what a sound is sent."""
    formats: list[str] = []

    @dataclass
    class Formats(FakeService):
        def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
            formats.append(output_format)
            return super().effect(body, output_format=output_format)

    toml = EFFECT.replace("[soundscape.effects]\n", '[soundscape]\nformat = "mp3_22050_32"\n\n[soundscape.effects]\n')
    toml = toml.replace("[project]", '[elevenlabs]\noutput_format = "mp3_44100_192"\n\n[project]', 1)
    fake = Formats()
    soundscape(an_inputs(tmp_path, toml), a_sounding_run(tmp_path, {"elevenlabs": lambda _context: fake}, spend=True))
    assert formats == ["mp3_22050_32"]


def test_the_format_narration_used_to_hold_is_pointed_at_the_voices_own_key(tmp_path: Path) -> None:
    """A project that set the take format under `[narration]` is told where it lives now.

    Read in silence it would leave every take at the voice's default format and buy each one again.
    """
    toml = EFFECT.replace("[project]", '[narration]\noutput_format = "mp3_22050_32"\n\n[project]', 1)
    inputs = an_inputs(tmp_path, toml)
    assert any(
        "[narration]: ignoring unknown key 'output_format' (did you mean 'elevenlabs.output_format'?)" in note
        for note in inputs.notes
    ), inputs.notes


def test_the_price_is_every_requested_second_at_its_own_kinds_rate(tmp_path: Path) -> None:
    """25 s of ambience at a cent, 0.5 s of effect at two cents and 30 s of music at half a cent."""
    result = soundscape(an_inputs(tmp_path, RATED), a_run(tmp_path))
    assert result.spend.dollars == result.spend.ceiling_dollars == round(0.25 + 0.01 + 0.15, 2)
    assert result.spend.state is SpendState.ESTIMATE
    assert result.spend.sections == (1, 2)


def test_what_speech_costs_prices_no_sound(tmp_path: Path) -> None:
    """Sound is billed by the second of audio, so the speech rate per character is never read for it."""
    toml = TOML.replace(
        "[soundscape.ambience]", "[elevenlabs]\nprice_per_1000_characters = 100.0\n\n[soundscape.ambience]"
    )
    result = soundscape(an_inputs(tmp_path, toml), a_run(tmp_path))
    assert result.spend.dollars == 0
    assert result.spend.price_layer is Layer.DEFAULT


def test_a_rate_one_kind_leaves_unstated_is_a_price_nobody_stated(tmp_path: Path) -> None:
    """A cap may not guard a run whose every rate is not stated, so one default rate makes the price a default."""
    stated = RATED.replace("price_per_minute = 0.3\n", "")
    assert soundscape(an_inputs(tmp_path, stated), a_run(tmp_path)).spend.price_layer is Layer.DEFAULT
    assert soundscape(an_inputs(tmp_path, RATED), a_run(tmp_path)).spend.price_layer is Layer.PROJECT


def test_a_price_no_layer_records_is_the_default_price_and_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer table may hold no row for the price, which narrate and build already read as the default."""
    inputs = an_inputs(tmp_path)

    def unstated(_layers: object, key: str) -> None:
        raise KeyError(key)

    monkeypatch.setattr(type(inputs.layers), "winner", unstated)
    assert soundscape(inputs, a_run(tmp_path)).spend.price_layer is Layer.DEFAULT


# ---- what the run buys ------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_paid_run_buys_every_item_and_writes_it_under_the_workspace(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    result = soundscape(inputs, a_run(tmp_path, spend=True))
    assert {item.status for item in result.items} == {SoundStatus.GENERATED}
    assert len(service.sounds) == 2
    assert len(service.music_bodies) == 1
    assert (inputs.workspace.soundscape_dir / "ambience.mp3").is_file()
    assert (inputs.workspace.soundscape_dir / "chime.mp3").is_file()
    assert Path("build/soundscape/ledger.json") in result.written


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_run_that_may_not_spend_buys_no_sound_whatever_sound_costs(tmp_path: Path, service: FakeService) -> None:
    """Only the run's own spend lets it buy, so a sound somebody stated is free is still not bought without it."""
    free = RATED.replace("0.6", "0").replace("1.2", "0").replace("0.3", "0")
    result = soundscape(an_inputs(tmp_path, free), a_run(tmp_path))
    assert {item.status for item in result.items} == {SoundStatus.PLANNED}
    assert service.sounds == []


def test_a_price_opens_no_run_and_builds_no_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_run: object, _inputs: Inputs) -> None:
        raise AssertionError("pricing built the sound service")

    monkeypatch.setattr(stage, "client_for", refuse)
    inputs = an_inputs(tmp_path)
    priced = stage.price(inputs)
    assert priced.buys
    assert priced.sections == (1, 2)
    assert not inputs.workspace.soundscape_dir.exists()


def test_a_run_with_nothing_left_to_buy_covers_no_section(tmp_path: Path) -> None:
    """A price that covers no section is how a caller learns the run has nothing to ask about."""
    inputs = an_inputs(tmp_path)
    assert soundscape(inputs, a_run(tmp_path)).spend.buys
    assert not stage.spend_of(inputs, [], None).buys


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ambience_request_asks_for_audio_that_meets_its_own_end(tmp_path: Path, service: FakeService) -> None:
    soundscape(an_inputs(tmp_path), a_run(tmp_path, spend=True))
    assert service.sounds[0]["loop"] is True
    assert "loop" not in service.sounds[1]


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_second_run_keeps_every_item_whose_request_has_not_moved(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, spend=True))
    sent = len(service.sounds) + len(service.music_bodies)
    again = soundscape(inputs, a_run(tmp_path, spend=True))
    assert {item.status for item in again.items} == {SoundStatus.KEPT}
    assert len(service.sounds) + len(service.music_bodies) == sent


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_an_item_whose_prompt_moved_is_bought_again_and_the_rest_are_kept(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, spend=True))
    service.sounds.clear()
    moved = an_inputs(tmp_path, TOML.replace("a bright chime", "a dull chime"))
    again = soundscape(moved, a_run(tmp_path, spend=True))
    statuses = {item.name: item.status for item in again.items}
    assert statuses["chime"] is SoundStatus.GENERATED
    assert statuses["ambience"] is SoundStatus.KEPT
    assert [body["text"] for body in service.sounds] == ["a dull chime"]


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_force_buys_every_item_again_although_nothing_moved(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, spend=True))
    service.sounds.clear()
    again = soundscape(inputs, a_run(tmp_path, spend=True), force=True)
    assert {item.status for item in again.items} == {SoundStatus.GENERATED}
    assert len(service.sounds) == 2


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ledger_records_what_each_item_was_bought_with(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, spend=True))
    ledger = Ledger.read(inputs.workspace.soundscape_dir / LEDGER_FILE)
    assert ledger is not None
    assert {row.name for row in ledger.items} == {"ambience", "chime", "music"}
    chime = ledger.of("chime")
    assert chime is not None and "a bright chime" in chime.request
    assert service.sounds


@pytest.mark.usefixtures("fake_ffmpeg")
def test_the_music_is_asked_for_in_chunks_and_joined_into_one_bed(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """The service writes at most one chunk in an answer, so a longer bed is bought in parts."""
    toml = TOML.replace("duration_seconds = 30", "duration_seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    soundscape(inputs, a_run(tmp_path, spend=True))
    assert len(service.music_bodies) > 1
    parts, out = joined[0]
    assert len(parts) == len(service.music_bodies)
    assert out == inputs.workspace.soundscape_dir / "music.mp3"


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_music_part_that_was_already_bought_is_not_bought_again(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    toml = TOML.replace("duration_seconds = 30", "duration_seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    soundscape(inputs, a_run(tmp_path, spend=True))
    sent = len(service.music_bodies)
    (inputs.workspace.soundscape_dir / "music.mp3").unlink()
    soundscape(inputs, a_run(tmp_path, spend=True))
    assert len(service.music_bodies) == sent
    assert len(joined) == 2


# ---- the gate and the stream ------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_a_ceiling_over_a_price_nobody_stated_refuses_the_run_before_it_buys(tmp_path: Path) -> None:
    """The rate is still the default, so a cap would be guarding a price DeckTalk invented."""
    inputs = an_inputs(tmp_path)
    with pytest.raises(ApprovalRequired):
        soundscape(inputs, a_run(tmp_path, spend=True, max_cost=1.0))
    assert not (inputs.workspace.soundscape_dir / "ambience.mp3").exists()


def test_a_cancelled_run_stops_before_it_reaches_the_first_item(tmp_path: Path) -> None:
    run = a_run(tmp_path)
    run.cancel.cancel()
    with pytest.raises(Cancelled):
        soundscape(an_inputs(tmp_path), run)


def test_one_progress_line_is_reported_for_every_asset(tmp_path: Path) -> None:
    run = a_run(tmp_path)
    seen: list[Progress] = []
    run.machine.events.subscribe(lambda line: seen.append(line) if isinstance(line, Progress) else None)
    soundscape(an_inputs(tmp_path), run)
    assert [line.label for line in seen] == ["ambience", "chime", "music", "soundscape"]
    assert {line.unit for line in seen} == {Unit.ASSET}
    assert {line.stage for line in seen} == {Stage.SOUNDSCAPE}
    assert seen[-1].done == seen[-1].total == 3


# ---- what a run selects -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("only", "names"),
    [
        pytest.param([1], {"ambience", "chime", "music"}, id="the effects and the bed cued in the section named"),
        # The ambience bed and the chime are cued in section one, and the music plays under any section.
        pytest.param([2], {"music"}, id="an effect cued in another section is left out"),
        pytest.param([7], set(), id="no section selected wants nothing"),
    ],
)
def test_only_keeps_what_the_sections_it_names_ask_for(tmp_path: Path, only: list[int], names: set[str]) -> None:
    result = soundscape(an_inputs(tmp_path), a_run(tmp_path), only=only)
    assert {item.name for item in result.items} == names


# ---- where the files go -----------------------------------------------------------------------


def test_every_default_path_is_the_workspace(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, TOML.replace('music = "build/soundscape/music.mp3"\n', ""))
    planned = stage.plan_items(inputs)
    assert {item.out.parent for item in planned} == {inputs.workspace.soundscape_dir}


def test_an_item_that_names_its_own_file_is_written_where_the_project_says(tmp_path: Path) -> None:
    toml = TOML.replace(
        '[soundscape.effects.chime]\ntext = "a bright chime"',
        '[soundscape.effects.chime]\ntext = "a bright chime"\nout = "media/chime.mp3"',
    )
    inputs = an_inputs(tmp_path, toml)
    chime = next(item for item in stage.plan_items(inputs) if item.name == "chime")
    assert chime.out == tmp_path / "media" / "chime.mp3"


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
@pytest.mark.parametrize(
    "written",
    [
        pytest.param("{not json", id="corrupt"),
        pytest.param('{"version": 1, "items": []}', id="older-shape"),
    ],
)
def test_a_ledger_that_does_not_read_is_refused_with_a_sentence_and_left_on_disk(
    tmp_path: Path, service: FakeService, written: str
) -> None:
    """The ledger is what this project paid for, so neither a rebuild nor a delete may decide to buy it again."""
    inputs = an_inputs(tmp_path)
    path = inputs.workspace.soundscape_dir / LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written, encoding="utf-8")
    with pytest.raises(InputError) as refused:
        soundscape(inputs, a_run(tmp_path, spend=True))
    assert "ledger.json" in str(refused.value) and "paid for" in str(refused.value)
    hint = refused.value.hint or ""
    assert not hint.startswith("Delete") and "buys again" in hint
    assert path.read_text(encoding="utf-8") == written
    assert not service.sounds and not service.music_bodies


# ---- the service a run buys from ----------------------------------------------------------------


def test_the_sound_provider_is_the_one_the_runs_machine_holds(tmp_path: Path) -> None:
    """The provider is looked up by name in the run's own sound table, so a host's table is the one billed."""
    made: list[tuple[FakeService, SoundContext]] = []

    def house(context: SoundContext) -> FakeService:
        made.append((FakeService(), context))
        return made[-1][0]

    run = a_sounding_run(tmp_path, {"elevenlabs": house}, spend=True)
    client = stage.client_for(run, an_inputs(tmp_path))
    ((built, context),) = made
    assert client is built
    assert context.allow_any_api_base is False
    assert context.timeout_seconds == 600


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_paid_run_buys_through_the_sound_provider_its_machine_holds(tmp_path: Path) -> None:
    """No class is checked: whatever the table answers with is asked for the effects and the music."""
    fake = FakeService()
    run = a_sounding_run(tmp_path, {"elevenlabs": lambda _context: fake}, spend=True)
    result = soundscape(an_inputs(tmp_path), run)
    assert {item.status for item in result.items} == {SoundStatus.GENERATED}
    assert [body["text"] for body in fake.sounds] == ["a quiet room", "a bright chime"]
    assert [body["prompt"] for body in fake.music_bodies] == ["warm strings"]


def test_the_sound_provider_is_the_one_the_soundscape_table_names(tmp_path: Path) -> None:
    toml = TOML.replace("[soundscape.ambience]", '[soundscape]\nprovider = "house"\n\n[soundscape.ambience]')
    fake = FakeService()
    run = a_sounding_run(tmp_path, {"house": lambda _context: fake}, spend=True)
    assert stage.client_for(run, an_inputs(tmp_path, toml)) is fake


def test_a_machine_whose_host_named_its_voices_and_no_sounds_has_no_sound_provider(tmp_path: Path) -> None:
    """A host that handed its machine a voice table reaches no shipped sound provider by a name it left out."""
    run = a_voiced_run(tmp_path, {"elevenlabs": lambda _context: FakeVoice()}, spend=True)
    with pytest.raises(InputError, match="not a sound provider this machine answers for"):
        stage.client_for(run, an_inputs(tmp_path))


def test_the_stage_names_no_vendor_and_checks_no_class() -> None:
    """Sound is its own seam, so the stage asks its table by name and never looks at what came back."""
    for module in (stage, ledger_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert "elevenlabs" not in source.lower(), module.__name__
        assert "isinstance" not in source, module.__name__


# ---- what a paid run charges ------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_every_item_bought_is_charged_as_its_own_event_and_the_spend_is_marked_charged(tmp_path: Path) -> None:
    lines: list[Event] = []
    result = soundscape(an_inputs(tmp_path, RATED), a_run(tmp_path, spend=True, lines=lines))
    charged = [line for line in lines if isinstance(line, SoundCharged)]
    assert [(line.item, line.sound, line.seconds) for line in charged] == [
        ("ambience", SoundKind.AMBIENCE, 25.0),
        ("chime", SoundKind.EFFECT, 0.5),
        ("music", SoundKind.MUSIC, 30.0),
    ]
    assert [line.dollars for line in charged] == pytest.approx([0.25, 0.01, 0.15])
    assert result.spend.state is SpendState.CHARGED
    assert result.spend.dollars == 0.41


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_a_run_that_keeps_every_item_charges_nothing(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, RATED)
    soundscape(inputs, a_run(tmp_path, spend=True))
    lines: list[Event] = []
    again = soundscape(inputs, a_run(tmp_path, spend=True, lines=lines))
    assert [line for line in lines if isinstance(line, SoundCharged)] == []
    assert again.spend.state is SpendState.ESTIMATE
    assert again.spend.dollars == 0


@pytest.mark.usefixtures("fake_ffmpeg")
def test_each_music_part_is_charged_as_it_is_bought(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """A part is paid for the moment the service answers, so a run stopped half way has charged each part it bought."""
    lines: list[Event] = []
    toml = RATED.replace("duration_seconds = 30", "duration_seconds = 600")
    soundscape(an_inputs(tmp_path, toml), a_run(tmp_path, spend=True, lines=lines))
    parts = [line for line in lines if isinstance(line, SoundCharged) and line.sound is SoundKind.MUSIC]
    assert len(parts) == len(service.music_bodies) == len(joined[0][0]) == 2
    assert sum(line.seconds for line in parts) == 600


# ---- where each request's settings come from -------------------------------------------------

TUNED = TOML.replace(
    '[soundscape.effects.chime]\ntext = "a bright chime"\n',
    '[soundscape.effects]\nduration_seconds = 1.5\n\n[soundscape.effects.chime]\ntext = "a bright chime"\n\n'
    '[soundscape.effects.tap]\ntext = "a tap"\nduration_seconds = 0.2\nmodel = "taps_v1"\n',
).replace(
    '[soundscape.ambience]\ntext = "a quiet room"\n',
    '[soundscape.ambience]\ntext = "a quiet room"\nmodel = "rooms_v1"\n',
)
"""The test project with the effects' own default, one effect that sets its own, and the bed's model."""


def bodies(inputs: Inputs) -> dict[str, dict[str, Any]]:
    """The first request body of every planned item, by name."""
    return {item.name: item.bodies[0] for item in stage.plan_items(inputs)}


def test_each_item_is_asked_for_with_the_settings_of_its_own_table(tmp_path: Path) -> None:
    sent = bodies(an_inputs(tmp_path, TUNED))
    assert (sent["ambience"]["model_id"], sent["ambience"]["duration_seconds"]) == ("rooms_v1", 25.0)
    assert (sent["chime"]["model_id"], sent["chime"]["duration_seconds"]) == ("eleven_text_to_sound_v2", 1.5)
    assert (sent["tap"]["model_id"], sent["tap"]["duration_seconds"]) == ("taps_v1", 0.2)
    assert sent["music"]["music_length_ms"] == 30_000


def test_an_override_reaches_the_bed_whose_table_also_holds_its_prompt(tmp_path: Path) -> None:
    """The bed's settings are read once, by the settings layer, so the environment and --set reach them."""
    write_project(tmp_path, TUNED)
    environ = {"DECKTALK_SOUNDSCAPE_AMBIENCE_MODEL": "rooms_v2", "DECKTALK_SOUNDSCAPE_MUSIC_DURATION_SECONDS": "60"}
    sent = bodies(Inputs.load(tmp_path, environ=environ))
    assert sent["ambience"]["model_id"] == "rooms_v2"
    assert sent["music"]["music_length_ms"] == 60_000


def test_the_ledger_digests_of_the_regrouped_defaults_are_the_ones_bought_before(tmp_path: Path) -> None:
    """A soundscape bought under the old tables is not bought again: the defaults ask for the same request."""
    assert {item.name: item.digest for item in stage.plan_items(an_inputs(tmp_path))} == LEDGER_DIGESTS


def test_moving_the_api_base_moves_no_digest(tmp_path: Path) -> None:
    """Another host of the same service, or a local mock, is sent the same request, so nothing is bought again."""
    moved = TOML.replace(
        "[soundscape.ambience]",
        '[elevenlabs]\napi_base = "https://api.eu.residency.elevenlabs.io/v1"\n\n[soundscape.ambience]',
    )
    assert {item.name: item.digest for item in stage.plan_items(an_inputs(tmp_path, moved))} == LEDGER_DIGESTS


LEDGER_DIGESTS = {
    "ambience": "ad67a63b2d29b43b",
    "chime": "80835435cc361352",
    "music": "0d580aae66c361e5",
}
"""The digest of every item of the test project at the defaults, computed by the code before the regroup.

The digest no longer reads `api_base` and is taken over the endpoint as the sound provider publishes
it, which at the default base is the same text the digest was always taken over, so these did not move.
"""


@pytest.mark.parametrize("name", ["model", "duration_seconds", "prompt_influence"])
def test_an_effect_named_after_a_shared_setting_is_refused_by_name(tmp_path: Path, name: str) -> None:
    toml = TOML.replace("[soundscape.effects.chime]", f"[soundscape.effects.{name}]")
    with pytest.raises(InputError, match=rf"\[soundscape.effects.{name}\] is named after a setting"):
        an_inputs(tmp_path, toml)


def test_a_misspelt_setting_among_the_effects_is_a_note_and_not_a_refusal(tmp_path: Path) -> None:
    toml = TOML.replace(
        "[soundscape.effects.chime]", "[soundscape.effects]\nduraton_seconds = 1.0\n\n[soundscape.effects.chime]"
    )
    inputs = an_inputs(tmp_path, toml)
    (said,) = inputs.notes
    assert said.endswith(
        "[soundscape.effects]: ignoring unknown key 'duraton_seconds' (did you mean 'duration_seconds'?)."
    )
    assert set(inputs.document.soundscape.effects) == {"chime"}
