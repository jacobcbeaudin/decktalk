"""The stage that buys the music, the ambience bed and the effects a project describes.

Every request here is money, so what these tests hold is the gate and the ledger: a run that nobody
approved buys nothing, a request that has not moved is not sent again, every file lands under the
workspace, and the record of what was bought is written after every paid call.

The service is faked at `client_for`, which is the one seam the stage reaches it through, so the
requests a run would send are read here rather than sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from decktalk.errors import ApprovalRequired, Cancel
from decktalk.events import Event, Progress, Unit
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media import audio
from decktalk.pipeline import Stage
from decktalk.results import SoundKind, SoundStatus, Voicing
from decktalk.stages import soundscape as stage
from decktalk.stages.soundscape import soundscape
from decktalk.stages.soundscape.ledger import LEDGER_FILE, Ledger
from support.projects import write_project

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
seconds = 30
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
    """The sound service, with every request it was sent and the bytes it answered with."""

    sounds: list[dict[str, Any]] = field(default_factory=list)
    music_bodies: list[dict[str, Any]] = field(default_factory=list)
    answer: bytes = b"audio"

    def sound_effect(self, body: dict[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        self.sounds.append(body)
        return self.answer

    def music(self, body: dict[str, Any], *, output_format: str) -> bytes:  # noqa: ARG002
        self.music_bodies.append(body)
        return self.answer


def a_run(root: Path, *, voice: Voicing = Voicing.PLACEHOLDER, max_cost: float | None = None) -> Run:
    machine = Machine(environ={}, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="run-1", cancel=Cancel(), voice=voice, max_cost=max_cost, root=root)


def an_inputs(root: Path, toml: str = TOML) -> Inputs:
    write_project(root, toml)
    return Inputs.load(root, environ={})


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    """The sound service faked at the one seam the stage builds it through."""
    fake = FakeService()
    monkeypatch.setattr(stage, "client_for", lambda _inputs: fake)
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
    assert "12 characters" in first.message


def test_the_price_is_the_prompt_characters_at_the_rate_the_project_states(tmp_path: Path) -> None:
    root = tmp_path
    toml = TOML.replace("[soundscape.ambience]", "[voice]\nprice_per_1000_characters = 100.0\n\n[soundscape.ambience]")
    result = soundscape(an_inputs(root, toml), a_run(root))
    assert result.spend.price_per_1000_characters == 100.0
    assert result.spend.characters == len("a quiet room") + len("a bright chime") + len("warm strings")
    assert result.spend.dollars == result.spend.ceiling_dollars
    assert result.spend.sections == (1, 2)


# ---- what the run buys ------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_paid_run_buys_every_item_and_writes_it_under_the_workspace(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    result = soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    assert {item.status for item in result.items} == {SoundStatus.GENERATED}
    assert len(service.sounds) == 2
    assert len(service.music_bodies) == 1
    assert (inputs.workspace.soundscape_dir / "ambience.mp3").is_file()
    assert (inputs.workspace.soundscape_dir / "chime.mp3").is_file()
    assert Path("build/soundscape/ledger.json") in result.written


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ambience_request_asks_for_audio_that_meets_its_own_end(tmp_path: Path, service: FakeService) -> None:
    soundscape(an_inputs(tmp_path), a_run(tmp_path, voice=Voicing.PAID))
    assert service.sounds[0]["loop"] is True
    assert "loop" not in service.sounds[1]


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_a_second_run_keeps_every_item_whose_request_has_not_moved(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    sent = len(service.sounds) + len(service.music_bodies)
    again = soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    assert {item.status for item in again.items} == {SoundStatus.KEPT}
    assert len(service.sounds) + len(service.music_bodies) == sent


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_an_item_whose_prompt_moved_is_bought_again_and_the_rest_are_kept(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    service.sounds.clear()
    moved = an_inputs(tmp_path, TOML.replace("a bright chime", "a dull chime"))
    again = soundscape(moved, a_run(tmp_path, voice=Voicing.PAID))
    statuses = {item.name: item.status for item in again.items}
    assert statuses["chime"] is SoundStatus.GENERATED
    assert statuses["ambience"] is SoundStatus.KEPT
    assert [body["text"] for body in service.sounds] == ["a dull chime"]


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_force_buys_every_item_again_although_nothing_moved(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    service.sounds.clear()
    again = soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID), force=True)
    assert {item.status for item in again.items} == {SoundStatus.GENERATED}
    assert len(service.sounds) == 2


@pytest.mark.usefixtures("fake_ffmpeg", "joined")
def test_the_ledger_records_what_each_item_was_bought_with(tmp_path: Path, service: FakeService) -> None:
    inputs = an_inputs(tmp_path)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    ledger = Ledger.read(inputs.workspace.soundscape_dir / LEDGER_FILE)
    assert ledger is not None
    assert {row.name for row in ledger.items} == {"ambience", "chime", "music"}
    assert ledger.of("chime") is not None
    assert "a bright chime" in ledger.of("chime").request  # type: ignore[union-attr]  (asserted above)
    assert service.sounds


@pytest.mark.usefixtures("fake_ffmpeg")
def test_the_music_is_asked_for_in_chunks_and_joined_into_one_bed(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    """The service writes at most one chunk in an answer, so a longer bed is bought in parts."""
    toml = TOML.replace("seconds = 30", "seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    assert len(service.music_bodies) > 1
    parts, out = joined[0]
    assert len(parts) == len(service.music_bodies)
    assert out == inputs.workspace.soundscape_dir / "music.mp3"


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_music_part_that_was_already_bought_is_not_bought_again(
    tmp_path: Path, service: FakeService, joined: list[tuple[list[Path], Path]]
) -> None:
    toml = TOML.replace("seconds = 30", "seconds = 600")
    inputs = an_inputs(tmp_path, toml)
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    sent = len(service.music_bodies)
    (inputs.workspace.soundscape_dir / "music.mp3").unlink()
    soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID))
    assert len(service.music_bodies) == sent
    assert len(joined) == 2


# ---- the gate and the stream ------------------------------------------------------------------


@pytest.mark.usefixtures("fake_ffmpeg", "joined", "service")
def test_a_ceiling_over_a_price_nobody_stated_refuses_the_run_before_it_buys(tmp_path: Path) -> None:
    """The rate is still the default, so a cap would be guarding a price DeckTalk invented."""
    inputs = an_inputs(tmp_path)
    with pytest.raises(ApprovalRequired):
        soundscape(inputs, a_run(tmp_path, voice=Voicing.PAID, max_cost=1.0))
    assert not (inputs.workspace.soundscape_dir / "ambience.mp3").exists()


def test_a_cancelled_run_stops_before_it_reaches_the_first_item(tmp_path: Path) -> None:
    from decktalk.errors import Cancelled  # noqa: PLC0415  (the class this one test names)

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


def test_only_keeps_the_effects_cued_in_the_sections_it_names(tmp_path: Path) -> None:
    result = soundscape(an_inputs(tmp_path), a_run(tmp_path), only=[1])
    assert {item.name for item in result.items} == {"ambience", "chime", "music"}


def test_an_effect_cued_in_another_section_is_left_out(tmp_path: Path) -> None:
    result = soundscape(an_inputs(tmp_path), a_run(tmp_path), only=[2])
    assert {item.name for item in result.items} == {"music"}


def test_the_ambience_bed_is_wanted_only_where_a_selected_section_asks_for_one(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path)
    assert "ambience" in {item.name for item in soundscape(inputs, a_run(tmp_path), only=[1]).items}
    assert "ambience" not in {item.name for item in soundscape(inputs, a_run(tmp_path), only=[2]).items}


def test_the_music_is_wanted_whenever_any_section_is_selected(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path)
    assert "music" in {item.name for item in soundscape(inputs, a_run(tmp_path), only=[2]).items}
    assert soundscape(inputs, a_run(tmp_path), only=[7]).items == ()


# ---- where the files go -----------------------------------------------------------------------


def test_every_default_path_is_the_workspace_and_no_build_directory_is_spelled_here(tmp_path: Path) -> None:
    inputs = an_inputs(tmp_path, TOML.replace('music = "build/soundscape/music.mp3"\n', ""))
    planned = stage.plan_items(inputs)
    assert {item.out.parent for item in planned} == {inputs.workspace.soundscape_dir}
    source = Path("src/decktalk/stages/soundscape/__init__.py").read_text(encoding="utf-8")
    assert "build/sfx" not in source
    assert "build/music" not in source


def test_an_item_that_names_its_own_file_is_written_where_the_project_says(tmp_path: Path) -> None:
    toml = TOML.replace(
        '[soundscape.effects.chime]\ntext = "a bright chime"',
        '[soundscape.effects.chime]\ntext = "a bright chime"\nout = "media/chime.mp3"',
    )
    inputs = an_inputs(tmp_path, toml)
    chime = next(item for item in stage.plan_items(inputs) if item.name == "chime")
    assert chime.out == tmp_path / "media" / "chime.mp3"
