"""Every section cut to its span: the recording, the clip, the slate and the black stand-in."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import InputError, NotBuiltError
from decktalk.inputs import Inputs
from decktalk.media import browser, ffmpeg
from decktalk.media.encode import Encoder
from decktalk.results import SectionKind, Substitute
from decktalk.settings import BY_ID
from decktalk.stages.assemble.cut import (
    _judge_missing,
    concat,
    cut_list,
    page_target,
    remove_stray_cuts,
    render_clip,
    render_sections,
    rendered_starts,
    section_slate,
    section_targets,
    vfades,
)

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


def test_an_optional_clip_plays_its_slate_and_earns_no_judgement(tmp_path, write_project, open_run, monkeypatch):
    """A section that declares `optional` says the slate is what it wants when the clip is not there.

    A certain `FILE_MISSING` stopped the build on that very slate, so a project could declare the
    slot and never build, which made `optional` mean nothing to anybody running a command.
    """
    toml = (
        "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    monkeypatch.setattr("decktalk.stages.assemble.cut.section_slate", lambda *_args: None)
    enc = Encoder(inputs.settings.video)
    (slot,) = inputs.document.clip_sections
    row = render_clip(inputs, opened.run, enc, slot, tmp_path / "out.mp4", 0.0, strict=False)
    _judge_missing(opened.run, [row])
    assert (row.substitute, row.missing) == (Substitute.SLATE, "media/slot.mp4")
    assert opened.codes() == []


def test_strict_refuses_a_missing_clip_unless_the_section_is_optional(tmp_path, write_project, open_run, monkeypatch):
    """A section that declares `optional` says its slate is what `--strict` is told to allow."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\nclip = 'media/real.mp4'\n"
        "[[section]]\nnumber = 2\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    monkeypatch.setattr("decktalk.stages.assemble.cut.section_slate", lambda *_args: None)
    enc = Encoder(inputs.settings.video)
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


def _recorded(inputs, numbers=(1, 2, 3)) -> None:  # noqa: ANN001
    """A webm on disk for each section, so every cut is encoded from a recording rather than from black."""
    inputs.workspace.recordings_dir.mkdir(parents=True, exist_ok=True)
    for number in numbers:
        inputs.workspace.recording(f"{number:02d}").write_bytes(f"webm {number}".encode())


def test_an_unchanged_rebuild_encodes_no_section_again(
    tmp_path, write_project, open_run, take_index, spoken, fake_ffmpeg
):
    """A cut whose arguments and whose recording have not moved is read back rather than encoded."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    _recorded(inputs)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert len(fake_ffmpeg.wrote(".mp4")) == 3
    fake_ffmpeg.calls.clear()
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert fake_ffmpeg.wrote(".mp4") == []


def test_a_changed_recording_encodes_its_own_section_and_no_other(
    tmp_path, write_project, open_run, take_index, spoken, fake_ffmpeg
):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    _recorded(inputs)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    fake_ffmpeg.calls.clear()
    inputs.workspace.recording("02").write_bytes(b"recorded again")
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert fake_ffmpeg.wrote(".mp4") == [inputs.workspace.section_video("02")]


def test_a_changed_fade_encodes_the_sections_it_touches_and_keeps_the_rest(
    tmp_path, write_project, open_run, take_index, spoken, fake_ffmpeg
):
    """The key is the whole argument list, so a setting nobody thought to name still moves it."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    _recorded(inputs)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    fake_ffmpeg.calls.clear()
    toml = (tmp_path / "decktalk.toml").read_text(encoding="utf-8") + "\n[transition]\ndips = [[2, 3]]\n"
    changed = write_project(tmp_path, toml)
    render_sections(changed, open_run(tmp_path).run, takes, only=None, strict=False)
    # Only section 1 loses the fade out it had at its cut, because a page section's own entrance
    # already stands in for a fade in, so sections 2 and 3 encode the same arguments as before.
    assert fake_ffmpeg.wrote(".mp4") == [changed.workspace.section_video("01")]


def test_a_cut_left_by_a_stopped_run_is_encoded_again(
    tmp_path, write_project, open_run, take_index, spoken, fake_ffmpeg
):
    """A cut with no key beside it may be half written, so it is never kept."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    _recorded(inputs)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    fake_ffmpeg.calls.clear()
    inputs.workspace.section_video("01").with_suffix(".json").unlink()
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert fake_ffmpeg.wrote(".mp4") == [inputs.workspace.section_video("01")]


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


def test_a_leftover_cut_and_key_of_a_section_nobody_declares_are_removed(tmp_path, write_project):
    inputs = write_project(tmp_path)
    sections = inputs.workspace.sections_dir
    sections.mkdir(parents=True)
    for name in ("01.mp4", "01.json", "09.mp4", "09.json"):
        (sections / name).write_bytes(b"")
    remove_stray_cuts(inputs)
    assert sorted(path.name for path in sections.iterdir()) == ["01.json", "01.mp4"]


def test_a_clip_the_project_names_opens_as_one_file_and_follows_no_name_inside_it(
    tmp_path, write_project, open_run, fake_ffmpeg
):
    """A clip that is a playlist would otherwise read files and hosts the project never named."""
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/clip.mp4'\n"
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "clip.mp4").write_bytes(b"")
    (slot,) = inputs.document.clip_sections
    render_clip(
        inputs, open_run(tmp_path).run, Encoder(inputs.settings.video), slot, tmp_path / "out.mp4", 0.0, strict=True
    )
    opened = ffmpeg.source(inputs.path("media/clip.mp4"))
    assert any(call[: len(opened)] == opened for call in fake_ffmpeg.calls)


def test_an_untrusted_project_draws_its_slate_untrusted(tmp_path, write_project, open_run, monkeypatch):
    """A slate carries the project's own chapter title, so it launches under the project's policy."""
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/slot.mp4'\noptional = true\n"
    write_project(tmp_path, toml)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    asked: list[object] = []
    monkeypatch.setattr(browser, "render_slate", lambda out, **named: asked.append(named["policy"]) or out)
    (slot,) = inputs.document.clip_sections
    section_slate(inputs, open_run(tmp_path).run, slot)
    assert asked == ["untrusted"]
