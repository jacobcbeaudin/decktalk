"""The assembled project every verify test measures, with each ffmpeg call replaced by a number."""

from __future__ import annotations

import json

import pytest

from decktalk.model import Project

PAGES_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"

[[section]]
number = 3
page = "deck/index.html"
"""


def write_project(tmp_path, toml: str = PAGES_TOML):
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    return tmp_path


def _cue_row(item: str) -> dict[str, object]:
    """One cue-times row from the shorthand "cue@seconds" the verify tests are written in."""
    cue, _, at = item.rpartition("@")
    return {"cue": cue, "on": cue, "at": float(at), "word_at": float(at)}


def build_verify_project(
    tmp_path,
    monkeypatch,
    cue_times: dict[str, str],
    cues: dict | None = None,
    change: float = 0.0,
    toml: str = PAGES_TOML,
):
    """A project with sections 01 and 02 assembled and every ffmpeg measurement replaced."""
    from decktalk.media import ffmpeg as ffmpeg_module
    from decktalk.media import frames as frames_module

    root = write_project(tmp_path, toml)
    p = Project.load(root, environ={})
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    for key in ("01", "02"):
        (p.sections_dir / f"{key}.mp4").write_bytes(b"x")
    p.final.write_bytes(b"x")
    p.narration_dir.mkdir(parents=True)
    sections = {k: [_cue_row(item) for item in v.split(",") if item] for k, v in cue_times.items()}
    p.cue_times_path.write_text(json.dumps({"sections": sections}), encoding="utf-8")
    if cues is not None:
        (root / "cues.json").write_text(json.dumps({"sections": cues}), encoding="utf-8")
    monkeypatch.setattr(ffmpeg_module, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(frames_module, "luma_at", lambda path, t, crop=None: (100.0, 200.0))
    monkeypatch.setattr(frames_module, "changed_pixels_percent", lambda path, t1, t2, **kw: change)
    monkeypatch.setattr(frames_module, "changed_series", lambda *a, **kw: [])
    return p


@pytest.fixture
def verify_project(tmp_path, monkeypatch):
    """A factory for the two-section assembled project, so each test names its own cue times."""

    def make(cue_times, cues=None, change=0.0, toml=PAGES_TOML):
        return build_verify_project(tmp_path, monkeypatch, cue_times, cues=cues, change=change, toml=toml)

    return make


@pytest.fixture
def pages_toml() -> str:
    """The three-page-section project every verify test starts from, for a test that edits it."""
    return PAGES_TOML
