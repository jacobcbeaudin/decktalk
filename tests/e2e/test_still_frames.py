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

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.errors import Exit
from decktalk.inputs import Inputs
from decktalk.media import ffmpeg
from decktalk.template import EXAMPLES, STARTER
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

BUILDS_BUDGET_SECONDS = budget(2 * EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS)
"""How long one project may take here, which is two builds of it."""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(BUILDS_BUDGET_SECONDS)]

PACKAGED = [STARTER, *(example.name for example in EXAMPLES if example.shipped)]
"""Every project `decktalk init` writes today, which is the starter and each shipped example."""

AUDIT_EVERY = 0
"""How often the skipping build captures a frame it skipped anyway, which is never here.

An audited frame is stepped, so an audit cuts every still stretch short, and the skipping build has
to be the path a production build takes, with each stretch as long as the page allows. The golden
compares every frame with the capturing build's, which is a stricter audit than any interval.
"""

SKIPPING = ("--set", "record.skip_still=true", "--set", f"record.skip_audit_every={AUDIT_EVERY}")
CAPTURING = ("--set", "record.skip_still=false")

RUN_ONLY = ("run", "elapsed_seconds")
"""What verify answers about its own run rather than about the film, which two builds never share."""


def decktalk(*args: str, cwd: Path) -> dict[str, Any]:
    """One `decktalk` command under `--json`, with no key, no voice variable and no machine file in reach."""
    ran = subprocess.run(
        [sys.executable, "-m", "decktalk", *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd),
        cwd=cwd,
    )
    assert ran.stdout.strip(), ran.stderr
    return json.loads(ran.stdout)


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded frame of `video`, taken over its rgb24 pixels."""
    with machine_tools():
        rows = ffmpeg.raw(*ffmpeg.source(video), "-f", "framemd5", "-pix_fmt", "rgb24", "-").decode()
    return [line.rsplit(",", 1)[1].strip() for line in rows.splitlines() if line and not line.startswith("#")]


def built(home: Path, name: str, chosen: tuple[str, ...], overrides: tuple[str, ...]) -> tuple[Path, dict[str, Any]]:
    """One packaged project written by `init` into its own folder, built with `overrides`, and what verify measured."""
    root = home / name
    made = subprocess.run(
        [sys.executable, "-m", "decktalk", "init", str(root), "--no-skills", *chosen, "--json"],
        capture_output=True, text=True, check=False, env=clean_environ(home), cwd=home,
    )  # fmt: skip
    assert made.returncode == Exit.FOUND_NOTHING, made.stderr
    done = decktalk("--project", str(root), "build", "--no-spend", *overrides, cwd=home)
    assert done.get("film"), done
    measured = decktalk("--project", str(root), "verify", cwd=home)
    for spent in RUN_ONLY:
        measured.pop(spent)
    return root, measured


@pytest.mark.parametrize("name", PACKAGED)
def test_every_page_section_records_the_same_frames_with_still_frames_skipped(tmp_path, name):
    """Skipping still frames changes how long a recording takes and nothing in it: every frame and every number."""
    chosen = () if name == STARTER else ("--example", name)
    skipping_home, capturing_home = (tmp_path / side / HOSTILE_DIRECTORY for side in ("skip", "capture"))
    for home in (skipping_home, capturing_home):
        home.mkdir(parents=True)
    skipping, skipping_verify = built(skipping_home, name, chosen, SKIPPING)
    capturing, capturing_verify = built(capturing_home, name, chosen, CAPTURING)
    inputs = Inputs.load(skipping, environ={})
    pages = [section.key for section in inputs.document.page_sections if not section.rendered]
    assert pages, "a packaged project records at least one page section"
    for key in pages:
        relative = inputs.workspace.recording(key).relative_to(skipping)
        fast, slow = frame_digests(skipping / relative), frame_digests(capturing / relative)
        assert len(fast) == len(slow), (name, key)
        assert [k for k, (one, other) in enumerate(zip(fast, slow, strict=True)) if one != other] == [], (name, key)
    assert skipping_verify == capturing_verify
