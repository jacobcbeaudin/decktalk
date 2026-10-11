"""The programs a project names: a section's renderer and a clip's maker, what each is handed, what it hands back,
and what it may not do."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.inputs.document import MadeClip
from decktalk.media.environment import children_see
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


WHERE = """
import json, os, pathlib, sys
named, out, seen = sys.argv[1:4]
pathlib.Path(seen).write_text(json.dumps({"cwd": os.getcwd(), "named": named, "env": dict(os.environ)}))
pathlib.Path(out).write_bytes(b"video")
"""


def test_a_program_runs_in_a_scratch_folder_with_project_files_named_absolutely_and_no_proxy(tmp_path: Path) -> None:
    """A program that runs in the project finds its .env by a relative path, and a proxy URL can carry a password."""
    root = tmp_path / "project"
    (root / "render").mkdir(parents=True)
    (root / "render" / "draw").write_text("print()")
    (root / ".env").write_text("ELEVENLABS_API_KEY=sentinel")
    seen = tmp_path / "seen.json"
    machine = {"PATH": "/usr/bin", "HTTPS_PROXY": "http://user:pass@proxy:3128", "no_proxy": "localhost"}
    with children_see(machine):
        render.run_program(
            (sys.executable, "-c", WHERE, "render/draw", "{out}", str(seen)),
            root=root, out=tmp_path / "out.mp4", words={"out": lambda: str(tmp_path / "out.mp4")}, timeout=30.0,
            what="section 3",
        )  # fmt: skip
    ran = json.loads(seen.read_text())
    assert Path(ran["cwd"]).resolve() != root.resolve()
    assert not (Path(ran["cwd"]) / ".env").exists()
    assert ran["named"] == str((root / "render" / "draw").absolute())
    assert ran["env"]["PATH"] == "/usr/bin"
    assert not {"HTTPS_PROXY", "NO_PROXY"} & {name.upper() for name in ran["env"]}


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
