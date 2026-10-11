"""A pane clip's frames: which frame shows at a second on its clock, and the one decode that hands each over.

The rule for the frame showing at a second is arithmetic and needs no tool. The decode is checked
against the pinned ffmpeg under `media`, on a clip whose every frame is drawn brighter than the one
before, so the brightness of a frame read back says which one it is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.media import ffmpeg
from decktalk.media.clipframes import ClipFrames, frame_index, landings_of

FPS = 25
"""The film's rate, which the clip is decoded at."""

FRAMES = 6
"""How many frames the test clip holds, each brighter than the one before."""

STEP = 40
"""How much brighter each frame of the test clip is drawn than the one before, in the clip's own luma."""


# ---- the frame showing at a second ---------------------------------------------------------------


def test_a_second_shows_the_last_frame_that_has_begun_by_then() -> None:
    assert [frame_index(second, FPS) for second in (0.0, 0.039, 0.04, 0.079, 0.08)] == [0, 0, 1, 1, 2]


def test_a_second_written_to_the_millisecond_still_shows_the_frame_that_starts_on_it() -> None:
    """At thirty frames a second the third frame starts at 0.0667, which a page writes as 0.067 or as 0.066."""
    assert frame_index(round(2 / 30, 3), 30) == 2
    assert frame_index(0.066, 30) == 1


def test_a_second_before_the_clip_starts_shows_its_first_frame() -> None:
    assert frame_index(-1.0, FPS) == 0


def test_a_clip_s_own_landings_sit_beside_it_under_its_name() -> None:
    assert landings_of(Path("deck/clips/tone.mp4")) == Path("deck/clips/tone.landings.json")
    assert landings_of(Path("build/final/larkbill.mp4")) == Path("build/final/larkbill.landings.json")


# ---- the decode ------------------------------------------------------------------------------------


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A clip of six frames whose frame `k` is grey at `k * STEP`, lossless so each reads back exactly."""
    out = tmp_path / "steps.mkv"
    ffmpeg.run(
        "-f", "lavfi", "-i", f"color=c=black:s=16x16:r={FPS}:d={FRAMES / FPS},geq=lum='N*{STEP}':cb=128:cr=128",
        "-c:v", "ffv1", "-pix_fmt", "yuv444p", str(out),
    )  # fmt: skip
    return out


def brightness(bmp: bytes | None) -> int:
    """The blue of a BMP's first pixel, which on a grey frame is its brightness."""
    assert bmp is not None and bmp[:2] == b"BM"
    offset = int.from_bytes(bmp[10:14], "little")
    return bmp[offset]


@pytest.mark.media
def test_frames_asked_in_order_are_each_the_frame_of_their_second(clip: Path) -> None:
    frames = ClipFrames(fps=FPS)
    try:
        seen = [brightness(frames.frame(clip, k / FPS)) for k in range(FRAMES)]
    finally:
        frames.close()
    assert seen == sorted(set(seen)), f"each second shows its own frame, brighter than the last: {seen}"


@pytest.mark.media
def test_a_frame_already_passed_is_decoded_again_from_the_start_and_is_the_same_frame(clip: Path) -> None:
    """A recording split across workers asks for a frame after a later one, and gets the frame the second shows."""
    frames = ClipFrames(fps=FPS)
    try:
        late = frames.frame(clip, 4 / FPS)
        early = frames.frame(clip, 1 / FPS)
        again = frames.frame(clip, 4 / FPS)
    finally:
        frames.close()
    assert brightness(late) > brightness(early) > 0
    assert late == again


@pytest.mark.media
def test_the_last_frame_holds_once_the_clip_has_played(clip: Path) -> None:
    frames = ClipFrames(fps=FPS)
    try:
        last, after = frames.frame(clip, (FRAMES - 1) / FPS), frames.frame(clip, 10.0)
    finally:
        frames.close()
    assert after == last


@pytest.mark.media
def test_a_file_that_is_no_video_has_no_frame(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a film")
    frames = ClipFrames(fps=FPS)
    try:
        assert frames.frame(broken, 0.0) is None
    finally:
        frames.close()


@pytest.mark.media
def test_a_still_s_decode_stops_once_its_one_frame_is_read(clip: Path) -> None:
    frames = ClipFrames(fps=FPS, held=False)
    assert brightness(frames.frame(clip, 0.0)) == 0
    assert frames.decodes == {}
