"""A film in a pane: a clip with its own sound plays inside a page section, under the page and the voice.

    uv run pytest -m e2e tests/e2e/test_pane_clip.py

The project's one page section declares a pane with `data-clip`, and the clip is made at build time
by the command its `[[clip]]` names, because a test ships no video: three seconds of red, green and blue,
a white square crossing its top strip every frame, and a quiet 1 kHz tone. A label is drawn over the
pane's corner and a line under it. The build is read back from the film itself: the clip's frames in
the pane from the cue's second, the label over them, the tone from that second at the narration's
loudness, and a verify with no finding that measured the clip's own landings on the film's clock.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from decktalk.findings import Code
from decktalk.media import ffmpeg
from decktalk.results import SkipReason
from decktalk.settings import AudioConfig, VideoConfig
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(BASE_BUDGET_SECONDS + FIRST_FETCH_SECONDS))]

FIXTURE = Path(__file__).resolve().parent / "paneclip"
"""The project: one page section with a pane, a label over it and a line under it, and the command making its clip."""

CLIP = "deck/clips/tone.mp4"
"""The clip the pane plays, as the page and its `[[clip]]` name it."""

PLACEHOLDER = {Code.TAKE_MISSING.value}
"""What a build with no spend reports, which is news about the voice and not about the picture."""

VIDEO = VideoConfig()
SCALE = VIDEO.width / 1920
"""How the page's 1920 wide canvas is drawn into the film."""

PANE_CENTRE = (round(880 * SCALE), round(430 * SCALE))
"""A pixel in the pane's lower part, which only the clip's colour ever covers."""

LABEL = (round(550 * SCALE), round(200 * SCALE))
"""A pixel of the label the page draws over the pane's corner."""

RED, GREEN, BLUE, WHITE, BLACK = (255, 0, 0), (0, 128, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)
"""The colours the clip and the page are drawn in, as ffmpeg names green and the page names its label."""

TOLERANCE = 40
"""How far one decoded channel may sit from the colour it was drawn in, after two lossy encodes."""

RATE = AudioConfig().sample_rate
"""The rate the film's soundtrack is decoded at."""

TONE_HZ = 1000
"""The clip's tone, which nothing else in the film plays."""

SKIPPING = ("--set", "record.skip_still=true", "--set", "record.skip_audit_every=1")
"""The build every test reads, which writes still frames again and captures every one of them anyway to compare."""

CAPTURING = ("--set", "record.skip_still=false")
"""A second build that captures every frame, which the skipping build has to match frame for frame."""

LOUDNESS_TOLERANCE_LU = 1.5
"""How far the clip's loudness in the film may sit from the narration's level it was matched to."""


def decktalk(*args: str, cwd: Path) -> dict[str, Any]:
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


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    home = tmp_path_factory.mktemp("paneclip") / HOSTILE_DIRECTORY
    root = home / "paneclip"
    shutil.copytree(FIXTURE, root)
    made = decktalk("--project", str(root), "build", "--no-spend", *SKIPPING, cwd=home)
    verified = decktalk("--project", str(root), "verify", cwd=home)
    with machine_tools():
        yield root, made, verified


def film(root: Path) -> Path:
    return root / "build" / "final" / "paneclip.mp4"


def cue_second(root: Path, cue: str) -> float:
    """Where one cue of section 1 falls in the film, from the placements and the resolved cue times."""
    placements = json.loads((root / "build" / "final" / "placements.json").read_text(encoding="utf-8"))
    (section,) = placements["sections"]
    times = json.loads((root / "build" / "cue-times.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row["seconds"] for block in times["sections"] for row in block["cues"]}
    return section["start"] + rows[cue]


def pixel(root: Path, seconds: float, at: tuple[int, int]) -> tuple[int, int, int]:
    """One pixel of the film's frame at `seconds`, decoded the way a viewer's player decodes it."""
    raw = ffmpeg.raw(
        "-ss", f"{seconds:.3f}", *ffmpeg.source(film(root)),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    )  # fmt: skip
    x, y = at
    offset = (y * VIDEO.width + x) * 3
    r, g, b = raw[offset : offset + 3]
    return r, g, b


def near(seen: tuple[int, int, int], wanted: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= TOLERANCE for a, b in zip(seen, wanted, strict=True))


def samples(root: Path) -> np.ndarray:
    """The film's soundtrack as one mono channel of floats."""
    raw = ffmpeg.raw(*ffmpeg.source(film(root)), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-")
    return np.frombuffer(raw, dtype="<f4")


def tone_level(sound: np.ndarray, start: float, end: float) -> float:
    """How strongly the clip's tone sounds between two seconds, as the amplitude of that one frequency."""
    piece = sound[round(start * RATE) : round(end * RATE)]
    t = np.arange(len(piece)) / RATE
    return float(2 * abs(np.mean(piece * np.exp(-2j * math.pi * TONE_HZ * t))))


def loudness(root: Path, start: float, end: float) -> float:
    """The integrated loudness of the film between two seconds, as EBU R128 reads it."""
    said = ffmpeg.stderr(
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", *ffmpeg.source(film(root)),
        "-af", "ebur128", "-f", "null", "-",
    )  # fmt: skip
    summary = said.rsplit("Integrated loudness:", 1)[1]
    return float(summary.split("I:", 1)[1].split("LUFS", 1)[0])


def test_the_build_and_the_verify_find_nothing(built) -> None:
    _root, made, verified = built
    assert {row["code"] for row in made["findings"]} <= PLACEHOLDER, made["findings"]
    assert {row["code"] for row in verified["findings"]} <= PLACEHOLDER, verified["findings"]


def test_the_pane_shows_the_clip_s_first_frame_before_its_cue_and_its_frames_from_the_cue(built) -> None:
    root, _made, _verified = built
    play = cue_second(root, "1.1:play")
    assert near(pixel(root, play - 0.5, PANE_CENTRE), RED), "the pane holds the clip's first frame before its cue"
    assert near(pixel(root, play + 0.5, PANE_CENTRE), RED)
    assert near(pixel(root, play + 1.5, PANE_CENTRE), GREEN), "the clip's second second plays a second after the cue"
    assert near(pixel(root, play + 2.5, PANE_CENTRE), BLUE)
    assert near(pixel(root, play + 3.5, PANE_CENTRE), BLUE), "the pane holds the clip's last frame once it has played"


def test_the_page_draws_its_label_over_the_clip(built) -> None:
    root, _made, _verified = built
    play = cue_second(root, "1.1:play")
    assert near(pixel(root, play + 1.5, LABEL), WHITE)


def test_the_clip_s_sound_starts_on_its_cue_at_the_narration_s_loudness(built) -> None:
    root, _made, _verified = built
    play = cue_second(root, "1.1:play")
    sound = samples(root)
    before, during = tone_level(sound, play - 1.0, play - 0.05), tone_level(sound, play + 0.1, play + 2.9)
    assert during > 100 * max(before, 1e-6), (before, during)
    onset = next(at for at in np.arange(play - 0.2, play + 0.2, 0.005) if tone_level(sound, at, at + 0.02) > during / 2)
    assert abs(onset - play) <= 0.03, f"the tone starts at {onset:.3f}, and its cue is at {play:.3f}"
    # A build with no spend has a placeholder narration and no loudness pass, so the clip is matched to
    # the level the master would bring the narration to.
    measured = loudness(root, play + 0.2, play + 2.8)
    assert abs(measured - AudioConfig().target_lufs) <= LOUDNESS_TOLERANCE_LU, measured


def test_verify_measures_the_pane_s_start_and_the_clip_s_own_landings_on_the_film_s_clock(built) -> None:
    root, _made, verified = built
    play = cue_second(root, "1.1:play")
    rows = {(row["clip"], row["cue"]): row for row in verified["motions"]}
    assert set(rows) == {(None, "1.1:play"), (CLIP, "green"), (CLIP, "blue")}, rows
    assert all(row["offset_seconds"] is not None for row in rows.values())
    assert rows[(CLIP, "green")]["spoken"] == pytest.approx(play + 1.0, abs=0.002)
    assert rows[(CLIP, "blue")]["spoken"] == pytest.approx(play + 2.0, abs=0.002)
    cues = {row["cue"]: row for row in verified["cues"]}
    assert cues["1.1:play"]["skipped"] == SkipReason.MOTION_LANDING.value


def test_the_recording_log_names_the_clip_it_played_and_keys_the_section_on_it(built) -> None:
    root, _made, _verified = built
    log = json.loads((root / "build" / "recordings" / "01.json").read_text(encoding="utf-8"))
    (lane,) = [row for row in log["recording"]["report"]["motions"] if row["kind"] == "clip"]
    assert lane["clip"] == CLIP
    assert CLIP in log["recording"]["assets"]
    assert f"file:{CLIP}" in log["made_from"]


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded frame of `video`, taken over its rgb24 pixels."""
    rows = ffmpeg.raw(*ffmpeg.source(video), "-f", "framemd5", "-pix_fmt", "rgb24", "-").decode()
    return [line.rsplit(",", 1)[1].strip() for line in rows.splitlines() if line and not line.startswith("#")]


def test_the_pane_records_the_same_frames_whether_still_frames_are_skipped_or_captured(built, tmp_path: Path) -> None:
    """A playing clip is a change on every frame it shows, so the skipping build, audited on every frame, matches."""
    root, made, _verified = built
    assert made["ok"], made["error"]
    home = tmp_path / HOSTILE_DIRECTORY
    again = home / "paneclip"
    shutil.copytree(FIXTURE, again)
    captured = decktalk("--project", str(again), "build", "--no-spend", "--to", "record", *CAPTURING, cwd=home)
    assert captured["ok"], captured["error"]
    recording = Path("build") / "recordings" / "01.mp4"
    assert frame_digests(root / recording) == frame_digests(again / recording)
