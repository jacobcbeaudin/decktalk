"""Every packaged project records the same frames and measures the same numbers whether still frames are skipped or not.

    uv run pytest -m scaffold tests/e2e/test_still_frames.py

The recorder writes a frame again without capturing it when the page reports that nothing it draws
changed since the frame before, which is most frames of a narrated deck, and writes a whole still
stretch again without stepping the page to each of its frames. That is only a speed-up if the frame
written again is the frame a capture would have taken. This golden builds the starter and each
shipped example twice with no voice, once skipping still frames as a production build does and once
capturing every frame, and requires every page section to decode to the same frames and verify to
measure the same numbers.

It builds every packaged project twice, so it carries the `scaffold` marker and runs with the suite
that already builds every packaged project. The fixture tests in `tests/decktalk/media/test_stepping.py`
hold each change signal on its own on every pull request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from decktalk.inputs import Inputs
from support.commands import HOSTILE_DIRECTORY, PACKAGED, decktalk, frame_digests, written
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

BUILDS_BUDGET_SECONDS = budget(2 * EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS)
"""How long one project may take here, which is two builds of it."""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(BUILDS_BUDGET_SECONDS)]

NO_AUDIT = 0
"""How often the skipping build captures a frame it skipped anyway, which is never here.

An audited frame is stepped, so an audit cuts every still stretch short, and the skipping build has
to be the path a production build takes, with each stretch as long as the page allows. The golden
compares every frame with the capturing build's, which is a stricter audit than any interval.
"""

SKIPPING = ("--set", "record.skip_still=true")
CAPTURING = ("--set", "record.skip_still=false")

RUN_ONLY = ("run", "elapsed_seconds")
"""What verify answers about its own run rather than about the film, which two builds never share."""


def built(home: Path, name: str, overrides: tuple[str, ...]) -> tuple[Path, dict[str, Any]]:
    """One packaged project written by `init` into its own folder, built with `overrides`, and what verify measured."""
    home.mkdir(parents=True)
    root = written(home, name)
    done = decktalk("--project", str(root), "build", "--no-spend", *overrides, cwd=home, audit_every=NO_AUDIT)
    assert done.get("film"), done
    measured = decktalk("--project", str(root), "verify", cwd=home)
    for spent in RUN_ONLY:
        measured.pop(spent)
    return root, measured


@pytest.mark.parametrize("name", PACKAGED)
def test_every_page_section_records_the_same_frames_with_still_frames_skipped(tmp_path, name):
    """Skipping still frames changes how long a recording takes and nothing in it: every frame and every number."""
    skipping, skipping_verify = built(tmp_path / "skip" / HOSTILE_DIRECTORY, name, SKIPPING)
    capturing, capturing_verify = built(tmp_path / "capture" / HOSTILE_DIRECTORY, name, CAPTURING)
    inputs = Inputs.load(skipping, environ={})
    pages = [section.key for section in inputs.document.page_sections if not section.rendered]
    assert pages, "a packaged project records at least one page section"
    for key in pages:
        relative = inputs.workspace.recording(key).relative_to(skipping)
        fast, slow = frame_digests(skipping / relative), frame_digests(capturing / relative)
        assert len(fast) == len(slow), (name, key)
        assert [k for k, (one, other) in enumerate(zip(fast, slow, strict=True)) if one != other] == [], (name, key)
    assert skipping_verify == capturing_verify
