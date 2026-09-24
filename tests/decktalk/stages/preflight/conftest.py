"""The part-voiced starter the take plan reads, built once here for the preflight tests.

The plan's subject is which sections it would send to the voice, so the project it reads has to
carry a take index some of whose rows are on disk and one of whose rows is not. `unchanged` is the
other half of that subject: a plan writes nothing, so the bytes it was handed must still be there.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.model import Project
from decktalk.model.script import PUNCT
from decktalk.scaffold import init
from decktalk.speech import register_speech_provider
from decktalk.stages.narrate import take_name, text_hash, words_name


def _planned_scaffold(tmp_path: Path, monkeypatch) -> tuple[Project, dict[str, str]]:
    """The starter with a fake provider and a part-voiced take index: 01 and 03 on disk, 02 stale.

    A take is named by its content hash, so a section whose digest is on disk is cached however it
    is numbered. Returns the project and the path of every file the plan must leave alone, with its
    bytes' hash.
    """

    class PlanVoice:
        name = "plan-voice"

        def speak(self, request):
            raise AssertionError("a plan sent a request")

        def cache_key(self, request):
            return "plan-voice"

    register_speech_provider("plan-voice", lambda context: PlanVoice())
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    root = init(tmp_path / "proj", name="proj").root
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("[voice]\n", "[voice]\nprovider = 'plan-voice'\n", 1))
    p = Project.load(root, environ={})
    cfg = p.settings.narration
    settings = p.voice.api_settings()
    spoken = {s.key: s for s in p.script_sections()[1]}
    p.narration_dir.mkdir(parents=True)
    take_index = Takes(script="script.md", model=cfg.model, output_format=cfg.output_format)
    # 01 and 03 carry the take of their own words. 02's row names a digest nothing wrote, so the
    # text it indexes is stale and the plan sends it again.
    for key, kind in [("01", "real"), ("02", "stale"), ("03", "real")]:
        seg = spoken[key]
        digest = text_hash(seg, cfg, "plan-voice", settings) if kind == "real" else "0123456789abcdef"
        tokens = [t.strip(PUNCT) for t in seg.spoken.split()]
        if kind == "real":
            (p.takes_dir / take_name(digest)).write_bytes(f"take {key}".encode())
            write_words(
                p.takes_dir / words_name(digest),
                [Word(t, round(i * 0.4, 3), round(i * 0.4 + 0.3, 3)) for i, t in enumerate(tokens)],
            )
        take_index.sections[key] = Take(
            index=int(key), chapter=seg.title, file=take_name(digest), words_file=words_name(digest),
            hash=digest, word_count=seg.word_count, estimated_seconds=seg.estimated_seconds(cfg),
            duration_seconds=len(tokens) * 0.4 + 1.3, voiced=True, spoken=seg.spoken,
            sound_end_seconds=len(tokens) * 0.4, lead_seconds=p.lead_seconds(key), tail_seconds=p.tail_seconds(key),
        )  # fmt: skip
    take_index.save(p.takes_path)
    files = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return p, files


def _unchanged(p: Project, files: dict[str, str]) -> bool:
    now = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return now == files


@pytest.fixture
def planned_scaffold():
    """Build the scaffold with a fake provider and a part-voiced take index, ready for a plan to read."""
    return _planned_scaffold


@pytest.fixture
def unchanged():
    """Whether build/narration still holds exactly the bytes the plan was handed."""
    return _unchanged
