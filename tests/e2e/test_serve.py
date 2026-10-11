"""The preview server answers a pane clip's frame, so a still the author opens under `serve` shows the clip.

    uv run pytest -m e2e tests/e2e/test_serve.py

The pane clip project is copied, its clip is made with the command its `[[clip]]` names, and
`decktalk serve` is run as the subprocess an author runs. Its handler threads answer outside the run
that opened the server, which is where a clip's frame used to fail before any ffmpeg was found.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from decktalk.media import ffmpeg
from decktalk.media.clipframes import FRAME_TYPE
from decktalk.page import CLIP_FRAMES
from decktalk.settings import VideoConfig
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(BASE_BUDGET_SECONDS + FIRST_FETCH_SECONDS))]

FIXTURE = Path(__file__).resolve().parent / "paneclip"
"""The project with one pane clip, whose first second is red."""

CLIP = "deck/clips/tone.mp4"
"""The clip the pane plays, as the page and its `[[clip]]` name it."""

RED = (255, 0, 0)
"""The colour the clip's first second is drawn in."""

TOLERANCE = 40
"""How far one decoded channel may sit from the colour it was drawn in, after one lossy encode."""

REQUEST_SECONDS = 30
"""How long one request may take, which covers a first decode on a cold machine."""

BMP_PIXELS = slice(10, 14)
"""Truth: where a BMP's file header holds the offset its pixels start at, as a little-endian number."""

BMP_SIZE = (slice(18, 22), slice(22, 26))
"""Truth: where a BMP's info header holds its width and its height, as little-endian numbers."""


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The URL `decktalk serve` prints for the pane clip project, with the clip made beside the page."""
    root = tmp_path_factory.mktemp("serve") / HOSTILE_DIRECTORY / "paneclip"
    shutil.copytree(FIXTURE, root)
    out = root / CLIP
    out.parent.mkdir(parents=True, exist_ok=True)
    landings = out.with_suffix(".landings.json")
    make = [sys.executable, "clips/make_clip", str(out), str(landings), ffmpeg.ffmpeg(), str(VideoConfig().fps)]
    subprocess.run(make, cwd=root, check=True)
    command = [sys.executable, "-m", "decktalk", "serve", "--port", "0", "--json"]
    with subprocess.Popen(
        command, cwd=root, env=clean_environ(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as server:
        try:
            said = first_object(server)
            assert said["ok"], said
            yield said["url"]
        finally:
            server.terminate()
            server.wait()


def first_object(server: subprocess.Popen[str]) -> dict[str, Any]:
    """The one object `serve` prints before it starts serving, read as it arrives since the stream stays open."""
    assert server.stdout is not None and server.stderr is not None
    text = ""
    while line := server.stdout.readline():
        text += line
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # silent: the object is not whole yet, so the next line is read.
            continue
    raise AssertionError(server.stderr.read())


def get(url: str) -> tuple[int, str, bytes]:
    """One request against the server, with the status of a refusal rather than an exception."""
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_SECONDS) as response:
            return response.status, response.headers["Content-Type"], response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.headers["Content-Type"], exc.read()


def centre(bmp: bytes) -> tuple[int, int, int]:
    """The red, green and blue of the pixel in the middle of a 24 bit BMP, whose rows are stored bottom up."""
    start = int.from_bytes(bmp[BMP_PIXELS], "little")
    width, height = (int.from_bytes(bmp[at], "little") for at in BMP_SIZE)
    stride = (width * 3 + 3) // 4 * 4
    at = start + (height - 1 - height // 2) * stride + (width // 2) * 3
    blue, green, red = bmp[at : at + 3]
    return red, green, blue


def test_serve_answers_a_pane_clip_s_first_frame_as_a_picture(served):
    """The docs promise a still shows a pane clip's first frame and that `serve` opens pages as the recorder does."""
    status, kind, body = get(f"{served}{CLIP_FRAMES}0/{CLIP}")
    assert status == 200, body
    assert kind == FRAME_TYPE and body.startswith(b"BM")
    shown = centre(body)
    assert all(abs(got - want) <= TOLERANCE for got, want in zip(shown, RED, strict=True)), shown


def test_serve_holds_a_frame_request_to_the_rule_every_file_is(served):
    """A frame is a way to read a file, so it opens only what the page could have fetched itself."""
    assert get(f"{served}{CLIP_FRAMES}0/decktalk.toml")[0] == 403
    assert get(f"{served}{CLIP_FRAMES}0/deck/../script.md")[0] == 403
