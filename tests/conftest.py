"""Options and fixtures shared by every test module."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Word
from decktalk.model import Project


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--gate-timing",
        action="store_true",
        default=False,
        help="fail the pipeline test on OFF CUE on every platform, as it does on Linux by itself",
    )


def _planned_scaffold(tmp_path: Path, monkeypatch) -> tuple[Project, dict[str, str]]:
    """The scaffold with a fake provider and a part-voiced take index: 01, 02 and 09 on disk, 03 stale.

    A take is named by its content hash, so a section whose digest is on disk is cached however it
    is numbered, and 09's take is the one written from 09's own words. Returns the project and the
    path of every file the plan must leave alone, with its bytes' hash.
    """
    import hashlib

    from decktalk.artifacts import write_words
    from decktalk.model.script import PUNCT
    from decktalk.scaffold import init
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import take_name, text_hash, words_name

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
    root = init(tmp_path / "proj", name="proj")
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("[voice]\n", "[voice]\nprovider = 'plan-voice'\n", 1))
    p = Project.load(root, environ={})
    cfg = p.settings.narration
    settings = p.voice.api_settings()
    spoken = {s.key: s for s in p.script_sections()[1]}
    p.narration_dir.mkdir(parents=True)
    take_index = Takes(script="script.md", model=cfg.model, output_format=cfg.output_format)
    # 01, 02 and 09 carry the take of their own words. 03's row names a digest nothing wrote, so the
    # text it indexes is stale and the plan sends it again.
    for key, kind in [("01", "real"), ("02", "real"), ("03", "stale"), ("09", "real")]:
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
            lead_seconds=p.lead_seconds(key),
        )  # fmt: skip
    take_index.save(p.takes_path)
    files = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.narration_dir.iterdir())}
    return p, files


def _unchanged(p: Project, files: dict[str, str]) -> bool:
    import hashlib

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
