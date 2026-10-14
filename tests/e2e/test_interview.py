"""The interview example, a narrator and a guest in dialogue, builds on placeholders, says who reads what, and captions.

    uv run pytest -m e2e tests/e2e/test_interview.py

The interview is the one packaged project whose section reads in two voices, and both of its voices
ship with an empty id, so a fresh copy built without spending plays one placeholder per turn and
names, for each turn, the id that would let it be bought. This runs on every pull request, because
the turn-by-turn join, the per-turn findings and the reports that name each voice are what a
one-voice project never exercises. Nothing here needs a key, a network or a voice.

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
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from decktalk.errors import Exit
from decktalk.findings import Code
from decktalk.media import ffmpeg
from decktalk.media.frames import changed_count, frame_index
from support.caption_files import caption_cues
from support.commands import HOSTILE_DIRECTORY, codes, flat, frame_digests, run, written
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(2 * EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

EXAMPLE = "interview"
"""The packaged project this file builds."""

DIALOGUE = ("narrator", "guest", "narrator", "guest")
"""The voices of the interview's second section, turn by turn."""

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


@dataclass(frozen=True)
class Built:
    """One fresh interview built without spending, what the build reported, and its master's frames.

    It is built a second time with the captioned copy asked for, so `root` holds the second build.
    """

    root: Path
    home: Path
    report: dict[str, Any]
    clean: list[str]

    @property
    def final(self) -> Path:
        return self.root / "build" / "final"


def build(root: Path, home: Path) -> dict[str, Any]:
    """`build --no-spend` of the interview, and what it reported."""
    ran = run("--project", str(root), "build", "--no-spend", "--json", cwd=home)
    assert ran.returncode == Exit.FOUND_NOTHING, ran.stderr
    return flat(ran.stdout)


def answer(built: Built, *args: str) -> dict[str, Any]:
    """The one object a read-only command printed under `--json` for the built interview."""
    ran = run("--project", str(built.root), *args, "--json", cwd=built.home)
    return flat(ran.stdout, args)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """`init --example interview`, `build --no-spend`, and the build again with the captioned copy, once."""
    home = tmp_path_factory.mktemp("interview") / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = written(home, EXAMPLE)
    report = build(root, home)
    clean = frame_digests(root / report["film"])
    with (root / "decktalk.toml").open("a", encoding="utf-8") as toml:
        toml.write("\n[output]\ncaptioned = true\ncaption_voices = true\n")
    build(root, home)
    return Built(root=root, home=home, report=report, clean=clean)


def test_the_interview_builds_with_no_spend_one_placeholder_per_turn(built: Built) -> None:
    film = built.root / built.report["film"]
    assert film.is_file() and film.stat().st_size > 0
    rows = json.loads((built.root / "build" / "narrate" / "takes.json").read_text(encoding="utf-8"))["sections"]
    assert [(row["section"], row["turn"]) for row in rows] == [(1, 1), (2, 1), (2, 2), (2, 3), (2, 4), (3, 1)]
    assert tuple(row["voice"] for row in rows if row["section"] == 2) == DIALOGUE
    assert not any(row["voiced"] for row in rows)
    missing = [row["message"] for row in built.report["findings"] if row["code"] == Code.TAKE_MISSING.value]
    assert len(missing) == len(rows)
    for voice in ("narrator", "guest"):
        assert any(
            f"which {voice} reads" in message and f"Name the voice {voice} in [voices.{voice}] id" in message
            for message in missing
        ), voice


def test_the_interview_says_who_reads_what(built: Built) -> None:
    status = answer(built, "status")
    assert [(voice["name"], voice["default"], voice["named"]) for voice in status["voices"]] == [
        ("narrator", True, False),
        ("guest", False, False),
    ]
    second = next(section for section in status["sections"] if section["section"] == 2)
    assert second["voices"] == ["narrator", "guest"]
    assert tuple(turn["voice"] for turn in second["turns"]) == DIALOGUE

    words = answer(built, "words", "--section", "2")
    (section,) = words["sections"]
    assert tuple(turn["voice"] for turn in section["turns"]) == DIALOGUE
    assert sum(turn["word_count"] for turn in section["turns"]) == len(section["words"])

    checked = answer(built, "check", "--no-pages")
    voice_codes = {Code.SCRIPT_VOICE_UNKNOWN, Code.SCRIPT_VOICE_UNMARKED, Code.SCRIPT_VOICE_BARE, Code.VOICE_UNUSED}
    assert not voice_codes & set(codes(checked)), checked["findings"]


# ---- the captioned copy ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    """One caption of the .vtt as frames: the first it shows on, the one after its last, and who says it."""

    first: int
    after: int
    voice: str | None


def vtt_cues(final: Path) -> list[Cue]:
    """Every caption of the film's .vtt as frames, at the rate its placements were cut at."""
    rate = Fraction(json.loads((final / "placements.json").read_text(encoding="utf-8"))["fps"])
    found = []
    for caption in caption_cues(final / f"{EXAMPLE}.vtt"):
        voice = re.search(r"<v ([^>]+)>", caption.text)
        first, after = (frame_index(at, rate, float("inf")) for at in (caption.start, caption.end))
        found.append(Cue(first, after, voice and voice.group(1)))
    return found


def gray_frames(video: Path, indices: set[int]) -> dict[int, bytes]:
    """The luma of each frame `indices` names, at the comparison size."""
    ordered = sorted(indices)
    picked = "+".join(f"eq(n\\,{n})" for n in ordered)
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


def test_the_clean_master_is_the_same_frames_with_the_copy_asked_for(built: Built) -> None:
    assert frame_digests(built.final / f"{EXAMPLE}.mp4") == built.clean
    assert len(frame_digests(built.final / f"{EXAMPLE}-captioned.mp4")) == len(built.clean)


def test_every_caption_shows_in_the_band_from_its_first_frame_to_its_last(built: Built) -> None:
    cues = vtt_cues(built.final)
    assert len(cues) > 4, cues
    shown = {n for cue in cues for n in (cue.first, cue.after - 1)}
    gaps = {cue.first - 1 for cue, before in zip(cues[1:], cues, strict=False) if before.after < cue.first}
    assert gaps, "the interview has a pause between two captions"
    master = gray_frames(built.final / f"{EXAMPLE}.mp4", shown | gaps)
    copy = gray_frames(built.final / f"{EXAMPLE}-captioned.mp4", shown | gaps)
    for n in sorted(shown):
        band = share(rows(master[n], BAND_TOP, 1), rows(copy[n], BAND_TOP, 1))
        above = share(rows(master[n], 0, PICTURE_BOTTOM), rows(copy[n], 0, PICTURE_BOTTOM))
        assert band > SHOWN_PERCENT, f"frame {n} shows no caption in the band ({band:.3f}%)"
        assert above < STILL_PERCENT, f"frame {n} differs from the master above the band ({above:.3f}%)"
    for n in sorted(gaps):
        assert share(master[n], copy[n]) < STILL_PERCENT, f"frame {n} is between two captions and shows one"


def test_the_page_places_each_caption_by_the_voice_that_says_it(built: Built) -> None:
    """The interview's page sets the guest's captions on the right and leaves the narrator's centred."""
    cues = vtt_cues(built.final)
    guest = [cue for cue in cues if cue.voice == "guest"]
    narrator = [cue for cue in cues if cue.voice == "narrator"]
    assert guest and narrator, cues
    middles = {(cue.first + cue.after) // 2 for cue in guest + narrator}
    master = gray_frames(built.final / f"{EXAMPLE}.mp4", middles)
    copy = gray_frames(built.final / f"{EXAMPLE}-captioned.mp4", middles)
    for cue in guest + narrator:
        n = (cue.first + cue.after) // 2
        x = centre_x(rows(master[n], BAND_TOP, 1), rows(copy[n], BAND_TOP, 1))
        if cue.voice == "guest":
            assert x > 0.6, f"the guest's caption at frame {n} sits at {x:.2f} of the width"
        else:
            assert 0.4 < x < 0.6, f"the narrator's caption at frame {n} sits at {x:.2f} of the width"
