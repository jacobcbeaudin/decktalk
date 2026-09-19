"""Stage results built by hand, so a handler is tested with no recording, no ffmpeg and no browser.

Each fixture returns the real result class a stage returns, so the payload a handler prints is the
payload a caller receives, and a test reads it back through `cli.schema` like any other caller.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from decktalk.artifacts import Luma, RecordingChecks, RecordingLog
from decktalk.cli import authoring, video
from decktalk.model import PageSection
from decktalk.stages.record import RecordResult, SectionRecording
from decktalk.stages.verify import CueCheck, CutCheck, StartCheck, VerifyResult
from decktalk.verdicts import SkipReason, Verdict


@pytest.fixture
def stage():
    """The stage module itself, so a test replaces the function the handler calls."""

    def module(name: str) -> ModuleType:
        return importlib.import_module(f"decktalk.stages.{name}")

    return module


@pytest.fixture
def fake_project(monkeypatch, tmp_path):
    """Skip Project.load, so a command runs against the stage function a test installs.

    It carries the paths and the one setting the handlers read, so a handler is exercised whole
    rather than up to its first attribute.
    """
    narration = tmp_path / "build" / "narration"
    project = SimpleNamespace(
        root=tmp_path,
        build=tmp_path / "build",
        path=lambda name: tmp_path / name,
        narration_dir=narration,
        takes_dir=narration,
        takes_path=narration / "takes.json",
        narration_path=narration / "narration.mp3",
        settings=SimpleNamespace(narration=SimpleNamespace(words_per_minute=150)),
        voice=SimpleNamespace(price_per_1000_characters=0.0),
        workspace=SimpleNamespace(
            recording_log=lambda key: tmp_path / "build" / "recordings" / f"{key}.json",
            progress_path=tmp_path / "build" / "progress.jsonl",
        ),
        page_files=["deck/index.html"],
    )
    for module in (authoring, video):
        monkeypatch.setattr(module, "load_project", lambda opts: project)
    return project


@pytest.fixture
def recording_row(tmp_path):
    """One section of a `record` run, carrying whichever verdicts a test wants judged."""

    def row(*verdicts: Verdict, number: int = 1, kept: bool = False) -> SectionRecording:
        checks = RecordingChecks(9.0, 10.5, Luma(y10=100.0, y50=100.0, y90=100.0, max50=200.0), verdicts)
        log = RecordingLog(
            url="u", requested_seconds=10.5, settle_seconds=0.5, load_seconds=0.1, clock_start_seconds=1.4,
            t0_method="cover", checks=checks,
        )  # fmt: skip
        section = PageSection(number=number, page="deck/index.html", scene=str(number))
        path = Path(tmp_path) / "build" / "recordings" / f"{section.key}.webm"
        return SectionRecording(section=section, path=path, log=log, kept=kept)

    return row


@pytest.fixture
def record_result():
    """A whole `record` result around the section rows a test hands it."""

    def result(*rows: SectionRecording) -> RecordResult:
        return RecordResult(sections=list(rows))

    return result


@pytest.fixture
def cue_row():
    """One cue row of `verify`, with the fields a test cares about overridden."""

    def row(check: str, verdict: Verdict, reason: SkipReason | None = None, **fields) -> CueCheck:
        built = dict(
            cue_seconds=1.0,
            final_seconds=1.0,
            changed_percent=1.0,
            control_percent=0.0,
            ok=verdict is Verdict.CHANGED,
            note="",
            offset_ms=0,
        )
        built.update(fields)
        return CueCheck(check=check, verdict=verdict, reason=reason, **built)

    return row


@pytest.fixture
def verify_result(tmp_path):
    """A whole `verify` result around the cue rows a test hands it, over a film that opens and cuts cleanly."""

    def result(*cues: CueCheck) -> VerifyResult:
        return VerifyResult(
            total_seconds=10.0,
            starts=[StartCheck(key="01", start=0.0, probe_at=0.5, yavg=50.0, ymax=200.0, ok=True)],
            cuts=[CutCheck(key="01", cut_at=10.0, rms_db=-120.0, ok=True)],
            cues=list(cues),
            final=Path(tmp_path) / "build" / "out" / "deck.mp4",
        )

    return result
