"""Every project `decktalk init` writes records and verifies with no voice.

This is the promise the scaffold makes: a fresh project builds on the machine it was written on,
with no API key, no spend and no file of the author's. The starter takes about a minute and the
lesson example a few, so the suite carries the `scaffold` marker and is the one suite
`scripts/check.py` leaves out. It is run by hand before a release. `tests/test_scaffold_data.py`
checks the same projects on every pull request without building them.

    uv run pytest -m scaffold
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from decktalk import build, verify
from decktalk.model import Project
from decktalk.scaffold import EXAMPLES, init
from decktalk.verdicts import Verdict

pytestmark = pytest.mark.scaffold
"""One marker, not three. A suite is picked by one name, and `scaffold` is the slow one.
It needs Chromium and ffmpeg all the same, so `decktalk install` runs before it."""

SHIPPED = [None, *[e.name for e in EXAMPLES if e.shipped]]


@pytest.mark.parametrize("example_name", SHIPPED)
def test_a_packaged_project_builds_and_verifies_without_a_voice(tmp_path, monkeypatch, example_name):
    """`build --no-voice` then `verify --strict` on a project straight out of `init`."""
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    name = example_name or "starter"
    root = init(tmp_path / name, name=name, example_name=example_name).root

    started = time.monotonic()
    project = Project.load(root, environ={})
    result = build(project, silent=True)
    seconds = time.monotonic() - started
    assert result.findings.certain == 0, result.findings
    final = Path(project.final)
    assert final.is_file() and final.stat().st_size > 0

    # Nothing the runtime could not honor, on any page of any section.
    assert result.recordings is not None
    for recording in result.recordings.sections:
        assert recording.log is not None, recording.key
        assert recording.log.warnings == [], recording.log.warnings
        assert recording.log.page_errors == [], recording.log.page_errors

    # Read the finished file back. No packaged project may raise a certain finding, because that is
    # a cue that did not land. The starter is the page every author copies, so it passes `--strict`
    # as well. An example is a project that was really made, and its art is its own, so a reveal of
    # its that sits at the measurement floor may be uncertain: what it may never be is a missed cue.
    checked = verify(project)
    assert checked.findings.certain == 0, checked.findings
    thin = [row for row in checked.to_dict(root)["cues"] if row["verdict"] == Verdict.THIN_CHANGE]
    if example_name is None:
        assert checked.findings.uncertain == 0, checked.findings
    else:
        assert checked.findings.uncertain == len(thin), checked.findings
        assert len(thin) <= 1, thin
    print(f"\n{name}: built in {seconds:.0f}s")
