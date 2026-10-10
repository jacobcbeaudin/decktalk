"""A dialogue built with `[output] captioned` has a captioned copy whose captions its own page draws.

    uv run pytest -m e2e tests/e2e/test_captioned.py

The interview is built twice without spending: once as it ships, and once with the captioned copy
asked for. The clean master has to decode to the same frames both times, so asking for the copy
costs the master nothing. The copy has to show every caption in the band at the bottom of the
frame from its first frame to its last, and nothing in that band in a gap between two captions, and
the rest of its picture has to be the master's. The interview's page places the guest's captions on
the right, so the copy also proves the page was told which voice says each caption without the
caption naming it.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from decktalk.errors import Exit
from decktalk.media import ffmpeg
from decktalk.media.frames import changed_count
from support.commands import HOSTILE_DIRECTORY, clean_environ, flat
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(2 * EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

EXAMPLE = "interview"
"""The packaged project in two voices."""

WIDTH, HEIGHT = 480, 270
"""The size frames are compared at, a sixteenth of the film's area, which is plenty to see a line of text."""

BAND_TOP = 0.7
"""Where the caption band begins, as a share of the height: the runtime draws the band at the bottom."""

PICTURE_BOTTOM = 0.6
"""Where the part of the frame no caption reaches ends, as a share of the height."""

LEVEL = 24
"""Truth: a luma step this large is a drawn change and never the encoder's noise."""

SHOWN_PERCENT = 0.5
"""The least share of the band a line of caption text changes."""

STILL_PERCENT = 0.05
"""The most share of a region two encodes of one picture may differ by."""

CUE_RE = re.compile(r"(\d+):(\d+):(\d+)\.(\d+) --> (\d+):(\d+):(\d+)\.(\d+)\n(.*?)(?:\n\n|\Z)", re.S)


@dataclass(frozen=True)
class Cue:
    """One caption of the .vtt as frames: the first it shows on, the one after its last, and who says it."""

    first: int
    after: int
    voice: str | None


def frame_of(stamp: tuple[str, ...], fps: int) -> int:
    """The first frame at or after a WebVTT time, which is the frame a player first shows a caption on."""
    h, m, s, ms = (int(part) for part in stamp)
    return math.ceil((h * 3600 + m * 60 + s) * fps + ms * fps / 1000 - 1e-9)


def vtt_cues(final: Path) -> list[Cue]:
    """Every caption of the film's .vtt as frames, at the rate its placements were cut at."""
    fps = json.loads((final / "placements.json").read_text(encoding="utf-8"))["fps"]
    found = []
    for match in CUE_RE.finditer((final / f"{EXAMPLE}.vtt").read_text(encoding="utf-8")):
        voice = re.search(r"<v ([^>]+)>", match.group(9))
        stamps = match.groups()
        found.append(Cue(frame_of(stamps[0:4], fps), frame_of(stamps[4:8], fps), voice and voice.group(1)))
    return found


def decktalk(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "decktalk", *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd),
        cwd=cwd,
    )


def built(root: Path, home: Path) -> dict[str, object]:
    ran = decktalk("--project", str(root), "build", "--no-spend", "--json", cwd=home)
    assert ran.returncode == Exit.FOUND_NOTHING, ran.stderr
    return flat(ran.stdout)


def frame_digests(video: Path) -> list[str]:
    """One md5 per decoded frame of the picture, which is every frame compared exactly."""
    with machine_tools():
        listing = ffmpeg.raw(*ffmpeg.source(video), "-map", "0:v", "-f", "framemd5", "-").decode()
    return [line.rsplit(",", 1)[1].strip() for line in listing.splitlines() if line and not line.startswith("#")]


def gray_frames(video: Path, indices: set[int]) -> dict[int, bytes]:
    """The luma of each frame `indices` names, at the comparison size."""
    ordered = sorted(indices)
    picked = "+".join(f"eq(n\\,{n})" for n in ordered)
    with machine_tools():
        raw = ffmpeg.raw(
            *ffmpeg.source(video), "-vf", f"select={picked},scale={WIDTH}:{HEIGHT},format=gray",
            "-fps_mode", "passthrough", "-f", "rawvideo", "-",
        )  # fmt: skip
    size = WIDTH * HEIGHT
    assert len(raw) == size * len(ordered), (len(raw), len(ordered))
    return {n: raw[i * size : (i + 1) * size] for i, n in enumerate(ordered)}


def rows(frame: bytes, top: float, bottom: float) -> bytes:
    return frame[round(top * HEIGHT) * WIDTH : round(bottom * HEIGHT) * WIDTH]


def share(a: bytes, b: bytes) -> float:
    return changed_count(a, b, LEVEL) / len(a) * 100


def centre_x(a: bytes, b: bytes) -> float:
    """Where across the frame the changed pixels of a region sit on average, as a share of the width."""
    xs = [i % WIDTH for i, (p, q) in enumerate(zip(a, b, strict=True)) if abs(p - q) > LEVEL]
    return sum(xs) / len(xs) / WIDTH


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    """The interview built as it ships, then again with its captioned copy, and the master's frames from the first."""
    home = tmp_path_factory.mktemp("captioned") / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = home / EXAMPLE
    made = decktalk("init", str(root), "--example", EXAMPLE, "--json", cwd=home)
    assert made.returncode == 0, made.stderr
    first = built(root, home)
    clean = frame_digests(root / str(first["film"]))
    with (root / "decktalk.toml").open("a", encoding="utf-8") as toml:
        toml.write("\n[output]\ncaptioned = true\ncaption_voices = true\n")
    built(root, home)
    return root, clean


def test_the_clean_master_is_the_same_frames_with_the_copy_asked_for(project: tuple[Path, list[str]]) -> None:
    root, clean = project
    final = root / "build" / "final"
    assert frame_digests(final / f"{EXAMPLE}.mp4") == clean
    assert len(frame_digests(final / f"{EXAMPLE}-captioned.mp4")) == len(clean)


def test_every_caption_shows_in_the_band_from_its_first_frame_to_its_last(project: tuple[Path, list[str]]) -> None:
    root, _clean = project
    final = root / "build" / "final"
    cues = vtt_cues(final)
    assert len(cues) > 4, cues
    shown = {n for cue in cues for n in (cue.first, cue.after - 1)}
    gaps = {cue.first - 1 for cue, before in zip(cues[1:], cues, strict=False) if before.after < cue.first}
    assert gaps, "the interview has a pause between two captions"
    master = gray_frames(final / f"{EXAMPLE}.mp4", shown | gaps)
    copy = gray_frames(final / f"{EXAMPLE}-captioned.mp4", shown | gaps)
    for n in sorted(shown):
        band = share(rows(master[n], BAND_TOP, 1), rows(copy[n], BAND_TOP, 1))
        above = share(rows(master[n], 0, PICTURE_BOTTOM), rows(copy[n], 0, PICTURE_BOTTOM))
        assert band > SHOWN_PERCENT, f"frame {n} shows no caption in the band ({band:.3f}%)"
        assert above < STILL_PERCENT, f"frame {n} differs from the master above the band ({above:.3f}%)"
    for n in sorted(gaps):
        assert share(master[n], copy[n]) < STILL_PERCENT, f"frame {n} is between two captions and shows one"


def test_the_page_places_each_caption_by_the_voice_that_says_it(project: tuple[Path, list[str]]) -> None:
    """The interview's page sets the guest's captions on the right and leaves the narrator's centred."""
    root, _clean = project
    final = root / "build" / "final"
    cues = vtt_cues(final)
    guest = [cue for cue in cues if cue.voice == "guest"]
    narrator = [cue for cue in cues if cue.voice == "narrator"]
    assert guest and narrator, cues
    middles = {(cue.first + cue.after) // 2 for cue in guest + narrator}
    master = gray_frames(final / f"{EXAMPLE}.mp4", middles)
    copy = gray_frames(final / f"{EXAMPLE}-captioned.mp4", middles)
    for cue in guest + narrator:
        n = (cue.first + cue.after) // 2
        x = centre_x(rows(master[n], BAND_TOP, 1), rows(copy[n], BAND_TOP, 1))
        if cue.voice == "guest":
            assert x > 0.6, f"the guest's caption at frame {n} sits at {x:.2f} of the width"
        else:
            assert 0.4 < x < 0.6, f"the narrator's caption at frame {n} sits at {x:.2f} of the width"
