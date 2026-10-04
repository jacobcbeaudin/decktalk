"""Every section cut to its span: the recording, the clip, the slate and the black stand-in."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Takes
from decktalk.errors import InputError, NotBuiltError, ToolError
from decktalk.events import FindingRaised
from decktalk.inputs import Inputs
from decktalk.media import ffmpeg
from decktalk.media.encode import Encoder
from decktalk.results import SectionKind, Substitute
from decktalk.settings import BY_ID
from decktalk.stages.assemble import slate
from decktalk.stages.assemble.cut import (
    _judge_missing,
    concat,
    page_target,
    placements_of,
    remove_stray_videos,
    render_clip,
    render_sections,
    rendered_starts,
    section_slate,
    section_targets,
    vfades,
)
from support.fakes import FakeFfmpeg
from support.logs import decisions

from .conftest import MID_CLIP_TOML, TITLED_TOML, draw_slate, open_run, rendered, spoken, take_index, write_project

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")


def test_a_path_with_an_apostrophe_in_it_survives_the_concat_list(tmp_path, monkeypatch):
    """A build under `jacob's films/` has an apostrophe to escape, so the list is written by the one escaper."""
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


def test_section_targets_are_frame_exact(tmp_path):
    """A section is cut to a whole number of frames, so the film never drifts off the narration."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.02, 1.0, spoken("one")), 2: ("b", 1.48, 1.4, spoken("two"))})
    targets = section_targets(takes, 30)
    assert abs(targets[1] - 1.0333) < 1e-3
    assert abs(targets[2] - 1.4667) < 1e-3
    # The boundaries are cumulative, so the two lengths add up to the whole of the narration.
    assert abs(targets[1] + targets[2] - takes.total_seconds) < 1 / 30


def test_a_page_section_with_no_recording_plays_black_and_is_an_error(tmp_path):
    """A film that quietly played black where a recording should be would publish a lie about itself."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    rows = render_sections(inputs, opened.run, takes, only=None, strict=False)
    assert [row.substitute for row in rows] == [Substitute.BLACK] * 3
    assert opened.codes() == ["FILE_MISSING"] * 3
    said = next(line.finding.message for line in opened.of(FindingRaised))
    assert "build/recordings/01.webm" in said
    assert "a black frame plays" in said


def test_strict_refuses_a_missing_recording_and_names_the_stage_that_writes_one(tmp_path):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.0, 0.8, spoken("word"))})
    with pytest.raises(NotBuiltError) as refused:
        render_sections(inputs, opened.run, takes, only=None, strict=True)
    assert "decktalk record" in (refused.value.hint or "")


def test_an_optional_clip_plays_its_slate_and_earns_no_judgement(tmp_path, monkeypatch):
    """A section that declares `optional` says the slate is what it wants when the clip is not there.

    A `FILE_MISSING` error stopped the build on that very slate, so a project could declare the
    slot and never build, which made `optional` mean nothing to anybody running a command.
    """
    toml = (
        "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    monkeypatch.setattr("decktalk.stages.assemble.cut.section_slate", lambda *_args: None)
    enc = Encoder(inputs.settings.video, inputs.settings.audio)
    (slot,) = inputs.document.clip_sections
    row = render_clip(inputs, opened.run, enc, slot, tmp_path / "out.mp4", 0.0, strict=False)
    _judge_missing(opened.run, [row])
    assert (row.substitute, row.source) == (Substitute.SLATE, Path("media/slot.mp4"))
    assert opened.codes() == []


def test_strict_refuses_a_missing_clip_unless_the_section_is_optional(tmp_path, monkeypatch):
    """A section that declares `optional` says its slate is what `--strict` is told to allow."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\nclip = 'media/real.mp4'\n"
        "[[section]]\nnumber = 2\nclip = 'media/slot.mp4'\nslate_seconds = 4\noptional = true\n"
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    monkeypatch.setattr("decktalk.stages.assemble.cut.section_slate", lambda *_args: None)
    enc = Encoder(inputs.settings.video, inputs.settings.audio)
    real, slot = inputs.document.clip_sections
    out = tmp_path / "out.mp4"

    with pytest.raises(InputError) as refused:
        render_clip(inputs, opened.run, enc, real, out, 0.0, strict=True)
    assert refused.value.location is not None
    assert refused.value.location.where == "media/real.mp4"

    allowed = render_clip(inputs, opened.run, enc, slot, out, 0.0, strict=True)
    assert (allowed.substitute, allowed.source) == (Substitute.SLATE, Path("media/slot.mp4"))


def test_a_cut_the_run_did_not_name_is_kept_only_under_its_own_key(tmp_path, fake_ffmpeg):
    """A cut on disk was once kept with no key at all, so a supplied `build/` chose what the film played.

    A section a `--section` run does not name is still cut through its key, so an unchanged cut is
    read back and one the key does not vouch for is encoded again. Only the named sections are judged.
    """
    inputs, takes = cut_once(tmp_path, fake_ffmpeg)
    render_sections(inputs, open_run(tmp_path).run, takes, only=[1, 3], strict=False)
    assert fake_ffmpeg.wrote(".mp4") == []

    planted = inputs.workspace.section_video("02")
    for key in ("absent", "mismatched"):
        planted.write_bytes(b"a film this build never made")
        if key == "absent":
            planted.with_suffix(".json").unlink()
        else:
            planted.with_suffix(".json").write_text('{"digest": "0"}', encoding="utf-8")
        fake_ffmpeg.calls.clear()
        opened = open_run(tmp_path)
        render_sections(inputs, opened.run, takes, only=[1, 3], strict=False)
        assert fake_ffmpeg.wrote(".mp4") == [planted]
        assert planted.read_bytes() == b""


def test_the_join_opens_every_cut_through_the_file_protocol_and_the_closed_demuxers(tmp_path, monkeypatch):
    """The concat demuxer copies its whitelists to each cut it opens, so a cut cannot be a playlist or a manifest."""
    seen: list[list[str]] = []
    monkeypatch.setattr(ffmpeg, "run", lambda *args: seen.append(list(args)))
    concat([tmp_path / "01.mp4"], tmp_path / "picture.mp4")
    (args,) = seen
    ahead = args[: args.index("-i")]
    assert ahead[ahead.index("-protocol_whitelist") + 1] == ffmpeg.SOURCE_PROTOCOLS
    assert set(ahead[ahead.index("-format_whitelist") + 1].split(",")) == {"concat", "mov"}


def recorded(tmp_path: Path) -> tuple[Inputs, Takes]:
    """Three narrated sections with a webm on disk for each, so every cut is encoded from a recording."""
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {n: (f"c{n}", 1.0, 0.8, spoken("word")) for n in (1, 2, 3)})
    inputs.workspace.recordings_dir.mkdir(parents=True, exist_ok=True)
    for number in (1, 2, 3):
        inputs.workspace.recording(f"{number:02d}").write_bytes(f"webm {number}".encode())
    return inputs, takes


def cut_once(tmp_path: Path, fake_ffmpeg: FakeFfmpeg) -> tuple[Inputs, Takes]:
    """Those three sections cut by one run, which encoded each of them, and the calls it made forgotten."""
    inputs, takes = recorded(tmp_path)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert len(fake_ffmpeg.wrote(".mp4")) == 3
    fake_ffmpeg.calls.clear()
    return inputs, takes


def test_an_unchanged_rebuild_encodes_no_section_again(tmp_path, fake_ffmpeg):
    """A cut whose arguments and whose recording have not moved is read back rather than encoded."""
    inputs, takes = cut_once(tmp_path, fake_ffmpeg)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert fake_ffmpeg.wrote(".mp4") == []


@pytest.mark.parametrize(
    ("change", "key"),
    [
        pytest.param(lambda inputs: inputs.workspace.recording("02").write_bytes(b"again"), "02", id="a new recording"),
        # A cut with no key beside it may be half written, so it is never kept.
        pytest.param(
            lambda inputs: inputs.workspace.section_video("01").with_suffix(".json").unlink(), "01", id="a stopped run"
        ),
    ],
)
def test_a_changed_section_is_encoded_again_and_no_other(tmp_path, fake_ffmpeg, change, key):
    inputs, takes = cut_once(tmp_path, fake_ffmpeg)
    change(inputs)
    render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
    assert fake_ffmpeg.wrote(".mp4") == [inputs.workspace.section_video(key)]


def test_a_changed_fade_encodes_the_sections_it_touches_and_keeps_the_rest(tmp_path, fake_ffmpeg):
    """The key is the whole argument list, so a setting nobody thought to name still moves it."""
    inputs, takes = cut_once(tmp_path, fake_ffmpeg)
    toml = (tmp_path / "decktalk.toml").read_text(encoding="utf-8") + "\n[transition]\ndips = [[2, 3]]\n"
    changed = write_project(tmp_path, toml)
    render_sections(changed, open_run(tmp_path).run, takes, only=None, strict=False)
    # Only section 1 loses the fade out it had at its cut, because a page section's own entrance
    # already stands in for a fade in, so sections 2 and 3 encode the same arguments as before.
    assert fake_ffmpeg.wrote(".mp4") == [changed.workspace.section_video("01")]


def test_every_cut_kept_or_encoded_says_why(tmp_path, fake_ffmpeg, caplog):
    del fake_ffmpeg
    inputs, takes = recorded(tmp_path)

    with caplog.at_level("DEBUG", logger="decktalk"):
        render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
        assert set(decisions(caplog, "cut")) == {(False, "no-cut")}
        inputs.workspace.section_video("01").with_suffix(".json").unlink()
        inputs.workspace.recording("02").write_bytes(b"recorded again")
        render_sections(inputs, open_run(tmp_path).run, takes, only=None, strict=False)
        said = set(decisions(caplog, "cut", "file", "hit", "why"))
        assert said == {("01.mp4", False, "no-key"), ("02.mp4", False, "key-changed"), ("03.mp4", True, "unchanged")}


def test_the_placements_record_where_each_section_plays_and_what_stood_in(tmp_path):
    inputs = write_project(tmp_path, TITLED_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    placements = placements_of(inputs, rows)
    assert [row.section for row in placements.sections] == [1, 2, 3, 4]
    assert [row.start for row in placements.sections] == [0.0, 2.0, 5.0, 7.5]
    assert placements.total_seconds == 9.0
    assert [row.kind for row in placements.sections[:2]] == [SectionKind.PAGE, SectionKind.CLIP]
    assert placements.sections[2].chapter == "The edit"
    assert placements.fps == inputs.settings.video.fps


def test_rendered_starts_add_up_in_the_order_the_film_plays(tmp_path):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    assert rendered_starts(rows) == {1: 0.0, 2: 2.0, 3: 5.0, 4: 7.5}


def test_a_page_with_no_span_names_the_stage_that_gives_it_one(tmp_path):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("a", 1.0, 0.8, spoken("word"))})
    with pytest.raises(NotBuiltError) as refused:
        page_target(takes, inputs.document.page_sections[1], 25)
    assert "decktalk narrate" in (refused.value.hint or "")


def test_a_hold_is_picture_alone_and_the_narration_pauses_for_it(tmp_path):
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


def test_a_leftover_cut_and_key_of_a_section_nobody_declares_are_removed(tmp_path):
    inputs = write_project(tmp_path)
    sections = inputs.workspace.sections_dir
    sections.mkdir(parents=True)
    for name in ("01.mp4", "01.json", "09.mp4", "09.json"):
        (sections / name).write_bytes(b"")
    remove_stray_videos(inputs)
    assert sorted(path.name for path in sections.iterdir()) == ["01.json", "01.mp4"]


def test_a_clip_the_project_names_opens_as_one_file_and_follows_no_name_inside_it(tmp_path, fake_ffmpeg):
    """A clip that is a playlist would otherwise read files and hosts the project never named."""
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/clip.mp4'\n"
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "clip.mp4").write_bytes(b"")
    (slot,) = inputs.document.clip_sections
    render_clip(
        inputs,
        open_run(tmp_path).run,
        Encoder(inputs.settings.video, inputs.settings.audio),
        slot,
        tmp_path / "out.mp4",
        0.0,
        strict=True,
    )
    opened = ffmpeg.source(inputs.path("media/clip.mp4"))
    assert any(call[: len(opened)] == opened for call in fake_ffmpeg.calls)


def test_an_untrusted_project_draws_its_slate_untrusted(tmp_path, monkeypatch):
    """A slate carries the project's own chapter title, so it launches under the project's policy."""
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/slot.mp4'\noptional = true\n"
    write_project(tmp_path, toml)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    asked: list[object] = []

    def draw(out: Path, **named: object) -> Path:
        asked.append(named["policy"])
        return draw_slate(out)

    monkeypatch.setattr(slate, "render_slate", draw)
    (slot,) = inputs.document.clip_sections
    section_slate(inputs, open_run(tmp_path).run, slot)
    assert asked == ["untrusted"]


def test_a_slate_is_drawn_again_when_what_it_shows_changes(tmp_path, monkeypatch):
    """A renamed chapter or a new colour once shipped the old slate, because the first one drawn was kept forever.

    The slate is kept under everything it shows, so an edit gives it a new name, the cut that reads it
    gets a new key, and the same inputs read the kept slate back without opening a browser.
    """
    drawn: list[str] = []

    def draw(out: Path, **named: object) -> Path:
        picture = f"{named['title']}|{named['background']}"
        drawn.append(picture)
        draw_slate(out).write_text(picture, encoding="utf-8")
        return out

    monkeypatch.setattr(slate, "render_slate", draw)
    base = "[project]\nname = 't'\n{video}[[section]]\nnumber = 1\nclip = 'media/slot.mp4'\nchapter = '{chapter}'\n"
    out = tmp_path / "build" / "sections" / "01.mp4"
    out.parent.mkdir(parents=True)

    def cut(chapter: str, video: str = "") -> tuple[Path | None, str]:
        inputs = write_project(tmp_path, base.format(chapter=chapter, video=video))
        (slot,) = inputs.document.clip_sections
        run = open_run(tmp_path).run
        render_clip(inputs, run, Encoder(inputs.settings.video, inputs.settings.audio), slot, out, 0.0, strict=False)
        return section_slate(inputs, run, slot), out.with_suffix(".json").read_text(encoding="utf-8")

    first, first_key = cut("Demo one")
    renamed, renamed_key = cut("Live demo")
    recoloured, recoloured_key = cut("Live demo", "[video]\nslate_color = '#ff0000'\n")
    again, again_key = cut("Live demo", "[video]\nslate_color = '#ff0000'\n")
    assert drawn == ["Demo one|0x0e1116", "Live demo|0x0e1116", "Live demo|#ff0000"]
    assert len({first, renamed, recoloured}) == 3
    assert len({first_key, renamed_key, recoloured_key}) == 3
    assert (again, again_key) == (recoloured, recoloured_key)
    assert renamed is not None and renamed.read_text(encoding="utf-8") == "Live demo|0x0e1116"


@pytest.fixture
def real_ffmpeg(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned ffmpeg this machine fetched, in place of the fake the rest of this module runs.

    The module fakes ffmpeg through this same monkeypatch, so undoing it once the fake is in place
    restores the real tool. Its tests are marked `media`, so the machine's toolchain is already bound
    as a run binds it, because what these tests measure is what the real demuxer opens.
    """
    request.getfixturevalue("fake_ffmpeg")
    monkeypatch.undo()


def _film(path: Path) -> Path:
    """A real one-second section cut, encoded by the pinned ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(
        "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


MANIFEST = (
    '<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
    'mediaPresentationDuration="PT1S" minBufferTime="PT1S" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">'
    '<Period><AdaptationSet mimeType="video/mp4"><Representation id="1" bandwidth="1000">'
    "<BaseURL>{target}</BaseURL></Representation></AdaptationSet></Period></MPD>\n"
)
"""A DASH manifest, which the concat demuxer would probe inside a cut and follow to the file it names."""


@pytest.mark.media
@pytest.mark.parametrize(
    "planted",
    [
        MANIFEST,
        "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:1.0,\n{target}\n#EXT-X-ENDLIST\n",
        "ffconcat version 1.0\nfile '{target}'\n",
    ],
    ids=["dash", "hls", "ffconcat"],
)
@pytest.mark.usefixtures("real_ffmpeg")
def test_a_planted_cut_that_names_another_tenants_film_is_never_followed(tmp_path, planted):
    """A supplied `build/sections/01.mp4` that is a DASH manifest names another tenant's film, and is never joined.

    The refusal is the measure, since joining it would make a film out of the other tenant's.
    """
    outside = _film(tmp_path / "tenant-b" / "film.mp4")
    sections = tmp_path / "tenant-a" / "build" / "sections"
    hostile = sections / "01.mp4"
    hostile.parent.mkdir(parents=True)
    hostile.write_text(planted.format(target=outside.resolve().as_posix()), encoding="utf-8")
    with pytest.raises(ToolError):
        concat([hostile, _film(sections / "02.mp4")], sections / "picture.mp4")


@pytest.mark.media
@pytest.mark.usefixtures("real_ffmpeg")
def test_a_planted_manifest_that_names_a_host_reaches_nothing(tmp_path, httpserver):
    """The segment is on a listener this test holds, so what is measured is the request that never came."""
    hostile = tmp_path / "build" / "sections" / "01.mp4"
    hostile.parent.mkdir(parents=True)
    hostile.write_text(MANIFEST.format(target=httpserver.url_for("/seg.mp4")), encoding="utf-8")
    with pytest.raises(ToolError):
        concat([hostile], hostile.with_name("picture.mp4"))
    assert httpserver.log == []
