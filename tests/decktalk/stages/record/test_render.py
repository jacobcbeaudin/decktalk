"""A section whose picture an external command draws: what it is handed, what it hands back, and what it may not do."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from decktalk.errors import InputError, ToolError
from decktalk.results import Word
from decktalk.stages.record import render

WORDS = (Word(word="one", start=0.5, end=0.8), Word(word="two", start=1.1, end=1.4))
CUES = {"3.1:one": 0.5, "3.1:two": 1.1}


def test_the_command_is_handed_the_section_s_words_and_cue_seconds_on_its_own_clock() -> None:
    """A renderer keys every keyframe to a cue's second, so it is given exactly what the page would have been."""
    document = render.motion_document(fps=25, width=1920, height=1080, seconds=2.0, words=WORDS, cues=CUES)
    assert document == {
        "fps": 25,
        "width": 1920,
        "height": 1080,
        "seconds": 2.0,
        "frames": 50,
        "words": [{"word": "one", "start": 0.5, "end": 0.8}, {"word": "two", "start": 1.1, "end": 1.4}],
        "cues": {"3.1:one": 0.5, "3.1:two": 1.1},
    }


RENDERER = """
import json, pathlib, sys
motion, out, landings, kept = sys.argv[1:5]
pathlib.Path(kept).write_text(pathlib.Path(motion).read_text())
pathlib.Path(out).write_bytes(b"video")
if landings != "-":
    rows = [{"id": "bar-1", "kind": "motion", "box": {"x": 0, "y": 0, "w": 100, "h": 50},
             "departs": 0.5, "arrives": 0.9, "landings": [{"cue": "3.1:one", "kind": "depart", "at": 0.5}]}]
    pathlib.Path(landings).write_text(json.dumps(rows))
"""


def run(tmp_path: Path, *, landings: bool = True, script: str = RENDERER) -> tuple[render.Rendered, Path]:
    kept = tmp_path / "kept.json"
    command = (sys.executable, "-c", script, "{motion}", "{out}", "{landings}" if landings else "-", str(kept))
    document = render.motion_document(fps=25, width=1920, height=1080, seconds=2.0, words=WORDS, cues=CUES)
    done = render.run_command(
        command, root=tmp_path, work=tmp_path / "render" / "03", out=tmp_path / "03.mp4", document=document,
        timeout=30.0, label="section 3",
    )  # fmt: skip
    return done, kept


def test_the_command_writes_the_video_and_reads_the_motion_document_it_was_named(tmp_path: Path) -> None:
    done, kept = run(tmp_path)
    assert (tmp_path / "03.mp4").read_bytes() == b"video"
    assert json.loads(kept.read_text())["cues"] == CUES
    assert [(lane.kind, lane.landings[0].cue) for lane in done.motions] == [("motion", "3.1:one")]


def test_a_command_that_writes_no_landings_reports_none(tmp_path: Path) -> None:
    """Without landings verify cannot measure the section's cues, which it then says rather than passing them."""
    done, _kept = run(tmp_path, landings=False)
    assert done.motions == ()


def test_a_command_that_fails_is_a_tool_error_naming_the_section(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="section 3") as raised:
        run(tmp_path, script="import sys; sys.stderr.write('no such font'); sys.exit(2)")
    assert "no such font" in str(raised.value)


def test_a_command_that_writes_no_video_is_a_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="wrote no video"):
        run(tmp_path, script="pass")


def test_a_host_that_does_not_trust_the_project_refuses_to_run_its_command() -> None:
    """A command is a program the project names, which a service rendering a stranger's project never runs."""
    with pytest.raises(InputError, match="untrusted"):
        render.admitted("untrusted", number=3)
    render.admitted("trusted", number=3)
