"""The starter copied and made square or vertical by its one key builds a film of its own frame that verifies clean.

    uv run pytest -m scaffold tests/e2e/test_sizes.py

One project makes one video, so a square cut of a deck is the project copied by hand with `[video]
size` changed. This builds the starter that way with no voice, at each named size but the default,
which every other suite builds, and requires the film, its captioned copy and every recording to be
drawn at that frame, and verify to find nothing in it, as it finds nothing in the wide starter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.findings import Code
from decktalk.media import ffmpeg
from decktalk.template import STARTER
from support.commands import HOSTILE_DIRECTORY, RUNS_PROGRAMS, decktalk, written
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

PLACEHOLDER = {Code.TAKE_MISSING.value}
"""What a build with no spend reports on every section, which is news about the voice and not about the picture."""


@pytest.mark.parametrize(("size", "frame"), [("square", (1080, 1080)), ("vertical", (1080, 1920))])
def test_the_starter_made_square_or_vertical_is_a_film_of_its_own_frame(tmp_path: Path, size: str, frame) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = written(home, STARTER)
    with (root / "decktalk.toml").open("a", encoding="utf-8") as project:
        project.write(f'\n[video]\nsize = "{size}"\n')
    built = decktalk(
        "--project", str(root), "build", "--no-spend", *RUNS_PROGRAMS, "--set", "output.captioned=true", cwd=home
    )
    assert {row["code"] for row in built["findings"]} <= PLACEHOLDER, built["findings"]
    film = root / built["film"]
    assert ffmpeg.probe_size(film) == frame
    assert ffmpeg.probe_size(film.with_name(f"{film.stem}-captioned.mp4")) == frame
    recordings = sorted((root / "build" / "recordings").glob("*.mp4"))
    assert recordings
    assert {ffmpeg.probe_size(one) for one in recordings} == {frame}
    verified = decktalk("--project", str(root), "verify", cwd=home)
    assert verified["findings"] == [], verified["findings"]
