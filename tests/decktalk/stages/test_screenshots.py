"""The screenshots stage: what each written PNG is called, and what its row says it shows."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import ConfigError
from decktalk.model import Project
from decktalk.stages.screenshots import Screenshot, ScreenshotsResult, screenshot_slides
from decktalk.verdicts import Findings

PAGES_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""


@pytest.fixture
def project(tmp_path) -> Project:
    (tmp_path / "decktalk.toml").write_text(PAGES_TOML, encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return Project.load(tmp_path, environ={})


def test_a_cue_screenshot_needs_exactly_one_slide(project):
    """`--after` freezes one slide at one moment, so several slides would name several pictures."""
    with pytest.raises(ConfigError, match="exactly one slide"):
        screenshot_slides(project, slides=["1.1", "2.1"], cues=["1.1a"])
    with pytest.raises(ConfigError, match="exactly one slide"):
        screenshot_slides(project, cues=["1.1a"])


def test_a_project_of_clips_alone_says_so_rather_than_opening_a_browser(tmp_path):
    """There is no page to index, so the command says that instead of launching Chromium for nothing."""
    toml = '[project]\nname = "t"\n\n[[section]]\nnumber = 1\nclip = "media/a.mp4"\n'
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError, match="no HTML pages"):
        screenshot_slides(Project.load(tmp_path, environ={}))


def test_every_row_names_the_picture_it_is(tmp_path):
    """`--json` is read by an agent that never sees the file, so each row says what the PNG shows."""
    root = tmp_path
    slide = Screenshot(
        path=root / "build" / "screenshots" / "slide-3.1-after-3.1eq.png",
        page="deck/index.html",
        slide="3.1",
        cue="3.1eq",
    )
    frame = Screenshot(
        path=root / "build" / "screenshots" / "section-01-at-1s.png",
        page="deck/index.html",
        section=1,
        at=1.0,
        page_errors=("TypeError: nope",),
    )
    result = ScreenshotsResult(files=[slide, frame])
    assert result.to_dict(root)["files"] == [
        {
            "file": "build/screenshots/slide-3.1-after-3.1eq.png",
            "page": "deck/index.html",
            "slide": "3.1",
            "cue": "3.1eq",
            "section": None,
            "at": None,
            "page_errors": [],
        },
        {
            "file": "build/screenshots/section-01-at-1s.png",
            "page": "deck/index.html",
            "slide": None,
            "cue": None,
            "section": 1,
            "at": 1.0,
            "page_errors": ["TypeError: nope"],
        },
    ]
    assert result.paths == [slide.path, frame.path]


def test_screenshots_judges_nothing():
    """The command writes pictures for a person to look at, so it can never fail a build."""
    errored = Screenshot(path=Path("a.png"), page_errors=("TypeError: nope",))
    assert ScreenshotsResult(files=[errored]).findings == Findings()


LONG_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body style="background:#fff">
<script src="decktalk-runtime.js"></script>
<div data-scene="1"><template data-slide="1.1"><p data-cue="1.1a">a</p></template></div>
</body></html>"""


@pytest.mark.browser
@pytest.mark.media
@pytest.mark.timeout(120)
def test_a_frame_near_the_end_of_a_section_longer_than_30_seconds_is_written(tmp_path):
    """The wait for a frame lasts as long as the frame's own time, which Playwright's default 30 s once cut short."""
    import shutil

    from decktalk.stages.narrate import narrate
    from decktalk.stages.screenshots import screenshot_frames
    from decktalk.toolchain.assets import RUNTIME_FILE, runtime_path

    toml = '[project]\nname = "long"\n[video]\nwidth = 320\nheight = 180\n'
    toml += '[[section]]\nnumber = 1\npage = "deck/index.html"\n'
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    words = " ".join(f"word{i}" for i in range(80))  # 80 words at 150 a minute is 32 s
    (tmp_path / "script.md").write_text(f"## 1. Long\n\n{words}.\n", encoding="utf-8")
    (tmp_path / "cues.json").write_text("{}", encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text(LONG_PAGE, encoding="utf-8")
    shutil.copyfile(runtime_path(), tmp_path / "deck" / RUNTIME_FILE)
    project = Project.load(tmp_path, environ={})
    takes = narrate(project, silent=True).takes
    span = takes.span("01")
    assert span is not None and span > 31
    at = round(span - 0.5, 1)
    [frame] = screenshot_frames(project, 1, [at])
    assert frame.at == at and frame.path.exists() and frame.path.stat().st_size > 0
