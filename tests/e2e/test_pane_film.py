"""A film DeckTalk built, played in a pane of another film, measured there as fully as it measured itself.

    uv run pytest -m e2e tests/e2e/test_pane_film.py

The project's `[[clip]]` builds the small DeckTalk project in `film/` with no voice, a page of two
reveals and no motion, and copies the built film and what the build wrote beside it into the deck. The
pane plays it from a cue, and the film carries every landing its own verify measured, so this verify
measures each of its reveals again on this film's clock.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from decktalk.findings import Code
from decktalk.media import ffmpeg
from decktalk.media.frames import changed_count
from support.commands import HOSTILE_DIRECTORY, decktalk
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(BASE_BUDGET_SECONDS + FIRST_FETCH_SECONDS))]

FIXTURE = Path(__file__).resolve().parent / "panefilm"
"""The project: one page section with a pane, and the DeckTalk project in `film/` its clip is built from."""

CLIP = "deck/clips/film.mp4"
"""The film the pane plays, as the page and its `[[clip]]` name it."""

PLACEHOLDER = {Code.TAKE_MISSING.value}
"""What a build with no spend reports, which is news about the voice and not about the picture."""


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    home = tmp_path_factory.mktemp("panefilm") / HOSTILE_DIRECTORY
    root = home / "panefilm"
    shutil.copytree(FIXTURE, root)
    made = decktalk("--project", str(root), "build", "--no-spend", cwd=home)
    verified = decktalk("--project", str(root), "verify", cwd=home)
    with machine_tools():
        yield root, made, verified


def seconds_of(root: Path, cue: str) -> float:
    """Where one cue of section 1 of a project falls on its film's clock."""
    placements = json.loads((root / "build" / "final" / "placements.json").read_text(encoding="utf-8"))
    (section,) = placements["sections"]
    times = json.loads((root / "build" / "cue-times.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row["seconds"] for block in times["sections"] for row in block["cues"]}
    return section["start"] + rows[cue]


def test_the_build_and_the_verify_find_nothing(built) -> None:
    _root, made, verified = built
    assert made["ok"], made["error"]
    assert {row["code"] for row in made["findings"]} <= PLACEHOLDER, made["findings"]
    assert {row["code"] for row in verified["findings"]} <= PLACEHOLDER, verified["findings"]


def test_verify_measures_every_reveal_of_the_film_in_its_pane_on_this_film_s_clock(built) -> None:
    """The film is two reveals and no motion, and its landings file carries both, so both are measured here."""
    root, _made, verified = built
    play = seconds_of(root, "1.1:play")
    rows = {row["cue"]: row for row in verified["motions"] if row["clip"] == CLIP}
    assert set(rows) == {"1.1:square", "1.1:bar"}, verified["motions"]
    for cue, row in rows.items():
        assert row["kind"] == "depart"
        assert row["spoken"] == pytest.approx(play + seconds_of(root / "film", cue), abs=0.002)
        assert row["offset_seconds"] is not None and abs(row["offset_seconds"]) <= 0.08, row


# ---- the film's speech in the captions ---------------------------------------------------------------

VTT_CUE = re.compile(r"(\d+):(\d+):(\d+)\.(\d+) --> (\d+):(\d+):(\d+)\.(\d+)\n(.*?)(?:\n\n|\Z)", re.S)


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    text: str


def captions(vtt: Path) -> list[Caption]:
    """Every caption of a .vtt, with its seconds and its text as written."""

    def seconds(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    return [
        Caption(seconds(*match.groups()[0:4]), seconds(*match.groups()[4:8]), match.group(9).strip())
        for match in VTT_CUE.finditer(vtt.read_text(encoding="utf-8"))
    ]


def test_the_film_s_own_captions_are_in_this_film_s_captions_from_the_pane_s_cue(built) -> None:
    """The film's speech is captioned as the film captioned it, on this film's clock, naming nobody."""
    root, _made, _verified = built
    play = seconds_of(root, "1.1:play")
    own = captions(root / "film" / "build" / "final" / "film.vtt")
    here = captions(root / "build" / "final" / "panefilm.vtt")
    assert own, "the film captions its own speech"
    for one in own:
        moved = [row for row in here if row.text == one.text]
        assert moved, f"{one.text!r} is in no caption of this film: {here}"
        assert moved[0].start == pytest.approx(play + one.start, abs=0.002)
        assert moved[0].end == pytest.approx(play + one.end, abs=0.002)
    assert not any("<v " in row.text for row in here), "no caption names a voice"
    srt = (root / "build" / "final" / "panefilm.srt").read_text(encoding="utf-8")
    assert all(one.text in srt for one in own)


WIDTH, HEIGHT = 480, 270
"""The size frames are compared at, which is plenty to see a line of text."""

BAND_TOP = 0.7
"""Where the caption band begins, as a share of the height: the runtime draws the band at the bottom."""

LEVEL = 24
"""Truth: a luma step this large is a drawn change and never the encoder's noise."""

SHOWN_PERCENT = 0.5
"""The least share of the band a line of caption text changes."""


def band(video: Path, second: float) -> bytes:
    """The caption band of the frame at `second`, in luma at the comparison size."""
    with machine_tools():
        raw = ffmpeg.raw(
            "-ss", f"{second:.3f}", *ffmpeg.source(video), "-frames:v", "1",
            "-vf", f"scale={WIDTH}:{HEIGHT},format=gray", "-f", "rawvideo", "-",
        )  # fmt: skip
    return raw[round(BAND_TOP * HEIGHT) * WIDTH : HEIGHT * WIDTH]


def test_the_captioned_copy_draws_the_film_s_captions_over_the_pane(built) -> None:
    root, _made, _verified = built
    play = seconds_of(root, "1.1:play")
    final = root / "build" / "final"
    for one in captions(root / "film" / "build" / "final" / "film.vtt"):
        middle = play + (one.start + one.end) / 2
        clean, copy = band(final / "panefilm.mp4", middle), band(final / "panefilm-captioned.mp4", middle)
        drawn = changed_count(clean, copy, LEVEL) / len(clean) * 100
        assert drawn > SHOWN_PERCENT, f"{one.text!r} is not drawn at {middle:.3f} s: {drawn:.2f} percent"
