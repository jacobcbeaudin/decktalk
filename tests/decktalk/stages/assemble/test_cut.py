"""Every section cut to its span: the recording, the clip, the slate and the black stand-in."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import InputError, NotBuiltError
from decktalk.media import ffmpeg
from decktalk.results import SectionKind, Substitute
from decktalk.stages.assemble.cut import (
    concat,
    cut_list,
    page_target,
    render_clip,
    render_sections,
    rendered_starts,
    section_targets,
    stray_cuts,
    vfades,
)
from decktalk.stages.assemble.cut import encoder as make_encoder

from .conftest import MID_CLIP_TOML, TITLED_TOML

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")


def test_a_path_with_an_apostrophe_in_it_survives_the_concat_list(tmp_path, monkeypatch):
    """A build under `jacob's films/` died at the join, so the list is written by the one escaper."""
    written: list[str] = []

    def run(*args: str) -> None:
        listing = Path(args[args.index("-i") + 1])
        written.append(listing.read_text(encoding="utf-8"))
        Path(args[-1]).write_bytes(b"")

    monkeypatch.setattr(ffmpeg, "run", run)
    awkward = tmp_path / "jacob's films"
    awkward.mkdir()
    files = [awkward / "01.mp4", awkward / "02.mp4"]
    concat(files, tmp_path / "picture.mp4")
    # The list is what the one escaper writes, so the join has no second spelling of a quoted path.
    assert written == [ffmpeg.concat_list(files)]
    assert "jacob'\\''s films" in written[0]


def test_section_targets_are_frame_exact(tmp_path, write_project, take_index, spoken):
    """A section is cut to a whole number of frames, so the film never drifts off the narration."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.02, 1.0, spoken("one")), 2: ("b", 1.48, 1.4, spoken("two"))})
    targets = section_targets(takes, 30)
    assert abs(targets[1] - 1.0333) < 1e-3
    assert abs(targets[2] - 1.4667) < 1e-3
    # The boundaries are cumulative, so the two lengths add up to the whole of the narration.
    assert abs(targets[1] + targets[2] - takes.total_seconds) < 1 / 30


def test_a_page_section_with_no_recording_plays_black_and_is_a_certain_finding(
    tmp_path, write_project, open_run, take_index, spoken
):
    """A film that quietly played black where a recording should be would publish a lie about itself."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    rows = render_sections(inputs, opened.run, takes, only=None, strict=False)
    assert [row.substitute for row in rows] == [Substitute.BLACK] * 3
    assert opened.codes() == ["FILE_MISSING"] * 3
    said = next(line.finding.message for line in opened.lines if getattr(line, "finding", None) is not None)
    assert "build/recordings/01.webm" in said
    assert "a black frame plays" in said


def test_strict_refuses_a_missing_recording_and_names_the_stage_that_writes_one(
    tmp_path, write_project, open_run, take_index, spoken
):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.0, 0.8, spoken("word"))})
    with pytest.raises(NotBuiltError) as refused:
        render_sections(inputs, opened.run, takes, only=None, strict=True)
    assert "decktalk record" in (refused.value.hint or "")


def test_strict_refuses_a_missing_clip_unless_the_section_is_optional(tmp_path, write_project, open_run, monkeypatch):
    """An optional slot is the scaffold's own B-roll, so its slate is what `--strict` is told to allow."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\nclip = 'media/real.mp4'\n"
        "[[section]]\nnumber = 2\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    monkeypatch.setattr("decktalk.stages.assemble.cut.section_slate", lambda *_args: None)
    enc = make_encoder(inputs)
    real, slot = inputs.document.clip_sections
    out = tmp_path / "out.mp4"

    with pytest.raises(InputError) as refused:
        render_clip(inputs, opened.run, enc, real, out, 0.0, strict=True)
    assert refused.value.location is not None
    assert refused.value.location.where == "media/real.mp4"

    allowed = render_clip(inputs, opened.run, enc, slot, out, 0.0, strict=True)
    assert (allowed.substitute, allowed.missing) == (Substitute.SLATE, "media/slot.mp4")


def test_a_section_the_run_did_not_name_keeps_the_cut_already_on_disk(
    tmp_path, write_project, open_run, take_index, spoken
):
    """Re-encoding a picture that has not moved buys nothing and costs the longest pass in the stage."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    inputs.workspace.sections_dir.mkdir(parents=True)
    inputs.workspace.section_video("02").write_bytes(b"already here")
    rows = render_sections(inputs, opened.run, takes, only=[1, 3], strict=False)
    kept = next(row for row in rows if row.number == 2)
    assert kept.note.endswith("(kept)")
    assert kept.substitute is None
    # Only the named sections were judged, because the kept one was never looked at for a recording.
    assert opened.codes() == ["FILE_MISSING", "FILE_MISSING"]


def test_the_cut_list_records_where_each_section_plays_and_what_stood_in(tmp_path, write_project, rendered):
    inputs = write_project(tmp_path, TITLED_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    cuts = cut_list(inputs, rows)
    assert [cut.section for cut in cuts.sections] == [1, 2, 3, 4]
    assert [cut.start for cut in cuts.sections] == [0.0, 2.0, 5.0, 7.5]
    assert cuts.total_seconds == 9.0
    assert cuts.of(2).kind is SectionKind.CLIP
    assert cuts.of(1).kind is SectionKind.PAGE
    assert cuts.of(3).chapter == "The edit"
    assert cuts.fps == inputs.settings.video.output_fps


def test_rendered_starts_add_up_in_the_order_the_film_plays(tmp_path, write_project, rendered):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    assert rendered_starts(rows) == {1: 0.0, 2: 2.0, 3: 5.0, 4: 7.5}


def test_a_page_with_no_span_names_the_stage_that_gives_it_one(tmp_path, write_project, take_index, spoken):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.0, 0.8, spoken("word"))})
    with pytest.raises(NotBuiltError) as refused:
        page_target(takes, inputs.document.page_sections[1], 25)
    assert "decktalk narrate" in (refused.value.hint or "")


def test_a_hold_is_picture_alone_and_the_narration_pauses_for_it(tmp_path, write_project, take_index, spoken):
    toml = (
        "[project]\nname = 't'\n[narration]\nlead_seconds = 0\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\nhold_seconds = 1.5\n"
    )
    inputs = write_project(tmp_path, toml)
    takes = take_index(inputs, {1: ("a", 2.0, 1.8, spoken("one two"))})
    assert page_target(takes, inputs.document.page_sections[0], 25) == 3.5


def test_the_fades_a_section_carries_are_the_dips_at_its_own_cuts():
    assert vfades(10.0, False, False, 0.16) == ""
    assert vfades(10.0, True, False, 0.16) == ",fade=t=in:st=0:d=0.16"
    assert vfades(10.0, False, True, 0.16) == ",fade=t=out:st=9.840:d=0.16"


def test_a_leftover_cut_of_a_section_nobody_declares_is_said_and_left_out(tmp_path, write_project, open_run):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    inputs.workspace.sections_dir.mkdir(parents=True)
    inputs.workspace.section_video("09").write_bytes(b"")
    stray_cuts(inputs, opened.run)
    assert opened.notes() == [
        "build/sections/09.mp4 is a cut of a section decktalk.toml no longer declares, so it is left out of the film."
    ]
