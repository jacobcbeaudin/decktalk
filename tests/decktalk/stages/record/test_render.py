"""The clips a project makes: what each program is handed, and the file it leaves or does not leave."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.inputs.document import MadeClip
from decktalk.stages.record import render

MAKER = """
import pathlib, sys
out, landings, fps, width, height = sys.argv[1:6]
pathlib.Path(out).write_bytes(f"{fps} {width}x{height}".encode())
pathlib.Path(landings).write_text("[]")
"""


def make(tmp_path: Path, script: str = MAKER) -> Path:
    clip = MadeClip(
        file="deck/clips/tone.mp4",
        command=(sys.executable, "-c", script, "{out}", "{landings}", "{fps}", "{width}", "{height}"),
    )
    return render.make_clip(clip, root=tmp_path, fps=30, width=1280, height=720, timeout=30.0)


def test_a_made_clip_is_handed_the_film_s_rate_and_frame_and_lands_on_its_own_file(tmp_path: Path) -> None:
    """The command writes beside the clip and the whole file is moved onto it, with its landings beside it."""
    made = make(tmp_path)
    assert made == tmp_path / "deck" / "clips" / "tone.mp4"
    assert made.read_bytes() == b"30 1280x720"
    assert (tmp_path / "deck" / "clips" / "tone.landings.json").read_text() == "[]"
    assert sorted(path.name for path in made.parent.iterdir()) == ["tone.landings.json", "tone.mp4"]


def test_a_made_clip_whose_command_fails_leaves_no_file_to_be_taken_for_a_clip(tmp_path: Path) -> None:
    script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'half'); sys.exit(3)"
    with pytest.raises(ToolError, match=r"the command of deck/clips/tone\.mp4 failed"):
        make(tmp_path, script)
    assert list((tmp_path / "deck" / "clips").iterdir()) == []
