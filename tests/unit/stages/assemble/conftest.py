"""The projects and rendered rows every assemble test measures, built once here."""

from __future__ import annotations

import pytest

from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.model import Project
from decktalk.stages.assemble.cut import RenderedSection

MINIMAL_TOML = """
[project]
name = "t"

[narration]
lead_seconds = 0

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

[narration]
lead_seconds = 0

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

[narration]
lead_seconds = 0

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

[narration]
lead_seconds = 0

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


def take_index(project: Project, rows: dict[str, tuple[str, float, float | None, list[Word]]]) -> Takes:
    """A take index built from (chapter, span, speech end, words) per section, with its words on disk.

    Every span is the take alone, because the projects here set `[narration] lead_seconds = 0`, so a
    section starts where the one before it ended, and the words are written where the take names
    them, which is what every reader of the clock opens.
    """
    project.takes_dir.mkdir(parents=True, exist_ok=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    for key, (chapter, span, speech_end_seconds, words) in rows.items():
        name = f"{key}-take"
        write_words(project.takes_dir / f"{name}.words.json", words)
        index.sections[key] = Take(
            index=int(key),
            chapter=chapter,
            file=f"{name}.mp3",
            words_file=f"{name}.words.json",
            hash=f"h{key}",
            word_count=len(words),
            estimated_seconds=span,
            duration_seconds=span,
            speech_end_seconds=speech_end_seconds,
        )
    index.total_seconds = round(sum(index.span(k) or 0.0 for k in index.keys), 3)
    return index


def mid_clip_plan(tmp_path):
    """Pages 1, 3 and 4 around a 3-second clip at 2, with the rows and take index assemble would build."""
    p = Project.load(write_project(tmp_path, MID_CLIP_TOML), environ={})
    takes = take_index(
        p,
        {
            "01": ("A", 2.0, 1.6, spoken("alpha beta", 0.7)),
            "03": ("C", 2.5, 2.1, spoken("gamma delta", 0.1)),
            "04": ("D", 1.5, 1.3, spoken("epsilon", 0.1)),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "01.webm"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 3.0, "02.mp4 (own audio)", audio=tmp_path / "broll.mp4"),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.52, "03.webm"),
        RenderedSection(p.sections[3], tmp_path / "04.mp4", 1.48, "04.webm"),
    ]
    return p, takes, rows


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


@pytest.fixture(name="take_index")
def take_index_fixture():
    """A take index whose words are on disk, which is what the narration clock is read from."""
    return take_index


@pytest.fixture(name="mid_clip_plan")
def mid_clip_plan_fixture():
    return mid_clip_plan


@pytest.fixture(name="titled_clip_rows")
def titled_clip_rows_fixture():
    return titled_clip_rows
