"""Every packaged project's sections, recorded in pieces that start at any frame, are the frames one pass recorded.

    uv run pytest -m scaffold tests/e2e/test_frame_addressing.py

The recorder addresses frame `k` by its index: it steps only the seconds work is due before it and
then `k / fps`, so a piece of a section can start anywhere, which is what a render split across
workers and a re-record from the middle of a section stand on. That holds only if the page at a
second is a function of that second and not of which seconds were shown before it. This golden builds
the starter and each shipped example with no voice, splits every page section at every cue, at a few
frames drawn from a seed and at the frames a path dependence was once seen on, records each piece in
a fresh page, and requires every decoded frame to equal the one pass `build` recorded. It then records
every project again the way `record` splits a section across its workers, in many chunks joined into
one file, and requires the same decoded frames, the same report from the page and the same verify
numbers as the one pass.

It records every packaged project twice over, so it carries the `scaffold` marker and runs with the
suite that already builds every packaged project, rather than on every pull request. The mechanisms
it guards each have a fixture test in `tests/decktalk/media/test_stepping.py` that a pull request runs.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.errors import Exit
from decktalk.inputs import Inputs
from decktalk.media import browser, ffmpeg, stepping
from decktalk.media.origin import Allowed
from decktalk.page import Q
from decktalk.template import EXAMPLES, STARTER
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

PIECES_BUDGET_SECONDS = budget(2 * EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS)
"""How long one project may take here, which is its build and a second recording of every frame it holds."""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(PIECES_BUDGET_SECONDS)]

PACKAGED = [STARTER, *(example.name for example in EXAMPLES if example.shipped)]
"""Every project `decktalk init` writes today, which is the starter and each shipped example."""

SEEDED_SPLITS = 3
"""How many frames drawn from a seed each section is also split at, besides its cues."""

SEEN_SPLITS: dict[tuple[str, str], tuple[int, ...]] = {("lesson", "01"): (1072,), ("starter", "01"): (439,)}
"""Frames a piece once started on and drew a different picture, kept so that each stays a split.

Lesson frame 1072 drew one dial's outline from a tile the continuous pass had partly redrawn, which
a full raster of every redrawn tile settled. Starter frame 439 started a word's fade on the piece's
first frame, because a word on the voice was revealed only on the seconds a step drew, which a
task at each word's own second settled.
"""

STALE_SPAN = 150
"""How many frames from lesson frame 1072 the stale outline lasted, plus a margin, which its own test records."""

ONE_PASS = ("--set", "record.concurrency=1", "--set", "record.skip_still=false")
"""What records every section in one chunk and captures every frame of it.

A section is split no wider than the recorders that record it, so one recorder records it whole, and
a frame nothing changed on is captured rather than written again, so the chunks, which skip such
frames, are held to frames every one of which was drawn.
"""

MANY_CHUNKS = ("--set", "record.concurrency=8", "--set", "record.chunk_frames_min=25")
"""What splits every section of a second or more into chunks, eight workers wide with chunks of one second."""

VERIFY_RUN_FIELDS = ("run", "elapsed_seconds")
"""The fields of verify's answer that name its own run rather than measure the film."""


def decktalk(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One `decktalk` command, with no key, no voice variable and no machine file in reach."""
    return subprocess.run(
        [sys.executable, "-m", "decktalk", *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd),
        cwd=cwd,
    )


class Built(dict[str, Path]):
    """Each packaged project, written by `init` and built with no voice the first time a test asks for it."""

    def __init__(self, factory: pytest.TempPathFactory) -> None:
        super().__init__()
        self.factory = factory

    def __missing__(self, name: str) -> Path:
        home = self.factory.mktemp(name) / HOSTILE_DIRECTORY
        home.mkdir(parents=True)
        root = home / name
        chosen = () if name == STARTER else ("--example", name)
        made = decktalk("init", str(root), "--no-skills", *chosen, cwd=home)
        assert made.returncode == Exit.FOUND_NOTHING, made.stderr
        done = decktalk("--project", str(root), "build", "--no-spend", *ONE_PASS, cwd=home)
        assert json.loads(done.stdout)["film"], done.stderr
        self[name] = root
        return root


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """The packaged projects this module has built, each built once however many tests read it."""
    return Built(tmp_path_factory)


def cue_seconds(url: str) -> list[float]:
    """The second of every cue the recorded page was handed, read from its own address."""
    pairs = parse_qs(urlsplit(url).query).get(Q.CUES.value, [""])[0]
    return [float(pair.rsplit("@", 1)[1]) for pair in pairs.split(",") if "@" in pair]


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded frame of `video`, taken over its rgb24 pixels."""
    rows = ffmpeg.raw(*ffmpeg.source(video), "-f", "framemd5", "-pix_fmt", "rgb24", "-").decode()
    return [line.rsplit(",", 1)[1].strip() for line in rows.splitlines() if line and not line.startswith("#")]


def record_pieces(root: Path, section: str, starts: list[int], end: int, into: Path) -> list[str]:
    """Record `[starts[i], starts[i + 1])` of one section, each piece in a fresh page, and digest every frame.

    Each piece is recorded the way `record` records a chunk, with every frame captured, so nothing is
    written again that the piece did not draw.
    """
    inputs = Inputs.load(root, environ={})
    log = RecordingLog.read(root / "build" / "recordings" / f"{section}.json")
    assert log is not None
    video, recorder = inputs.settings.video, inputs.settings.record
    bounds = [*starts, end]
    digests: list[str] = []
    with (
        machine_tools(),
        browser.chromium(policy=browser.TRUSTED, spend=False, switches=recorder.capture_switches) as real,
    ):
        for first, stop in zip(bounds, bounds[1:], strict=False):
            piece = stepping.record_piece(
                real,
                log.recording.url,
                range(first, stop),
                into / f"{section}-{first}.mp4",
                allowed=Allowed.of(inputs.root, inputs.served_paths()),
                width=video.width,
                height=video.height,
                fps=video.fps,
                frame_wait_seconds=recorder.frame_wait_seconds,
                skip_still=False,
                skip_audit_every=0,
                color_scheme=recorder.color_scheme,
                motion=inputs.settings.motion,
                documents=inputs.documents(),
            )
            digests += frame_digests(piece.path)
    return digests


def splits(name: str, section: str, url: str, fps: int, count: int) -> list[int]:
    """Where one section is split: every cue's frame, a few frames from a seed and every frame once seen to differ."""
    cues = {stepping.frame_count(seconds, fps) for seconds in cue_seconds(url)}
    seeded = set(random.Random(f"{name}/{section}").sample(range(1, count), SEEDED_SPLITS))
    return sorted({0, *(k for k in cues | seeded | set(SEEN_SPLITS.get((name, section), ())) if 0 < k < count)})


def differing(expected: list[str], got: list[str], offset: int) -> list[int]:
    """The frame numbers whose digests differ, counted from `offset`."""
    assert len(got) == len(expected), (len(got), len(expected))
    return [offset + at for at, (one, other) in enumerate(zip(expected, got, strict=True)) if one != other]


def test_the_lesson_from_frame_1072_draws_the_frames_its_one_pass_recording_holds(built, tmp_path):
    """The piece that once drew a dial's outline from a partly redrawn tile for 140 frames draws every one of them."""
    root = built["lesson"]
    first = SEEN_SPLITS[("lesson", "01")][0]
    whole = frame_digests(root / "build" / "recordings" / "01.mp4")
    got = record_pieces(root, "01", [first], first + STALE_SPAN, tmp_path)
    assert differing(whole[first : first + STALE_SPAN], got, first) == []


@pytest.mark.parametrize("name", PACKAGED)
def test_every_section_split_anywhere_records_the_frames_of_one_pass(built, tmp_path, name):
    """Each page section, cut at every cue and at seeded frames and recorded piece by piece, is the one pass."""
    root = built[name]
    inputs = Inputs.load(root, environ={})
    pages = [section.key for section in inputs.document.page_sections if not section.rendered]
    assert pages, "a packaged project records at least one page section"
    for key in pages:
        path = inputs.workspace.recording(key).with_suffix(".json")
        log = RecordingLog.read(path)
        assert log is not None, path
        section = path.stem
        whole = frame_digests(path.with_suffix(".mp4"))
        starts = splits(name, section, log.recording.url, log.recording.fps, len(whole))
        got = record_pieces(root, section, starts, len(whole), tmp_path)
        assert differing(whole, got, 0) == [], (name, section, starts)


def measured(root: Path) -> dict[str, object]:
    """What `verify` measures of the project's film, without the fields that name its own run."""
    done = decktalk("--project", str(root), "verify", cwd=root.parent)
    answer = json.loads(done.stdout)
    assert answer["film"], done.stderr
    return {key: value for key, value in answer.items() if key not in VERIFY_RUN_FIELDS}


@pytest.mark.parametrize("name", PACKAGED)
def test_every_section_recorded_in_many_chunks_is_the_one_pass_in_frames_report_and_verify(built, tmp_path, name):
    """A section split across workers and joined is the section one worker records whole, to the decoded frame.

    The copy is recorded again in chunks of one second on eight workers and built again, so each of
    its sections is a join of several files, and its frames, what its page reported and what verify
    measures of its film are each the one pass.
    """
    root = built[name]
    once = measured(root)
    copy = tmp_path / name
    shutil.copytree(root, copy)
    redone = decktalk("--project", str(copy), "record", "--force", *MANY_CHUNKS, cwd=tmp_path)
    assert json.loads(redone.stdout).get("sections"), redone.stdout
    built_again = decktalk("--project", str(copy), "build", "--no-spend", *MANY_CHUNKS, cwd=tmp_path)
    assert json.loads(built_again.stdout).get("film"), built_again.stdout
    inputs = Inputs.load(root, environ={})
    for section in inputs.document.page_sections:
        if section.rendered:
            continue
        path = inputs.workspace.recording(section.key)
        whole = RecordingLog.read(path.with_suffix(".json"))
        split = RecordingLog.read(copy / path.relative_to(root).with_suffix(".json"))
        assert whole is not None and split is not None, path
        assert whole.recording.pieces == (0,), (name, section.key)
        assert len(split.recording.pieces) > 1, (name, section.key, split.recording.pieces)
        assert differing(frame_digests(path), frame_digests(copy / path.relative_to(root)), 0) == [], (
            name,
            section.key,
            split.recording.pieces,
        )
        assert split.recording.report == whole.recording.report, (name, section.key)
        assert set(split.recording.assets) == set(whole.recording.assets), (name, section.key)
    assert measured(copy) == once
