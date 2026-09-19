"""Hand-built stage results, so a handler is tested with no recording, no ffmpeg and no browser."""

from __future__ import annotations

import importlib
from types import ModuleType, SimpleNamespace

import pytest

from decktalk.cli import authoring, video
from decktalk.verdicts import Findings, Verdict


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
        timeline_path=narration / "timeline.json",
        settings=SimpleNamespace(narration=SimpleNamespace(words_per_minute=150)),
        voice=SimpleNamespace(price_per_1000_characters=0.0),
        workspace=SimpleNamespace(
            recording_log=lambda key: tmp_path / "build" / "recordings" / f"{key}.json",
            progress_path=tmp_path / "build" / "progress.jsonl",
        ),
    )
    for module in (authoring, video):
        monkeypatch.setattr(module, "load_project", lambda opts: project)
    return project


@pytest.fixture
def recording_row():
    """One section of a `record` run, carrying whichever verdicts a test wants judged."""

    def row(*verdicts: Verdict) -> SimpleNamespace:
        luma = SimpleNamespace(y10=100.0, y50=100.0, y90=100.0, max50=200.0)
        checks = SimpleNamespace(duration_seconds=9.0, wanted_seconds=10.5, luma=luma, verdicts=verdicts)
        log = SimpleNamespace(
            checks=checks, page_errors=[], assets=[], trim_seconds=1.4, t0_guessed=False, t0_method="cover"
        )
        return SimpleNamespace(
            key="01",
            kept=False,
            log=log,
            path=None,
            verdicts=verdicts,
            label=" ".join(verdicts) or "ok",
            to_dict=lambda root: {"key": "01", "verdicts": list(verdicts)},
        )

    return row


@pytest.fixture
def record_result():
    """A whole `record` result around the section rows a test hands it."""

    def result(*rows: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            sections=list(rows),
            kept_sections=[r for r in rows if r.kept],
            page_errors=[r for r in rows if r.log.page_errors],
            findings=Findings.of(v for r in rows for v in r.verdicts),
            to_dict=lambda root: {"recordings": [r.to_dict(root) for r in rows]},
        )

    return result


@pytest.fixture
def cue_row():
    """One cue row of `verify`, with the fields a test cares about overridden."""

    def row(check: str, verdict: Verdict, **fields) -> SimpleNamespace:
        built = dict(
            check=check,
            cue_seconds=1.0,
            final_seconds=1.0,
            changed_percent=1.0,
            control_percent=0.0,
            ok=verdict == Verdict.CHANGED,
            note="",
            offset_ms=0,
            av_ms=None,
            verdict=verdict,
            reason=None,
        )
        built.update(fields)
        return SimpleNamespace(**built)

    return row


@pytest.fixture
def verify_result():
    """A whole `verify` result around the cue rows a test hands it."""

    def result(*cues: SimpleNamespace) -> SimpleNamespace:
        start = SimpleNamespace(key="01", start=0.0, probe_at=0.5, yavg=50.0, ymax=200.0, ok=True, verdict=Verdict.OK)
        cut = SimpleNamespace(key="01", cut_at=10.0, rms_db=-120.0, ok=True, verdict=Verdict.QUIET)
        return SimpleNamespace(
            total_seconds=10.0,
            starts=[start],
            cuts=[cut],
            cues=list(cues),
            black_starts=0,
            ok=all(c.verdict in (Verdict.CHANGED, Verdict.SKIPPED) for c in cues),
            seams=[],
            recordings=[],
            recorded_findings=Findings(),
            film_findings=Findings.of([start.verdict, cut.verdict, *(c.verdict for c in cues)]),
            findings=Findings.of([start.verdict, cut.verdict, *(c.verdict for c in cues)]),
            to_dict=lambda root: {"cues": [{"cue": c.check, "verdict": c.verdict} for c in cues]},
        )

    return result
