"""The projects and rendered rows every assemble test measures, built once here."""

from __future__ import annotations

import pytest

from decktalk.artifacts import Timeline, TimelineSection, Word
from decktalk.model import Project
from decktalk.stages.assemble.cut import RenderedSection

MINIMAL_TOML = """
[project]
name = "t"

[[section]]
number = 0
clip = "media/open.mp4"

[[section]]
number = 1
page = "deck/index.html"
scene = 1

[[section]]
number = 2
page = "deck/index.html"
hold_seconds = 1.5
"""


PAGES_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"

[[section]]
number = 3
page = "deck/index.html"
"""


def write_project(tmp_path, toml: str = MINIMAL_TOML):
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    return tmp_path


MID_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
clip = "media/broll.mp4"

[[section]]
number = 3
page = "deck/index.html"

[[section]]
number = 4
page = "deck/index.html"
"""


TITLED_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
chapter = "Open"
page = "deck/index.html"

[[section]]
number = 2
chapter = "The edit"
clip = "media/before.mov"
words = "media/before.words.json"

[[section]]
number = 3
chapter = "The edit"
page = "deck/index.html"

[[section]]
number = 4
chapter = "Close"
page = "deck/index.html"
"""


def spoken(text: str, start: float = 0.0, step: float = 0.4, gap_after: str | None = None) -> list[Word]:
    words: list[Word] = []
    t = start
    for w in text.split():
        words.append(Word(w, round(t, 3), round(t + 0.3, 3)))
        t += step
        if gap_after is not None and w == gap_after:
            t += 3.0
    return words


def mid_clip_plan(tmp_path):
    """Pages 1, 3 and 4 around a 3-second clip at 2, with the rows and timeline assemble would build."""
    p = Project.load(write_project(tmp_path, MID_CLIP_TOML), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 2.0, 2.0, 1.6, spoken("alpha beta", 0.7)),
            "03": TimelineSection("C", 2.0, 4.5, 2.5, 4.1, spoken("gamma delta", 2.1)),
            "04": TimelineSection("D", 4.5, 6.0, 1.5, 5.8, spoken("epsilon", 4.6)),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "01.webm"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 3.0, "02.mp4 (own audio)", audio=tmp_path / "broll.mp4"),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.52, "03.webm"),
        RenderedSection(p.sections[3], tmp_path / "04.mp4", 1.48, "04.webm"),
    ]
    return p, tl, rows


def titled_clip_rows(tmp_path, *, clip_audio: bool = True):
    """The same four sections with chapter titles, two of which share one."""
    p = Project.load(write_project(tmp_path, TITLED_CLIP_TOML), environ={})
    audio = tmp_path / "media" / "before.mov" if clip_audio else None
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "01.webm"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 3.0, "02.mp4 (own audio)", audio=audio),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.52, "03.webm"),
        RenderedSection(p.sections[3], tmp_path / "04.mp4", 1.48, "04.webm"),
    ]
    return p, rows


@pytest.fixture
def pages_toml() -> str:
    """Three page sections and nothing else, for a test that wants no clip in the way."""
    return PAGES_TOML


@pytest.fixture(name="write_project")
def write_project_fixture():
    """Write a decktalk.toml into a directory and give the directory back."""
    return write_project


@pytest.fixture(name="spoken")
def spoken_fixture():
    """Evenly spaced words, as a silent narration gives them."""
    return spoken


@pytest.fixture(name="mid_clip_plan")
def mid_clip_plan_fixture():
    return mid_clip_plan


@pytest.fixture(name="titled_clip_rows")
def titled_clip_rows_fixture():
    return titled_clip_rows
