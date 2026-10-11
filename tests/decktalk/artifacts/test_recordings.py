"""What `record` did for one section, and what it judged about the result."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.recordings import LANDINGS, FilmSection, PaneLandings, RecordingLog, film_lanes, pane_landings
from decktalk.findings import Code, Finding, Location
from decktalk.media import ffmpeg
from decktalk.media.clipframes import landings_of
from decktalk.media.pagereport import Box, Landing, MotionLane, PageReport, Recording, Size
from decktalk.stages import played_clips
from support.pages import a_recording


def log(**fields: object) -> RecordingLog:
    base = {"section": 3, "made_from": {"scene": "abc"}, "recording": a_recording()}
    return RecordingLog.model_validate({**base, **fields})


def test_every_judgement_the_recorder_made_is_one_list_of_findings() -> None:
    """The page's own warnings and the checks over the frames are read by code, never as sentences."""
    judged = log(
        findings=(
            Finding(
                code=Code.RECORD_BLACK,
                message="the frame half way through is black, so nothing the slide declares was on screen.",
                location=Location(where="deck/index.html", section=3),
            ),
        )
    )
    assert [found.code for found in judged.findings] == [Code.RECORD_BLACK]


def test_the_log_round_trips_through_its_own_file(tmp_path: Path) -> None:
    written = log(recording=a_recording(assets=("deck/index.html",), external=("https://cdn.example",)))
    path = written.write(tmp_path / "03.json")
    assert RecordingLog.read(path) == written


# ---- the landings a pane clip carries ----------------------------------------------------------------

PANE = MotionLane(
    id="clip-1", kind="clip", box=Box(x=400, y=160, w=960, h=540), size=Size(w=960, h=540), departs=5.9,
    arrives=18.0, rest=Box(x=400, y=160, w=960, h=540), landings=(Landing(cue="1.1:play", kind="depart", at=5.9),),
    clip="deck/clips/tone.mp4",
)  # fmt: skip
"""A pane that plays its clip from 5.9 s, three halves the size of the 640 by 360 clip."""

OWN = MotionLane(
    id="colour", kind="motion", box=Box(x=0, y=120, w=640, h=240), size=Size(w=640, h=240), departs=1.0,
    arrives=2.0, landings=(Landing(cue="green", kind="depart", at=1.0),),
)  # fmt: skip
"""One lane of the clip's own, on its own clock and in its own pixels, as its maker wrote it."""


def played(tmp_path: Path, monkeypatch, *, pane: MotionLane = PANE, written: str | None = None) -> Recording:
    clip = tmp_path / "deck" / "clips" / "tone.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"film")
    landings_of(clip).write_text(written if written is not None else LANDINGS.dump_json((OWN,)).decode())
    monkeypatch.setattr(ffmpeg, "probe_size", lambda _path: (640, 360))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: 3.0)
    monkeypatch.setattr(ffmpeg, "has_audio", lambda _path: False)
    return a_recording(
        assets=("deck/index.html", "deck/clips/tone.mp4"), report=PageReport(motions=(pane,)), requested_seconds=20.0
    )


def placed_in(recording: Recording, root: Path, seconds: float = 20.0) -> PaneLandings:
    """The landings inside every pane clip `recording` played, in a section `seconds` long."""
    return pane_landings(played_clips(recording, seconds, root))


def test_a_clip_s_own_landing_moves_onto_the_section_s_clock_and_into_its_pane(tmp_path: Path, monkeypatch) -> None:
    placed = placed_in(played(tmp_path, monkeypatch), tmp_path)
    (lane,) = placed.lanes
    assert placed.unplaced == ()
    assert lane.id == "clip-1/colour" and lane.clip == "deck/clips/tone.mp4"
    assert lane.box == Box(x=400, y=340, w=960, h=360)
    assert (lane.departs, lane.arrives) == (6.9, 7.9)
    assert [(one.cue, one.at) for one in lane.landings] == [("green", 6.9)]


def test_a_pane_that_moved_while_its_clip_played_places_none_of_the_clip_s_landings(tmp_path, monkeypatch) -> None:
    """A pane that moved has no one box to draw the clip's lanes into, so the reason is said rather than guessed."""
    moved = PANE.model_copy(update={"box": Box(x=300, y=160, w=1060, h=540), "moved": 8.5})
    placed = placed_in(played(tmp_path, monkeypatch, pane=moved), tmp_path)
    assert placed.lanes == ()
    assert placed.unplaced == ("deck/clips/tone.mp4: its pane moved at 8.5 s, before its clip ended at 8.9 s",)


def test_a_pane_that_moved_once_its_clip_had_played_places_them_where_it_stood(tmp_path, monkeypatch) -> None:
    """The clip played from 5.9 s to 8.9 s, so a camera that leaves on its last frame takes nothing from it."""
    moved = PANE.model_copy(update={"box": Box(x=300, y=160, w=1060, h=540), "moved": 8.9})
    (lane,) = placed_in(played(tmp_path, monkeypatch, pane=moved), tmp_path).lanes
    assert lane.box == Box(x=400, y=340, w=960, h=360)


def test_a_pane_that_moves_once_its_section_has_cut_its_clip_short_places_them(tmp_path, monkeypatch) -> None:
    """A clip plays only as long as its section does, so the pane need hold still only until the section ends."""
    moved = PANE.model_copy(update={"box": Box(x=300, y=160, w=1060, h=540), "moved": 8.5})
    placed = placed_in(played(tmp_path, monkeypatch, pane=moved), tmp_path, seconds=8.0)
    assert placed.unplaced == () and len(placed.lanes) == 1


def test_a_pane_on_a_fraction_of_a_pixel_draws_the_clip_s_lanes_where_it_stood(tmp_path, monkeypatch) -> None:
    """A still pane is still at any position, and its clip's lanes are rounded once, into the pane's pixels."""
    fractional = PANE.model_copy(update={"rest": Box(x=400.4, y=160.0, w=959.8, h=540.0)})
    (lane,) = placed_in(played(tmp_path, monkeypatch, pane=fractional), tmp_path).lanes
    assert lane.box == Box(x=400, y=340, w=960, h=360)


def test_a_landings_file_that_is_not_lanes_is_said(tmp_path: Path, monkeypatch) -> None:
    placed = placed_in(played(tmp_path, monkeypatch, written='{"not": "lanes"}'), tmp_path)
    assert placed.lanes == ()
    assert placed.unplaced and "not in the shape of lanes" in placed.unplaced[0]


def test_a_clip_the_page_never_loaded_carries_nothing_into_the_film(tmp_path: Path, monkeypatch) -> None:
    """The lane is the page's word and the served file the origin's, so a page cannot name a file it never played."""
    recording = played(tmp_path, monkeypatch).model_copy(update={"assets": ("deck/index.html",)})
    assert recording.pane_clips() == ()
    assert placed_in(recording, tmp_path).lanes == ()


def section(recording: Recording, root: Path, *, skipped: frozenset[str] = frozenset()) -> FilmSection:
    plays = played_clips(recording, 20.0, root)
    return FilmSection(start=10.0, log=log(recording=recording), plays=plays, skipped=skipped)


def test_a_film_s_landings_are_every_section_s_lanes_on_the_film_s_clock(tmp_path: Path, monkeypatch) -> None:
    lanes = film_lanes([section(played(tmp_path, monkeypatch), tmp_path)])
    assert [(lane.id, lane.landings[0].at) for lane in lanes] == [("clip-1", 15.9), ("clip-1/colour", 16.9)]


def test_a_landing_on_a_cue_verify_skips_is_not_in_the_film_s_landings(tmp_path: Path, monkeypatch) -> None:
    """Verify measures nothing on a `"verify": false` cue, and the clip's own cues are never this project's."""
    recording = played(tmp_path, monkeypatch)
    lanes = film_lanes([section(recording, tmp_path, skipped=frozenset({"1.1:play", "green"}))])
    assert [(lane.id, lane.landings[0].cue) for lane in lanes] == [("clip-1/colour", "green")]


SQUARE = MotionLane(
    id="reveal-1", kind="reveal", box=Box(x=80, y=80, w=200, h=200), size=Size(w=200, h=200), departs=1.0,
    arrives=1.5, landings=(Landing(cue="1.1:square", kind="depart", at=1.0),),
)  # fmt: skip
"""A square the page revealed at a cue, as the recorder sampled it from the cue's second."""

UNSEEN = SQUARE.model_copy(
    update={"id": "reveal-2", "box": None, "landings": (Landing(cue="1.1:bar", kind="depart", at=2.0),)}
)
"""A reveal whose element the recorder never saw drawn, which has no box to measure on."""


def test_a_film_carries_every_reveal_its_pages_drew_but_not_one_on_a_cue_verify_skips(tmp_path: Path) -> None:
    recording = a_recording(report=PageReport(reveals=(SQUARE, UNSEEN)))
    (lane,) = film_lanes([section(recording, tmp_path)])
    assert (lane.kind, lane.box, lane.departs, lane.landings[0].at) == ("reveal", SQUARE.box, 11.0, 11.0)
    assert film_lanes([section(recording, tmp_path, skipped=frozenset({"1.1:square"}))]) == ()
