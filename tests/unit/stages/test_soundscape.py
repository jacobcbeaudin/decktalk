"""The soundscape stage: what it asks the provider for, what it buys again, and what it keeps.

This is the one command that spends money per call, so the cache rule and the dry run are held here
rather than in the handler above them. No test makes a request: the client is a stub that counts
what it was asked for, and `--dry-run` is asserted to build no client at all.
"""

from __future__ import annotations

import json
from importlib import import_module

import pytest

from decktalk.model import Project
from decktalk.pipeline import SoundscapeStatus
from decktalk.stages.soundscape import (
    SoundscapeResult,
    music_chunks,
    request_hash,
    sound_body,
    soundscape,
)

# The package re-exports the `soundscape` function under its own module's name, so the module is
# fetched by name rather than reached through the attribute the function now owns.
soundscape_module = import_module("decktalk.stages.soundscape")

TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[soundscape.ambience]
text = "a quiet room"

[soundscape.sfx.ding]
text = "a small bell"

[soundscape.music]
prompt = "calm piano"
seconds = 20
"""


class StubClient:
    """Stands in for `ElevenLabs`, counting every request rather than making one."""

    def __init__(self) -> None:
        self.sounds: list[dict] = []
        self.music: list[dict] = []

    def sound_effect(self, body, output_format):
        self.sounds.append(body)
        return b"an mp3"

    def music(self, body, output_format):  # pragma: no cover - the music path joins parts with ffmpeg
        self.music.append(body)
        return b"an mp3"


@pytest.fixture
def project(tmp_path, monkeypatch) -> Project:
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return Project.load(tmp_path, environ={"ELEVENLABS_API_KEY": "sk_not_a_real_key"})


def only_sounds(monkeypatch, client: StubClient) -> None:
    """Build the stub instead of a real client, and measure nothing with ffmpeg."""
    monkeypatch.setattr(soundscape_module, "ElevenLabs", lambda key, cfg: client)
    monkeypatch.setattr(soundscape_module.ffmpeg, "probe_duration", lambda path: 4.0)


def test_a_dry_run_builds_no_client_and_sends_nothing(project, monkeypatch):
    """The plan is what an author reads before spending, so it may not reach the provider at all."""
    monkeypatch.setattr(
        soundscape_module, "ElevenLabs", lambda key, cfg: pytest.fail("a dry run built a provider client")
    )
    result = soundscape(project, dry_run=True)
    assert [item.status for item in result.items] == [SoundscapeStatus.PLANNED] * 3
    assert [item.name for item in result.items] == ["ambience", "ding", "music"]
    # A planned item still names the endpoint a real run would call, because the ledger is keyed by it.
    assert all(item.endpoint.startswith("https://") for item in result.items)
    assert result.findings.certain == 0 and result.findings.uncertain == 0


def test_a_changed_prompt_is_paid_for_again_and_an_unchanged_one_is_kept(project, monkeypatch):
    """The cache is what stops a second run buying the same audio, so it is keyed on the request."""
    client = StubClient()
    only_sounds(monkeypatch, client)

    first = soundscape(project, only=["ding"])
    assert [item.status for item in first.items] == [SoundscapeStatus.GENERATED] and len(client.sounds) == 1

    again = soundscape(project, only=["ding"])
    assert [item.status for item in again.items] == [SoundscapeStatus.UNCHANGED]
    assert len(client.sounds) == 1, "an unchanged request was sent a second time"
    assert again.items[0].duration_seconds == 4.0

    # The prompt is part of the request, so changing it is a different sound and is bought again.
    toml = (project.root / "decktalk.toml").read_text(encoding="utf-8")
    (project.root / "decktalk.toml").write_text(toml.replace("a small bell", "a large bell"), encoding="utf-8")
    changed = Project.load(project.root, environ={"ELEVENLABS_API_KEY": "sk_not_a_real_key"})
    third = soundscape(changed, only=["ding"])
    assert [item.status for item in third.items] == [SoundscapeStatus.GENERATED] and len(client.sounds) == 2


def test_force_buys_an_unchanged_sound_again(project, monkeypatch):
    client = StubClient()
    only_sounds(monkeypatch, client)
    soundscape(project, only=["ding"])
    kept = soundscape(project, only=["ding"], force=True)
    assert [item.status for item in kept.items] == [SoundscapeStatus.GENERATED] and len(client.sounds) == 2


def test_the_positional_names_pick_the_items_to_work_on(project, monkeypatch):
    """`--only` took section numbers everywhere else, so soundscape names its items positionally."""
    client = StubClient()
    only_sounds(monkeypatch, client)
    assert [item.name for item in soundscape(project, only=["ambience"], dry_run=True).items] == ["ambience"]
    assert [item.name for item in soundscape(project, only=["ding"], dry_run=True).items] == ["ding"]
    both = soundscape(project, only=["ambience", "ding"], dry_run=True)
    assert [item.name for item in both.items] == ["ambience", "ding"]
    # A name nothing matches asks for nothing rather than for everything.
    assert soundscape(project, only=["nothing-by-this-name"], dry_run=True).items == []


def test_a_project_with_no_soundscape_table_generates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    plain = "[project]\nname = 't'\n\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"
    (tmp_path / "decktalk.toml").write_text(plain, encoding="utf-8")
    (tmp_path / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
    empty = Project.load(tmp_path, environ={})
    assert soundscape(empty) == SoundscapeResult()


def test_an_ambience_request_loops_and_an_effect_does_not(project):
    """A bed plays under the whole section and a one-shot lands on a cue, which is one flag apart."""
    cfg = project.settings.elevenlabs
    spec = project.soundscape
    assert sound_body(spec.ambience, cfg, loop=True)["loop"] is True
    assert "loop" not in sound_body(spec.sfx["ding"], cfg, loop=False)


def test_long_music_is_asked_for_in_chunks_that_say_they_belong_together(project):
    """One request has a ceiling, so a longer piece is joined from parts that name their place."""
    cfg = project.settings.elevenlabs
    longer = {**vars(project.soundscape.music), "seconds": cfg.max_music_chunk_seconds * 2 + 1}
    long_spec = type(project.soundscape.music)(**longer)
    chunks = music_chunks(long_spec, cfg)
    assert len(chunks) == 3
    assert all("part" in body["prompt"] for body in chunks)
    assert sum(body["music_length_ms"] for body in chunks) == pytest.approx(long_spec.seconds * 1000, abs=3)
    # A piece that fits in one request is asked for plainly, with no part wording to sing through.
    one = music_chunks(project.soundscape.music, cfg)
    assert len(one) == 1 and "part" not in one[0]["prompt"]


def test_the_request_hash_moves_with_the_endpoint_and_the_body():
    """The ledger is keyed on both, so a host change or a body change is a different purchase."""
    body = {"text": "a small bell"}
    assert request_hash("https://a/sound-generation", body) != request_hash("https://b/sound-generation", body)
    assert request_hash("https://a/sound-generation", body) != request_hash("https://a/sound-generation", {"text": "x"})
    assert request_hash("https://a/sound-generation", body) == request_hash("https://a/sound-generation", dict(body))


def test_a_torn_ledger_is_survived_and_the_sound_is_made_again(project, monkeypatch):
    """A half-written ledger must not be read as proof that the audio was already bought."""
    client = StubClient()
    only_sounds(monkeypatch, client)
    soundscape(project, only=["ding"])
    ledger = next(project.root.glob("build/sfx/ding.cache.json"))
    ledger.write_text("{not json", encoding="utf-8")
    again = soundscape(project, only=["ding"])
    assert [item.status for item in again.items] == [SoundscapeStatus.GENERATED] and len(client.sounds) == 2
    assert json.loads(ledger.read_text(encoding="utf-8"))["hash"]
