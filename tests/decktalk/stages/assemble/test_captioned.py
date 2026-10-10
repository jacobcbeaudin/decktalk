"""The captioned copy: which page draws each caption, the frames it covers, and the list that lays it over the film."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.captions import CaptionCue
from decktalk.errors import InputError
from decktalk.stages.assemble.captioned import MICROSECONDS, Shown, drawing_page, layer_listing, page_caption, shown
from decktalk.stages.assemble.cut import placements_of

from .conftest import MID_CLIP_TOML, rendered, write_project

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")

TWO_PAGES_TOML = MID_CLIP_TOML.replace('page = "deck/index.html"\nscene = "3"', 'page = "deck/other.html"\nscene = "3"')
"""A page section, a clip, then a section on a second page and one back on the first."""

FPS = 25


def test_a_caption_is_drawn_by_the_page_playing_where_it_shows_and_a_clip_borrows_the_first(tmp_path):
    inputs = write_project(tmp_path, TWO_PAGES_TOML)
    placements = placements_of(inputs, rendered(inputs, {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}))
    pages = [drawing_page(inputs, placements, at) for at in (0.5, 2.5, 4.5, 6.5)]
    assert pages == ["deck/index.html", "deck/index.html", "deck/other.html", "deck/index.html"]


def test_a_caption_covers_the_frames_a_player_shows_it_on_and_one_that_covers_none_is_left_out(tmp_path):
    """A player shows a caption on every frame from the first at or after its start to the first at or after its end."""
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    placements = placements_of(inputs, rendered(inputs, {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}))
    cues = [
        CaptionCue(start=0.5, end=1.21, lines=("Hello.",), voice="narrator", turn=1),
        CaptionCue(start=1.21, end=1.235, lines=("[tick]",)),
        CaptionCue(start=1.3, end=1.9, lines=("[door opens]", "Who?"), voice="guest", turn=2, spoken=(1, 2)),
    ]
    found = shown(inputs, placements, cues, FPS, 8 * FPS)
    assert [(one.first, one.after) for one in found] == [(13, 31), (33, 48)]
    assert found[1].caption == {"lines": ["[door opens]", "Who?"], "voice": "guest", "turn": 2, "spoken": [1, 2]}


def test_a_page_is_handed_who_says_a_caption_and_never_a_name_in_its_lines():
    cue = CaptionCue(start=0.0, end=1.0, lines=("Says who?",), voice="guest", turn=3, named=True)
    assert page_caption(cue) == {"lines": ["Says who?"], "voice": "guest", "turn": 3, "spoken": [0, 1]}


def test_a_film_with_captions_and_no_page_to_draw_them_is_refused(tmp_path):
    inputs = write_project(tmp_path, '[project]\nname = "t"\n\n[[section]]\nnumber = 1\nclip = "media/broll.mp4"\n')
    placements = placements_of(inputs, rendered(inputs, {1: 2.0}))
    with pytest.raises(InputError, match="captioned"):
        shown(inputs, placements, [CaptionCue(start=0.0, end=1.0, lines=("Hi.",))], FPS, 2 * FPS)


def test_the_list_plays_each_still_on_its_frames_and_the_clear_frame_everywhere_else():
    """Every duration is the gap between two frame boundaries, so the boundaries add up exactly."""
    captions = [
        Shown(page="p", caption={}, first=3, after=10),
        Shown(page="p", caption={}, first=10, after=11),
    ]
    listing = layer_listing([Path("/a.png"), Path("/b.png")], captions, Path("/clear.png"), 30, 30)
    entries = listing.split("file ")[1:]
    names = [entry.split("\n")[0] for entry in entries]
    assert names == ["'/clear.png'", "'/a.png'", "'/b.png'", "'/clear.png'", "'/clear.png'"]
    durations = [int(entry.split("duration ")[1].split("us")[0]) for entry in entries if "duration" in entry]
    assert durations == [100_000, 233_333, 33_334, 633_333]
    assert sum(durations) == MICROSECONDS
