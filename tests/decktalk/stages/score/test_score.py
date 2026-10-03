"""The stage that buys the music, the ambience bed and the effects a project describes.

Every request here is money, so what these tests hold is the gate and the ledger: a run that nobody
approved buys nothing, a request that has not moved is not sent again, every file lands under the
workspace, and the record of what was bought is written after every paid call.

The service is a fake sound provider, either handed to the stage at `client_for` or registered in
the run's own sound table, so the requests a run would send are read here rather than sent.
"""

from __future__ import annotations

import inspect
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from decktalk.errors import ApprovalRequired, Cancelled, InputError
from decktalk.events import Event, SoundCharged, StageProgress, Unit
from decktalk.findings import Code, Severity
from decktalk.inputs import Inputs
from decktalk.media import audio
from decktalk.pipeline import Stage
from decktalk.results import Billing, CostState, Layer, SoundKind, SoundStatus, rate_money
from decktalk.speech.sound import SoundContext
from decktalk.stages import score as stage
from decktalk.stages.score import ledger as ledger_module
from decktalk.stages.score import score
from decktalk.stages.score.ledger import LEDGER_FILE, Ledger
from support.fakes import FakeVoice
from support.git import committed_clone, git
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
music = "build/score/music.mp3"

[[mix.effects]]
file = "score/chime.mp3"
section = 1
cue = "1.1:open"

[score.ambience]
prompt = "a quiet room"

[score.effects.chime]
prompt = "a bright chime"

[score.music]
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
    root.mkdir(parents=True, exist_ok=True)
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


def test_a_project_with_no_score_generates_nothing_and_says_so(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, NOTHING_TOML)
    run = a_run(tmp_path)
    said: list[Event] = []
    run.machine.events.subscribe(said.append)
    result = score(inputs, run)
    assert result.items == ()
    assert result.cost.characters == 0
    assert any("nothing to generate" in getattr(line, "message", "") for line in said)


def test_the_items_are_the_ambience_the_effects_and_the_music_in_the_order_the_table_declares_them(
    tmp_path: Path,
) -> None:
    result = score(an_inputs(tmp_path), a_run(tmp_path))
    assert [(item.name, item.kind) for item in result.items] == [
        ("ambience", SoundKind.AMBIENCE),
        ("chime", SoundKind.EFFECT),
        ("music", SoundKind.MUSIC),
    ]


def test_a_run_nobody_approved_plans_every_item_and_writes_nothing(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path)
    result = score(inputs, a_run(tmp_path))
    assert {item.status for item in result.items} == {SoundStatus.PLANNED}
    assert result.written == ()
    assert not (inputs.workspace.score_dir / LEDGER_FILE).exists()


def test_an_item_that_is_only_planned_and_has_no_audio_is_an_unbought_sound_that_plays_silence(
    tmp_path: Path,
) -> None:
    """An unbought sound is the author's choice not to spend yet, so it warns and never fails the film."""
    result = score(an_inputs(tmp_path), a_run(tmp_path))
    assert {found.code for found in result.findings} == {Code.SOUND_MISSING}
    assert Code.SOUND_MISSING.severity is Severity.WARNING
    assert result.ok
    first = result.findings[0]
    assert first.location.where == "ambience"
    assert first.location.file == Path("score/ambience.mp3")
    assert "25 seconds of audio" in first.message


RATED = (
    TOML.replace(
        "[score.ambience]\n",
        "[score.ambience]\ndollars_per_minute = 0.6\n",
    )
    .replace(
        "[score.effects.chime]",
        "[score.effects]\ndollars_per_minute = 1.2\n\n[score.effects.chime]",
    )
    .replace(
        "duration_seconds = 30\n",
        "duration_seconds = 30\ndollars_per_minute = 0.3\n",
    )
)
"""The test project with a rate stated for each kind: 60 cents, $1.20 and 30 cents a minute."""

EFFECT = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[score.effects]
dollars_per_minute = 0.12

[score.effects.whoosh]
prompt = "a whoosh"
duration_seconds = 10
"""
"""One ten-second effect at twelve cents a minute, which is two cents."""


def test_a_ten_second_effect_is_priced_per_second_at_the_rate_its_table_states(tmp_path: Path) -> None:
    priced = stage.price(an_inputs(tmp_path, EFFECT))
    assert priced.dollars == priced.ceiling_dollars == 0.02
    assert (priced.billing, priced.seconds, priced.characters) == (Billing.PER_SECOND, 10.0, 0)
    assert priced.dollars_per_minute == 0.12
    assert (priced.price_key, priced.price_layer, priced.averaged) == (
        "score.effects.dollars_per_minute",
        Layer.PROJECT,
        False,
    )
    assert priced.sections == (1,)
    assert priced.sentence == "This run costs $0.02 for about 10 seconds of audio at $0.12 per minute of audio."


@pytest.mark.parametrize("typed", [0.12, 0.3, 0.07, 1.2, 0.005, 99.99])
def test_the_rate_a_cost_prints_is_the_rate_the_author_typed(tmp_path: Path, typed: float) -> None:
    """A rate is typed and printed in dollars per minute, so a reader can check one against the other."""
    priced = stage.price(
        an_inputs(tmp_path, EFFECT.replace("dollars_per_minute = 0.12", f"dollars_per_minute = {typed}"))
    )
    assert priced.dollars_per_minute == typed
    assert priced.dollars == round(10 * typed / 60, 2)
    assert priced.rate == f"{rate_money(typed)} per minute of audio"
    assert f'"dollars_per_minute":{typed}' in priced.model_dump_json()


def test_kinds_bought_at_different_rates_are_priced_exactly_and_said_to_average(tmp_path: Path) -> None:
    """The sum is exact, and the one rate a price states is what that sum comes to per minute, said as an average."""
    priced = score(an_inputs(tmp_path, RATED), a_run(tmp_path)).cost
    assert priced.seconds == 55.5
    assert priced.averaged
    assert priced.dollars_per_minute * priced.seconds / 60 == pytest.approx(0.41)
    assert priced.sentence == (
        "This run costs $0.41 for about 56 seconds of audio at an average of $0.44 per minute of audio."
    )


def test_a_cap_over_a_rate_nobody_stated_names_that_rates_key(tmp_path: Path) -> None:
    unstated = RATED.replace("duration_seconds = 30\ndollars_per_minute = 0.3\n", "duration_seconds = 30\n")
    with pytest.raises(ApprovalRequired) as refused:
        score(an_inputs(tmp_path, unstated), a_run(tmp_path, spend=True, max_cost=5.0))
    assert "score.music.dollars_per_minute" in (refused.value.hint or "")


@pytest.mark.usefixtures("fake_ffmpeg")
def test_sound_is_asked_for_in_its_own_format(tmp_path: Path) -> None:
    """The format is the score's own key, so a take's format moving never changes what a sound is sent."""
    formats: list[str] = []

    @dataclass
    class Formats(FakeService):
        def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
            formats.append(output_format)
            return super().effect(body, output_format=output_format)

    toml = EFFECT.replace("[score.effects]\n", '[score]\nformat = "mp3_22050_32"\n\n[score.effects]\n')
    toml = toml.replace("[project]", '[elevenlabs]\noutput_format = "mp3_44100_192"\n\n[project]', 1)
    fake = Formats()
    score(an_inputs(tmp_path, toml), a_sounding_run(tmp_path, {"elevenlabs": lambda _context: fake}, spend=True))
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
    result = score(an_inputs(tmp_path, RATED), a_run(tmp_path))
    assert result.cost.dollars == result.cost.ceiling_dollars == round(0.25 + 0.01 + 0.15, 2)
    assert result.cost.state is CostState.ESTIMATE
    assert result.cost.sections == (1, 2)


def test_what_speech_costs_prices_no_sound(tmp_path: Path) -> None:
    """Sound is billed by the second of audio, so the speech rate per character is never read for it."""
    toml = TOML.replace("[score.ambience]", "[elevenlabs]\ndollars_per_1000_characters = 100.0\n\n[score.ambience]")
    result = score(an_inputs(tmp_path, toml), a_run(tmp_path))
    assert result.cost.dollars == 0
    assert result.cost.price_layer is Layer.DEFAULT


def test_a_rate_one_kind_leaves_unstated_is_a_price_nobody_stated(tmp_path: Path) -> None:
    """A cap may not guard a run whose every rate is not stated, so one default rate makes the price a default."""
    stated = RATED.replace("dollars_per_minute = 0.3\n", "")
    assert score(an_inputs(tmp_path, stated), a_run(tmp_path)).cost.price_layer is Layer.DEFAULT
    assert score(an_inputs(tmp_path, RATED), a_run(tmp_path)).cost.price_layer is Layer.PROJECT


def test_a_price_no_layer_records_is_the_default_price_and_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer table may hold no row for the price, which narrate and build already read as the default."""
    inputs = an_inputs(tmp_path)

    def unstated(_layers: object, key: str) -> None:
        raise KeyError(key)

    monkeypatch.setattr(type(inputs.layers), "winner", unstated)
    assert score(inputs, a_run(tmp_path)).cost.price_layer is Layer.DEFAULT


# ---- what the run buys ------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_paid_run_buys_every_item_and_writes_it_under_the_workspace(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    result = score(inputs, a_run(tmp_path, spend=True))
    assert {item.status for item in result.items} == {SoundStatus.GENERATED}
    assert len(service.sounds) == 2
    assert len(service.music_bodies) == 1
    assert (inputs.workspace.score_dir / "ambience.mp3").is_file()
    assert (inputs.workspace.score_dir / "chime.mp3").is_file()
    assert Path("score/ledger.json") in result.written


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_run_that_may_not_spend_buys_no_sound_whatever_sound_costs(tmp_path: Path, service: FakeService) -> None:
    """Only the run's own spend lets it buy, so a sound somebody stated is free is still not bought without it."""
    free = RATED.replace("0.6", "0").replace("1.2", "0").replace("0.3", "0")
    result = score(an_inputs(tmp_path, free), a_run(tmp_path))
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
    assert not inputs.workspace.score_dir.exists()


def test_a_run_with_nothing_left_to_buy_covers_no_section(tmp_path: Path) -> None:
    """A price that covers no section is how a caller learns the run has nothing to ask about."""
    inputs = an_inputs(tmp_path)
    assert score(inputs, a_run(tmp_path)).cost.buys
    assert not stage.cost_of(inputs, [], None).buys


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ambience_request_asks_for_audio_that_meets_its_own_end(tmp_path: Path, service: FakeService) -> None:
    score(an_inputs(tmp_path), a_run(tmp_path, spend=True))
    assert service.sounds[0]["loop"] is True
    assert "loop" not in service.sounds[1]


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_second_run_keeps_every_item_whose_request_has_not_moved(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    sent = len(service.sounds) + len(service.music_bodies)
    again = score(inputs, a_run(tmp_path, spend=True))
    assert {item.status for item in again.items} == {SoundStatus.KEPT}
    assert len(service.sounds) + len(service.music_bodies) == sent


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_an_item_whose_prompt_moved_is_bought_again_and_the_rest_are_kept(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    service.sounds.clear()
    moved = an_inputs(tmp_path, TOML.replace("a bright chime", "a dull chime"))
    again = score(moved, a_run(tmp_path, spend=True))
    statuses = {item.name: item.status for item in again.items}
    assert statuses["chime"] is SoundStatus.GENERATED
    assert statuses["ambience"] is SoundStatus.KEPT
    assert [body["text"] for body in service.sounds] == ["a dull chime"]


def test_the_stage_takes_no_force_because_every_item_it_makes_is_bought() -> None:
    """`force` never spends, and every item this stage makes is bought, so it has nothing free to redo.

    A `force` here could only buy again what the ledger holds, which is how `build --force` once
    bought every sound of a project again unasked. `replace_score` is the one flag that does.
    """
    assert "force" not in inspect.signature(score).parameters
    assert "force" not in inspect.signature(stage.price).parameters


@pytest.mark.usefixtures("fake_ffmpeg")
def test_replace_score_with_spend_buys_every_held_item_and_every_music_part_again(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    toml = TOML.replace("duration_seconds = 30", "duration_seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    score(inputs, a_run(tmp_path, spend=True))
    sounds, parts = len(service.sounds), len(service.music_bodies)
    priced = stage.price(inputs, replace_score=True)
    again = score(inputs, a_run(tmp_path, spend=True), replace_score=True)
    assert {item.status for item in again.items} == {SoundStatus.GENERATED}
    assert (len(service.sounds), len(service.music_bodies)) == (2 * sounds, 2 * parts)
    assert priced.seconds == again.cost.seconds
    assert len(joined) == 2


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_replace_score_without_spend_buys_nothing_and_keeps_every_held_item(
    tmp_path: Path, service: FakeService
) -> None:
    """Without spend there is nothing to replace a bought sound with, so the one on disk keeps playing."""
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    sent = len(service.sounds) + len(service.music_bodies)
    again = score(inputs, a_run(tmp_path), replace_score=True)
    assert {item.status for item in again.items} == {SoundStatus.KEPT}
    assert len(service.sounds) + len(service.music_bodies) == sent
    assert again.findings == ()


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ledger_records_what_each_item_was_bought_with(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    ledger = Ledger.read(inputs.workspace.score_dir / LEDGER_FILE)
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
    score(inputs, a_run(tmp_path, spend=True))
    assert len(service.music_bodies) > 1
    parts, out = joined[0]
    assert len(parts) == len(service.music_bodies)
    assert out == inputs.workspace.joined_dir / "music.mp3"


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_music_part_that_was_already_bought_is_not_bought_again(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    toml = TOML.replace("duration_seconds = 30", "duration_seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    score(inputs, a_run(tmp_path, spend=True))
    sent = len(service.music_bodies)
    (inputs.workspace.joined_dir / "music.mp3").unlink()
    score(inputs, a_run(tmp_path, spend=True))
    assert len(service.music_bodies) == sent
    assert len(joined) == 2


# ---- where the score is kept --------------------------------------------------------------------


def files_under(directory: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under one directory, by its relative path, with its bytes and the time it was last written."""
    found = (path for path in directory.rglob("*") if path.is_file())
    return {path.relative_to(directory).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in found}


@pytest.mark.usefixtures("fake_ffmpeg")
def test_bought_audio_and_the_ledger_are_kept_in_the_score_directory_by_item_name(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """`rm -rf build` is free, so a bought sound never lands there, with no setting to remember."""
    toml = TOML.replace("duration_seconds = 30", "duration_seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    result = score(inputs, a_run(tmp_path, spend=True))
    assert inputs.settings.score.dir == "score"
    parts = [f"music-part{index + 1}.mp3" for index in range(len(service.music_bodies))]
    assert sorted(files_under(tmp_path / "score")) == sorted(["ambience.mp3", "chime.mp3", LEDGER_FILE, *parts])
    assert Path("score") / LEDGER_FILE in result.written
    [(joined_parts, out)] = joined
    assert joined_parts == [tmp_path / "score" / part for part in parts]
    assert out == inputs.workspace.build / "score" / "music.mp3", "the joined music is a cache, made again for free"
    assert [name for name in files_under(inputs.workspace.build) if name.startswith("score/")] == ["score/music.mp3"]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_run_after_the_build_directory_is_deleted_buys_no_sound_again(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """The ledger and the audio survive in the score directory, so only the free join is made again."""
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    sent = (len(service.sounds), len(service.music_bodies))
    shutil.rmtree(inputs.workspace.build)
    again = score(Inputs.load(tmp_path, environ={}), a_run(tmp_path, spend=True))
    assert (len(service.sounds), len(service.music_bodies)) == sent
    assert {item.status for item in again.items} == {SoundStatus.KEPT}
    assert again.cost.seconds == 0 and again.findings == ()
    assert len(joined) == 2 and joined[-1][1].is_file()


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_run_that_may_not_spend_never_writes_the_score_directory(tmp_path: Path, service: FakeService) -> None:
    """A run without spend reads the paid records and never writes one, whether it has any or not."""
    fresh = an_inputs(tmp_path / "fresh")
    score(fresh, a_run(tmp_path / "fresh"))
    assert not (tmp_path / "fresh" / "score").exists()
    inputs = an_inputs(tmp_path / "bought")
    score(inputs, a_run(tmp_path / "bought", spend=True))
    sent = len(service.sounds) + len(service.music_bodies)
    kept = files_under(tmp_path / "bought" / "score")
    shutil.rmtree(inputs.workspace.build)
    played = score(inputs, a_run(tmp_path / "bought"))
    assert files_under(tmp_path / "bought" / "score") == kept
    assert {item.status for item in played.items} == {SoundStatus.KEPT} and played.findings == ()
    assert len(service.sounds) + len(service.music_bodies) == sent


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_run_that_does_not_spend_leaves_a_checkout_that_commits_its_score_clean(
    tmp_path: Path, service: FakeService
) -> None:
    """The Action's case: the score is committed, so playing it buys nothing and changes no tracked file."""
    bought = an_inputs(tmp_path / "bought")
    score(bought, a_run(tmp_path / "bought", spend=True))
    sent = len(service.sounds) + len(service.music_bodies)
    clone = committed_clone(bought.root, tmp_path / "clone")
    fresh = Inputs.load(clone, environ={})
    played = score(fresh, a_run(clone))
    assert git(clone, "status", "--porcelain") == ""
    assert {item.status for item in played.items} == {SoundStatus.KEPT}
    assert len(service.sounds) + len(service.music_bodies) == sent


@pytest.mark.parametrize("named", ["", "/tmp/score", "../score", "."])
def test_a_score_dir_outside_the_project_or_empty_is_refused_at_load(tmp_path: Path, named: str) -> None:
    """A bought sound must land in a folder the project keeps, which only a directory inside it is."""
    toml = TOML.replace("[score.ambience]", f'[score]\ndir = "{named}"\n\n[score.ambience]')
    with pytest.raises(InputError, match=r"score\.dir|\[score\] dir"):
        an_inputs(tmp_path / "proj", toml)


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_replace_score_overwrites_each_bought_file_in_place(tmp_path: Path, service: FakeService) -> None:
    """A sound is named by its item, so buying it again writes over the one file the project commits."""
    inputs = an_inputs(tmp_path)
    score(inputs, a_run(tmp_path, spend=True))
    first = files_under(tmp_path / "score")
    assert sorted(first) == sorted(["ambience.mp3", "chime.mp3", LEDGER_FILE, "music-part1.mp3"])
    service.answer = b"bought again"
    score(inputs, a_run(tmp_path, spend=True), replace_score=True)
    again = files_under(tmp_path / "score")
    assert sorted(again) == sorted(first)
    assert {name for name, (data, _) in again.items() if data == b"bought again"} == set(first) - {LEDGER_FILE}


# ---- the gate and the stream ------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_a_ceiling_over_a_price_nobody_stated_refuses_the_run_before_it_buys(tmp_path: Path) -> None:
    """The rate is still the default, so a cap would be guarding a price DeckTalk invented."""
    inputs = an_inputs(tmp_path)
    with pytest.raises(ApprovalRequired):
        score(inputs, a_run(tmp_path, spend=True, max_cost=1.0))
    assert not (inputs.workspace.score_dir / "ambience.mp3").exists()


def test_a_cancelled_run_stops_before_it_reaches_the_first_item(tmp_path: Path) -> None:
    run = a_run(tmp_path)
    run.cancel.cancel()
    with pytest.raises(Cancelled):
        score(an_inputs(tmp_path), run)


def test_one_progress_line_is_reported_for_every_asset(tmp_path: Path) -> None:
    run = a_run(tmp_path)
    seen: list[StageProgress] = []
    run.machine.events.subscribe(lambda line: seen.append(line) if isinstance(line, StageProgress) else None)
    score(an_inputs(tmp_path), run)
    assert [line.label for line in seen] == ["ambience", "chime", "music", "score"]
    assert {line.unit for line in seen} == {Unit.ASSET}
    assert {line.stage for line in seen} == {Stage.SCORE}
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
    result = score(an_inputs(tmp_path), a_run(tmp_path), only=only)
    assert {item.name for item in result.items} == names


# ---- where the files go -----------------------------------------------------------------------


def test_every_default_path_is_the_workspace(tmp_path: Path) -> None:
    """What is bought lands in the score directory, and only the music joined from it lands under the build."""
    inputs = an_inputs(tmp_path, TOML.replace('music = "build/score/music.mp3"\n', ""))
    planned = stage.plan_items(inputs)
    assert {path.parent for item in planned for path in item.bought} == {inputs.workspace.score_dir}
    assert {item.out.parent for item in planned if item.parts} == {inputs.workspace.joined_dir}


def test_an_item_that_names_its_own_file_is_written_where_the_project_says(tmp_path: Path) -> None:
    toml = TOML.replace(
        '[score.effects.chime]\nprompt = "a bright chime"',
        '[score.effects.chime]\nprompt = "a bright chime"\nout = "media/chime.mp3"',
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
    path = inputs.workspace.score_dir / LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written, encoding="utf-8")
    with pytest.raises(InputError) as refused:
        score(inputs, a_run(tmp_path, spend=True))
    assert "ledger.json" in str(refused.value) and "Only buying every item it lists again" in str(refused.value)
    hint = refused.value.hint or ""
    assert not hint.startswith("Delete") and "costs money on a paid provider" in hint
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
    assert context.base_url == "https://api.elevenlabs.io/v1"
    assert context.timeout_seconds == 600


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_paid_run_buys_through_the_sound_provider_its_machine_holds(tmp_path: Path) -> None:
    """No class is checked: whatever the table answers with is asked for the effects and the music."""
    fake = FakeService()
    run = a_sounding_run(tmp_path, {"elevenlabs": lambda _context: fake}, spend=True)
    result = score(an_inputs(tmp_path), run)
    assert {item.status for item in result.items} == {SoundStatus.GENERATED}
    assert [body["text"] for body in fake.sounds] == ["a quiet room", "a bright chime"]
    assert [body["prompt"] for body in fake.music_bodies] == ["warm strings"]


def test_the_sound_provider_is_the_one_the_score_table_names(tmp_path: Path) -> None:
    toml = TOML.replace("[score.ambience]", '[score]\nprovider = "house"\n\n[score.ambience]')
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
    result = score(an_inputs(tmp_path, RATED), a_run(tmp_path, spend=True, lines=lines))
    charged = [line for line in lines if isinstance(line, SoundCharged)]
    assert [(line.name, line.kind, line.seconds) for line in charged] == [
        ("ambience", SoundKind.AMBIENCE, 25.0),
        ("chime", SoundKind.EFFECT, 0.5),
        ("music", SoundKind.MUSIC, 30.0),
    ]
    assert [line.dollars for line in charged] == pytest.approx([0.25, 0.01, 0.15])
    assert result.cost.state is CostState.CHARGED
    assert result.cost.dollars == 0.41


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_a_run_that_keeps_every_item_charges_nothing(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, RATED)
    score(inputs, a_run(tmp_path, spend=True))
    lines: list[Event] = []
    again = score(inputs, a_run(tmp_path, spend=True, lines=lines))
    assert [line for line in lines if isinstance(line, SoundCharged)] == []
    assert again.cost.state is CostState.ESTIMATE
    assert again.cost.dollars == 0


@pytest.mark.usefixtures("fake_ffmpeg")
def test_each_music_part_is_charged_as_it_is_bought(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """A part is paid for the moment the service answers, so a run stopped half way has charged each part it bought."""
    lines: list[Event] = []
    toml = RATED.replace("duration_seconds = 30", "duration_seconds = 600")
    score(an_inputs(tmp_path, toml), a_run(tmp_path, spend=True, lines=lines))
    parts = [line for line in lines if isinstance(line, SoundCharged) and line.kind is SoundKind.MUSIC]
    assert len(parts) == len(service.music_bodies) == len(joined[0][0]) == 2
    assert sum(line.seconds for line in parts) == 600


# ---- where each request's settings come from -------------------------------------------------

TUNED = TOML.replace(
    '[score.effects.chime]\nprompt = "a bright chime"\n',
    '[score.effects]\nduration_seconds = 1.5\n\n[score.effects.chime]\nprompt = "a bright chime"\n\n'
    '[score.effects.tap]\nprompt = "a tap"\nduration_seconds = 0.2\nmodel = "taps_v1"\n',
).replace(
    '[score.ambience]\nprompt = "a quiet room"\n',
    '[score.ambience]\nprompt = "a quiet room"\nmodel = "rooms_v1"\n',
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
    environ = {"DECKTALK_SCORE_AMBIENCE_MODEL": "rooms_v2", "DECKTALK_SCORE_MUSIC_DURATION_SECONDS": "60"}
    sent = bodies(Inputs.load(tmp_path, environ=environ))
    assert sent["ambience"]["model_id"] == "rooms_v2"
    assert sent["music"]["music_length_ms"] == 60_000


def test_the_ledger_digests_of_the_regrouped_defaults_are_the_ones_bought_before(tmp_path: Path) -> None:
    """A score bought under the old tables is not bought again: the defaults ask for the same request."""
    assert {item.name: item.digest for item in stage.plan_items(an_inputs(tmp_path))} == LEDGER_DIGESTS


def test_moving_the_base_url_moves_no_digest(tmp_path: Path) -> None:
    """Another host of the same service, or a local mock, is sent the same request, so nothing is bought again."""
    write_project(tmp_path, TOML)
    machine = {"elevenlabs": {"base_url": "https://api.eu.residency.elevenlabs.io/v1"}}
    moved = Inputs.load(tmp_path, environ={}, machine=machine)
    assert moved.settings.elevenlabs.base_url == machine["elevenlabs"]["base_url"]
    assert {item.name: item.digest for item in stage.plan_items(moved)} == LEDGER_DIGESTS


LEDGER_DIGESTS = {
    "ambience": "ad67a63b2d29b43b",
    "chime": "80835435cc361352",
    "music": "0d580aae66c361e5",
}
"""The digest of every item of the test project at the defaults, computed by the code before the regroup.

The digest no longer reads `base_url` and is taken over the endpoint as the sound provider publishes
it, which at the default base is the same text the digest was always taken over, so these did not move.
"""


@pytest.mark.parametrize("name", ["model", "duration_seconds", "prompt_influence"])
def test_an_effect_named_after_a_shared_setting_is_refused_by_name(tmp_path: Path, name: str) -> None:
    toml = TOML.replace("[score.effects.chime]", f"[score.effects.{name}]")
    with pytest.raises(InputError, match=rf"\[score.effects.{name}\] is named after a setting"):
        an_inputs(tmp_path, toml)


def test_a_misspelt_setting_among_the_effects_is_a_note_and_not_a_refusal(tmp_path: Path) -> None:
    toml = TOML.replace("[score.effects.chime]", "[score.effects]\nduraton_seconds = 1.0\n\n[score.effects.chime]")
    inputs = an_inputs(tmp_path, toml)
    (said,) = inputs.notes
    assert said.endswith("[score.effects]: ignoring unknown key 'duraton_seconds' (did you mean 'duration_seconds'?).")
    assert set(inputs.document.score.effects) == {"chime"}
