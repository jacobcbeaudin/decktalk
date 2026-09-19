"""Frame analysis against real ffmpeg on a synthetic video.

These tests need the ffmpeg that `decktalk install` fetches, so they carry the media marker:

    uv run pytest -m media

The video is built from ffmpeg's lavfi sources with colored edges, because a comparison that
leaves YUV reports changed pixels on colored edges that did not change, and a gray test card
would hide that. It runs past the coarse seek in frame_seek, and its keyframes are sparse, the
way an assembled section's are.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.media import audio, ffmpeg, frames
from decktalk.settings import Settings
from decktalk.stages.verify.measure import best_probe, first_change_offset
from decktalk.stages.verify.plan import probe_plan, reference_time
from decktalk.verdicts import Verdict

pytestmark = pytest.mark.media

FPS = 25
W, H = 480, 270
REVEAL_FRAME = 125  # 5.00 s. A small black square appears on this frame.
FULL_FRAME = 126  # 5.04 s. The full accent panel covers it on the next frame.
PANEL_PERCENT = 100 * 60 / (W * H) * 100  # the 100 x 60 panel's share of the picture


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    """Six seconds of a static colored card, then a two-frame reveal at 5.00 s."""
    out = tmp_path_factory.mktemp("media") / "card.mp4"
    boxes = [
        "drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill",
        "drawbox=x=20:y=40:w=3:h=200:color=0xff0000:t=fill",
        "drawbox=x=30:y=40:w=2:h=200:color=0x00c000:t=fill",
        "drawbox=x=40:y=40:w=1:h=200:color=0xff00ff:t=fill",
        "drawbox=x=60:y=40:w=160:h=160:color=0x2c1fea:t=3",
        "drawbox=x=90:y=70:w=100:h=100:color=0xffcc00:t=2",
        f"drawbox=x=320:y=120:w=20:h=20:color=black:t=fill:enable='gte(n,{REVEAL_FRAME})'",
        f"drawbox=x=300:y=100:w=100:h=60:color=0x2c1fea:t=fill:enable='gte(n,{FULL_FRAME})'",
    ]
    ffmpeg.run(
        "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r={FPS}:d=6," + ",".join(boxes),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out),
    )  # fmt: skip
    return out


def _grid(t: float) -> float:
    """The time of the first frame at or after t."""
    return round(math.ceil(t * FPS - 1e-6) / FPS, 3)


@pytest.mark.parametrize("ref_t", [4.40, 4.41, 4.43, 4.439, 4.44])
def test_changed_series_reads_zero_on_a_static_colored_card_at_every_grid_phase(card, ref_t):
    series = frames.changed_series(card, ref_t, ref_t, ref_t + 0.4, fps=FPS, level=12, width=W, height=H)
    assert series, "ffmpeg returned no frames"
    # The first pair is the reference compared with itself, on the frame at or after ref_t.
    assert series[0][0] == _grid(ref_t)
    # Nothing on the card changes before 5.00 s, so no frame may report a changed pixel.
    assert [p for _, p in series] == [0.0] * len(series)


def test_changed_series_times_are_the_frames_own_positions(card):
    series = frames.changed_series(card, 4.93, 4.93, 5.2, fps=FPS, level=12, width=W, height=H)
    shares = dict(series)
    assert shares[4.96] == 0.0
    assert 0.2 < shares[5.0] < 0.45  # the black square alone, 400 px
    assert PANEL_PERCENT * 0.9 < shares[5.04] < PANEL_PERCENT * 1.1


def test_changed_pixels_percent_reads_zero_for_the_same_colored_picture(card):
    assert frames.changed_pixels_percent(card, 1.0, 4.0, level=12, width=W, height=H) == 0.0
    assert frames.changed_pixels_percent(card, 4.43, 4.44, level=12, width=W, height=H) == 0.0
    share = frames.changed_pixels_percent(card, 4.9, 5.5, level=12, width=W, height=H)
    assert PANEL_PERCENT * 0.9 < share < PANEL_PERCENT * 1.1


@pytest.fixture(scope="module")
def moving_card(tmp_path_factory) -> Path:
    """A two-pixel dot moves every frame, and the accent panel appears at 5.04 s."""
    out = tmp_path_factory.mktemp("media") / "moving.mp4"
    graph = (
        f"color=c=white:s={W}x{H}:r={FPS}:d=6,drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill,"
        f"drawbox=x=300:y=100:w=100:h=60:color=0x2c1fea:t=fill:enable='gte(n,{FULL_FRAME})'[bg];"
        f"color=c=0x333333:s=2x2:r={FPS}:d=6[dot];[bg][dot]overlay=x='60+3*n':y=230:eval=frame[out0]"
    )
    ffmpeg.run(
        "-f", "lavfi", "-i", graph,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out),
    )  # fmt: skip
    return out


@pytest.mark.parametrize("before", [4.88, 4.93, 4.959])
def test_onset_ignores_a_few_pixels_of_motion_before_the_reveal(moving_card, before):
    cfg = Settings().verify
    series = frames.changed_series(moving_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    motion = max(p for t, p in series if t < FULL_FRAME / FPS)
    # The dot is a real change of a few pixels, and it must stay under the onset threshold.
    assert 0.0 < motion < cfg.onset_percent
    assert first_change_offset(moving_card, before, 5.7, FULL_FRAME / FPS, cfg, FPS) == 0


@pytest.fixture(scope="module")
def ringing_card(tmp_path_factory) -> Path:
    """A 1080p card whose accent panel appears at 5.00 s, after two frames of encoder-like ringing.

    On the two frames before the panel, a checkerboard of 4 px cells 30 levels either side of the
    gray card covers the panel's area, the way x264 leaves a little ringing on a still picture just
    before a change. It survives the scale to 480 by 270 but averages to nothing over an 8 by 8
    block, so it is not a reveal.
    """
    out = tmp_path_factory.mktemp("media") / "ringing.mp4"
    ring = f"between(N,{REVEAL_FRAME - 2},{REVEAL_FRAME - 1})*between(X,1200,1599)*between(Y,400,639)"
    graph = (
        f"color=c=gray:s=1920x1080:r={FPS}:d=6,format=gray,"
        f"geq=lum='if({ring},158-60*mod(floor(X/4)+floor(Y/4)\\,2),128)',"
        f"drawbox=x=1200:y=400:w=400:h=240:color=0x2c1fea:t=fill:enable='gte(n,{REVEAL_FRAME})',format=yuv420p"
    )
    ffmpeg.run("-f", "lavfi", "-i", graph, "-c:v", "libx264", "-qp", "0", "-g", "250", str(out))  # fmt: skip
    return out


def test_onset_ignores_encoder_ringing_before_the_reveal(ringing_card):
    cfg = Settings().verify
    before = 4.84
    # At the comparison size the ringing is a real change of far more than onset_percent ...
    series = frames.changed_series(ringing_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    assert max(p for t, p in series if t < REVEAL_FRAME / FPS) > 10 * cfg.onset_percent
    # ... but no 8 by 8 block changes, so the onset scan still finds the panel on its own frame.
    assert first_change_offset(ringing_card, before, 5.7, REVEAL_FRAME / FPS, cfg, FPS) == 0


@pytest.mark.parametrize("before", [4.88, 4.89, 4.90, 4.92, 4.93, 4.959])
def test_onset_is_the_first_revealed_frame_at_every_grid_phase(card, before):
    cfg = Settings().verify
    reveal = REVEAL_FRAME / FPS
    assert first_change_offset(card, before, 5.7, reveal, cfg, FPS) == 0
    # A cue between two frames reports the distance to the frame that shows the reveal.
    assert first_change_offset(card, before, 5.7, reveal + 0.02, cfg, FPS) == -20


A_FRAME, B_FRAME = 25, 39  # 1.00 s and 1.56 s, 0.56 s apart
A_PERCENT = 100 * 100 * 60 / (W * H)
B_PERCENT = 100 * 40 * 30 / (W * H)


@pytest.fixture(scope="module")
def close_card(tmp_path_factory) -> Path:
    """A three-second section whose large panel appears at 1.00 s and a smaller box 0.56 s later."""
    out = tmp_path_factory.mktemp("media") / "close.mp4"
    boxes = [
        "drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill",
        "drawbox=x=60:y=40:w=160:h=160:color=0x2c1fea:t=3",
        f"drawbox=x=300:y=100:w=100:h=60:color=0x2c1fea:t=fill:enable='gte(n,{A_FRAME})'",
        f"drawbox=x=60:y=220:w=40:h=30:color=black:t=fill:enable='gte(n,{B_FRAME})'",
    ]
    ffmpeg.run(
        "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r={FPS}:d=3," + ",".join(boxes),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out),
    )  # fmt: skip
    return out


def test_close_cues_get_probes_and_controls_that_fit_the_gap(close_card):
    cfg = Settings().verify
    a, b, end = A_FRAME / FPS, B_FRAME / FPS, 3.0

    def measure(cue_at: float, neighbors: list[float]) -> tuple[float, float, float, list[float], bool]:
        before = reference_time(0.0, cue_at, False, 0.0, cfg, FPS)
        assert before is not None
        delays, fitted = probe_plan(cue_at, before, 0.0, end, neighbors, cfg, FPS)
        best = best_probe(close_card, before, 0.0, cue_at, delays, cfg)
        assert best is not None
        margin, chg, ctl, _after = best
        return margin, chg, ctl, delays, fitted

    def lands(margin: float, chg: float) -> bool:
        return chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent

    # Measured without its neighbor, the second cue's only probe has a control that holds the
    # first reveal, which is larger, so the second cue reads NO CHANGE.
    margin, chg, ctl, delays, fitted = measure(b, [])
    assert (delays, fitted) == ([0.7], False) and ctl > A_PERCENT * 0.9 and not lands(margin, chg)
    # With the first cue as its neighbor, the probe shortens until one control span is clear of the
    # first reveal, in the quiet before it, and the second cue lands.
    margin, chg, ctl, delays, fitted = measure(b, [a])
    assert (delays, fitted) == ([0.54], True) and ctl == 0.0
    assert B_PERCENT * 0.9 < chg < B_PERCENT * 1.1 and lands(margin, chg)

    # The first cue's probe no longer reaches the second reveal, so it measures its own panel alone.
    _, alone, _, _, _ = measure(a, [])
    assert alone > (A_PERCENT + B_PERCENT) * 0.9
    margin, chg, ctl, delays, fitted = measure(a, [b])
    assert (delays, fitted) == ([0.42], True) and A_PERCENT * 0.9 < chg < A_PERCENT * 1.1 and lands(margin, chg)

    # The check is never weaker: a cue where nothing appears no longer passes on the next cue's reveal.
    margin, chg, _, _, _ = measure(0.44, [])
    assert lands(margin, chg)
    margin, chg, _, delays, fitted = measure(0.44, [a, b])
    assert (delays, fitted) == ([0.42], True) and not lands(margin, chg)

    # A neighbor outside every span changes nothing.
    assert measure(a, [2.9]) == measure(a, [])


def _synthetic_section(out: Path, panel_x: int, fade_out: bool) -> None:
    """Two seconds of the card with the accent panel at panel_x, fading out over its last 0.16 s like a dip."""
    graph = (
        f"color=c=white:s={W}x{H}:r={FPS}:d=2,drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill,"
        f"drawbox=x={panel_x}:y=100:w=100:h=60:color=0x2c1fea:t=fill"
    )
    if fade_out:
        graph += ",fade=t=out:st=1.84:d=0.16"
    ffmpeg.run(
        "-f", "lavfi", "-i", graph, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out)
    )  # fmt: skip


def test_verify_finds_a_pop_between_synthetic_sections_outside_the_dip(tmp_path):
    from decktalk.model import Project
    from decktalk.stages.verify import verify

    toml = '[project]\nname = "t"\n' + "".join(
        f'[[section]]\nnumber = {n}\npage = "deck/index.html"\n' + ("seamless = true\n" if n > 1 else "")
        for n in (1, 2, 3)
    )
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    p.narration_dir.mkdir(parents=True)
    p.cue_times_path.write_text("{}", encoding="utf-8")
    # Section 2 continues section 1's picture. Section 3 opens with the panel moved, a pop. Every cut dips,
    # so sections 1 and 2 fade out, the way assemble renders a page section before a dip.
    videos = []
    for key, panel_x, fade_out in (("01", 300, True), ("02", 300, True), ("03", 60, False)):
        videos.append(p.sections_dir / f"{key}.mp4")
        _synthetic_section(videos[-1], panel_x, fade_out)
    listing = tmp_path / "sections.txt"
    listing.write_text("".join(f"file '{v.as_posix()}'\n" for v in videos), encoding="utf-8")
    ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(p.final))

    result = verify(p, checks=[])
    assert [(c.key, c.cut_at, c.verdict) for c in result.seams] == [
        ("02", 2.0, Verdict.OK),
        ("03", 4.0, Verdict.POP_AT_CUT),
    ]
    continuous, pop = result.seams
    assert continuous.changed_percent == 0.0
    assert pop.changed_percent > 2 * PANEL_PERCENT * 0.9  # the panel left one place and appeared in another
    assert not result.ok
    # Across the dip itself the frame at 1.96 s is still fading to black, which would read as a pop.
    assert frames.changed_pixels_percent(p.final, 1.95, 2.0, level=40, width=W, height=H) > 10


def test_mix_pauses_the_narration_for_a_clip_between_page_sections(tmp_path):
    """Pages 1 and 3 around a clip at 2, where section 3's words resume after the clip's own sound."""
    from decktalk.artifacts import Take, Takes
    from decktalk.model import Project
    from decktalk.stages.assemble.cut import RenderedSection
    from decktalk.stages.assemble.mix import mix_input_args, plan_mix
    from decktalk.stages.verify.measure import click_offset_ms

    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\nclip = 'broll.m4a'\n"
        "[[section]]\nnumber = 3\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    p.narration_dir.mkdir(parents=True)
    # The track holds section 1 from 0 to 2 s and section 3 from 2 to 4 s, with a click half a second into each.
    audio.write_clicks(p.narration_dir / "narration.mp3", 4.0, [0.5, 2.5], sample_rate=48000, bitrate="128k")
    clip = tmp_path / "broll.m4a"
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=660:r=48000:d=2", "-c:a", "aac", str(clip))
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["01"] = Take(1, "A", "h1.mp3", "h1.words.json", "h1", 0, 2.0, 2.0, speech_end_seconds=0.6)
    takes.sections["03"] = Take(3, "C", "h3.mp3", "h3.words.json", "h3", 0, 2.0, 2.0, speech_end_seconds=0.6)
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "01.webm"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 1.5, "02.mp4 (own audio)", audio=clip),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.0, "03.webm"),
    ]
    plan = plan_mix(p, rows, takes, soundscape=False)
    out = tmp_path / "mix.wav"
    ffmpeg.run(
        "-f", "lavfi", "-t", f"{plan.total}", "-i", "color=c=black:s=64x36:r=25", *mix_input_args(plan),
        "-filter_complex", plan.filter, "-map", "[a]", str(out),
    )  # fmt: skip
    assert abs(ffmpeg.probe_duration(out) - 5.5) < 0.05
    first = click_offset_ms(out, 0.5, 0.25, floor=0.0, ceiling=2.0)
    resumed = click_offset_ms(out, 4.0, 0.25, floor=3.5, ceiling=5.5)
    assert first is not None and abs(first) <= 8
    assert resumed is not None and abs(resumed) <= 8, "section 3's word does not sit half a second after the clip"
    assert audio.rms_db(out, 2.1, 1.3) > -30, "the clip's own sound is missing from the pause"
    assert audio.rms_db(out, 3.55, 0.4) < -50, "something sounds between the clip and section 3's first word"


def test_a_cached_take_with_a_short_tail_is_placed_and_never_rewritten_or_voiced_again(tmp_path):
    """A take whose own silence is shorter than min_tail_seconds gets the rest from the join, and keeps its bytes."""
    from decktalk.artifacts import Take, Takes, Word, write_words
    from decktalk.model import Project
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import narrate, take_name, text_hash, words_name

    class NeverSpeaks:
        name = "never"

        def speak(self, request):
            raise AssertionError("a cached take was sent to the voice again")

        def cache_key(self, request):
            return "never-voice"

    register_speech_provider("never", lambda context: NeverSpeaks())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[narration]\nmin_tail_seconds = 0.9\n[voice]\nprovider = 'never'\n[[section]]\nnumber = 1\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    cfg = p.settings.narration
    p.narration_dir.mkdir(parents=True)
    _all, spoken = p.script_sections()
    seg = spoken[0]
    settings = p.voice.api_settings()
    digest = text_hash(seg, cfg, "never-voice", settings)
    # One second of tone for the speech, then the 0.4 s of silence the voice left after it.
    take = p.narration_dir / take_name(digest)
    ffmpeg.run(
        "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1", "-af", "apad=pad_dur=0.4",
        "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(take),
    )  # fmt: skip
    write_words(p.narration_dir / words_name(digest), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)])
    data = take.read_bytes()
    take_index = Takes(script="script.md", model="m", output_format=cfg.output_format)
    take_index.sections[seg.key] = Take(
        index=1, chapter="Open", file=take_name(digest), words_file=words_name(digest), hash=digest,
        word_count=2, estimated_seconds=1.0, duration_seconds=ffmpeg.probe_duration(take), speech_end_seconds=1.0,
    )  # fmt: skip
    take_index.save(p.takes_path)

    first = narrate(p)
    assert first.cached == [seg.key] and first.synthesized == []
    assert take.read_bytes() == data, "a cached take was rewritten"
    placed = Takes.load(p.takes_path).sections[seg.key]
    assert placed.hash == digest and placed.sound_end_seconds == pytest.approx(1.0, abs=0.03)
    # The section runs for its lead, its take to its last sound, and exactly min_tail_seconds after that.
    lead = p.lead_seconds(seg.key)
    assert (placed.lead_seconds, placed.tail_seconds) == (lead, 0.9)
    assert first.takes.span(seg.key) == pytest.approx(lead + 1.0 + 0.9, abs=0.03)
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(first.takes.total_seconds, abs=0.03)
    assert audio.rms_db(narration, lead + 1.05, 0.8) < -60, "the tail is not silent"

    second = narrate(p)
    assert second.cached == [seg.key] and second.synthesized == []
    assert Takes.load(p.takes_path).sections[seg.key] == placed, "a second run placed the take differently"
    assert take.read_bytes() == data


def test_a_renumbered_section_keeps_its_take_and_is_never_voiced_again(tmp_path):
    """A close that moves from section 2 to section 3 plays the same file, because a take is its content."""
    from decktalk.artifacts import Take, Takes, Word, write_words
    from decktalk.model import Project
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import narrate, take_name, text_hash, words_name

    class NeverSpeaks:
        name = "never-renumbered"

        def speak(self, request):
            raise AssertionError("a renumbered take was sent to the voice again")

        def cache_key(self, request):
            return "never-voice"

    register_speech_provider("never-renumbered", lambda context: NeverSpeaks())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 3. Close\n\nGoodbye now.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        "[narration]\nmin_tail_seconds = 0.5\n[voice]\nprovider = 'never-renumbered'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 3\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    cfg = p.settings.narration
    p.narration_dir.mkdir(parents=True)
    _all, spoken = p.script_sections()
    settings = p.voice.api_settings()
    take_index = Takes(script="script.md", model="m", output_format=cfg.output_format)
    digests: dict[str, str] = {}
    for old_key, seg, freq in [("01", spoken[0], 440), ("02", spoken[1], 660)]:
        digest = text_hash(seg, cfg, "never-voice", settings)
        digests[old_key] = digest
        ffmpeg.run(
            "-f", "lavfi", "-i", f"sine=f={freq}:r=44100:d=1", "-af", "apad=pad_dur=1",
            "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(p.narration_dir / take_name(digest)),
        )  # fmt: skip
        write_words(p.narration_dir / words_name(digest), [Word("word", 0.0, 1.0)])
        take_index.sections[old_key] = Take(
            index=int(old_key), chapter=seg.title, file=take_name(digest), words_file=words_name(digest),
            hash=digest, word_count=2, estimated_seconds=1.0,
            duration_seconds=ffmpeg.probe_duration(p.narration_dir / take_name(digest)),
        )  # fmt: skip
    take_index.save(p.takes_path)

    result = narrate(p)
    assert result.synthesized == [] and result.cached == ["01", "03"]
    moved = Takes.load(p.takes_path)
    assert sorted(moved.sections) == ["01", "03"]
    # The close moved from section 2 to section 3 and plays the very same file, which nothing copied.
    assert moved.sections["03"].file == take_name(digests["02"]) and moved.sections["03"].index == 3
    assert sorted(f.name for f in p.narration_dir.glob("*.mp3")) == sorted(
        [take_name(digests["01"]), take_name(digests["02"]), "narration.mp3"]
    )
    assert result.takes.keys == ["01", "03"]
    assert narrate(p).cached == ["01", "03"]


def _tone_with_tail(path: Path, *, tail: float, rate: int = 44100, bitrate: str = "128k") -> None:
    """One second of tone, then `tail` seconds of silence, the way a voice leaves a pause after its last word."""
    ffmpeg.run(
        "-f", "lavfi", "-i", f"sine=f=440:r={rate}:d=1", "-af", f"apad=pad_dur={tail}",
        "-c:a", "libmp3lame", "-b:a", bitrate, str(path),
    )  # fmt: skip


def test_sound_end_finds_the_last_sound_whether_or_not_the_container_counts_the_encoder_padding(tmp_path):
    """The sound ends where the tone ends, whether or not the container length counts the encoder padding.

    Some ffmpeg builds, such as the static 7.0 build for Apple silicon, count the mp3 encoder padding in the
    container length, so the silence ends just over 0.05 s before the container end. Newer builds, and the
    static builds for Linux and Windows, write the padding into the header, so the two lengths match.
    """
    take = tmp_path / "take.mp3"
    _tone_with_tail(take, tail=1.3)
    assert audio.sound_end(take) == pytest.approx(1.0, abs=0.03)


@pytest.mark.parametrize("tail", [1.3, 0.2])
def test_narrate_twice_leaves_a_voiced_take_untouched(tmp_path, tail):
    """A take lands the same way on every run, and its own silence reaches none of its placement."""
    from decktalk.artifacts import Takes, Word
    from decktalk.model import Project
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import narrate

    class ToneVoice:
        name = f"tone-{tail}"
        calls = 0

        def speak(self, request):
            ToneVoice.calls += 1
            src = tmp_path / "voice.mp3"
            _tone_with_tail(src, tail=tail)
            return src.read_bytes(), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)]

        def cache_key(self, request):
            return f"tone-voice-{tail}"

    register_speech_provider(ToneVoice.name, lambda context: ToneVoice())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 2. Close\n\nBye.\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(
        f"[narration]\nmin_tail_seconds = 1.3\nlead_seconds = 0\n[voice]\nprovider = '{ToneVoice.name}'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\npage = 'a.html'\n",
        encoding="utf-8",
    )
    p = Project.load(tmp_path, environ={})
    first = narrate(p)
    assert first.synthesized == ["01", "02"] and ToneVoice.calls == 2
    entry = Takes.load(p.takes_path).sections["02"]
    take = p.narration_dir / entry.file
    # A long pause the voice left and a short one both give the section 1.3 s after its last sound.
    assert entry.sound_end_seconds == pytest.approx(1.0, abs=0.03)
    assert entry.span_seconds == pytest.approx(1.0 + 1.3, abs=0.03)
    data = take.read_bytes()

    second = narrate(p)
    assert second.cached == ["01", "02"] and second.synthesized == [] and ToneVoice.calls == 2
    again = Takes.load(p.takes_path).sections["02"]
    assert again == entry, "a cached take was placed differently"
    assert take.read_bytes() == data


def test_lead_and_tail_seconds_leave_a_voiced_take_cached(tmp_path):
    """A section's lead and tail are silence placed around its take in narration.mp3, and neither voices it again."""
    from decktalk.artifacts import Takes, Word
    from decktalk.model import Project
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import narrate

    class ToneVoice:
        name = "tone-lead"
        calls = 0

        def speak(self, request):
            ToneVoice.calls += 1
            src = tmp_path / "voice.mp3"
            _tone_with_tail(src, tail=0.8)
            return src.read_bytes(), [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)]

        def cache_key(self, request):
            return "tone-lead-voice"

    register_speech_provider(ToneVoice.name, lambda context: ToneVoice())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n\n## 2. Close\n\nBye.\n", encoding="utf-8")
    base = (
        f"[narration]\nmin_tail_seconds = 0.7\nlead_seconds = 0\n[voice]\nprovider = '{ToneVoice.name}'\n"
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\npage = 'a.html'\n"
    )
    toml = tmp_path / "decktalk.toml"
    toml.write_text(base, encoding="utf-8")
    first = narrate(Project.load(tmp_path, environ={}))
    assert first.synthesized == ["01", "02"] and ToneVoice.calls == 2
    takes_path = tmp_path / "build" / "narration" / "takes.json"
    entry = Takes.load(takes_path).sections["02"]
    take = tmp_path / "build" / "narration" / entry.file
    data = take.read_bytes()
    before_start, before_span = first.takes.start("02"), first.takes.span("02")
    before_words = Project.load(tmp_path, environ={}).narration_words("02", entry.words_file, at=before_start)
    before_total = first.takes.total_seconds

    toml.write_text(base + "lead_seconds = 1.5\n", encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    second = narrate(p)
    assert second.synthesized == [] and second.cached == ["01", "02"] and ToneVoice.calls == 2
    # The take is byte-identical and still cached: a lead is silence placed before it, so only the row records it.
    assert take.read_bytes() == data
    after = Takes.load(takes_path).sections["02"]
    assert after == replace(entry, lead_seconds=1.5) and after.lead_seconds == 1.5
    start = second.takes.start("02")
    assert start == before_start
    assert second.takes.span("02") == pytest.approx(before_span + 1.5, abs=0.002)
    after_words = p.narration_words("02", after.words_file, at=start)
    assert [w.start for w in after_words] == [pytest.approx(w.start + 1.5, abs=0.001) for w in before_words]
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(before_total + 1.5, abs=0.03)
    assert audio.rms_db(narration, start + 0.1, 1.3) < -60, "the lead is not silent"
    assert audio.rms_db(narration, start + 1.55, 0.4) > -30, "the take does not follow the lead"

    toml.write_text(base + "lead_seconds = 1.5\ntail_seconds = 2\n", encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    third = narrate(p)
    assert third.synthesized == [] and third.cached == ["01", "02"] and ToneVoice.calls == 2
    assert take.read_bytes() == data
    tailed = Takes.load(takes_path).sections["02"]
    assert tailed == replace(after, tail_seconds=2.0)
    # The section now runs 2 s past its last sound where it ran 0.7 s, and section 01 keeps its own tail.
    assert third.takes.span("02") == pytest.approx(second.takes.span("02") + 1.3, abs=0.002)
    assert Takes.load(takes_path).sections["01"].tail_seconds == 0.7
    narration = p.narration_path
    assert ffmpeg.decoded_duration(narration) == pytest.approx(third.takes.total_seconds, abs=0.03)
    speech_ends = start + 1.5 + tailed.sound_end_seconds
    assert audio.rms_db(narration, speech_ends + 0.05, 1.9) < -60, "the tail is not silent"
    fourth = narrate(p)
    assert fourth.synthesized == [] and ToneVoice.calls == 2
    assert Takes.load(takes_path).sections["02"] == tailed


def test_clip_cuts_a_section_span_with_its_take_and_its_words(tmp_path, capsys):
    """The picture, the take over the same span after the section's lead, the gain, the hold, and the words file."""
    from decktalk.artifacts import Take, Takes, Word, read_words, write_words
    from decktalk.cli import main
    from decktalk.model import Project
    from decktalk.stages.clip import clip

    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\nchapter = 'Open'\npage = 'a.html'\nlead_seconds = 0.5\n", encoding="utf-8"
    )
    p = Project.load(tmp_path, environ={})
    p.out_dir.mkdir(parents=True)
    p.sections_dir.mkdir(parents=True)
    p.narration_dir.mkdir(parents=True)
    # A 4 s section: red until 2.0 s, then blue.
    ffmpeg.run(
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=2", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(p.section_video(p.sections[0])),
    )  # fmt: skip
    # The take has a tone from 1.5 to 2.0 s, which is 2.0 to 2.5 s in the section after its 0.5 s lead.
    ffmpeg.run(
        "-f", "lavfi", "-i", "sine=f=440:r=44100:d=0.5", "-af", "adelay=1500:all=1,apad=whole_dur=3.5",
        "-c:a", "libmp3lame", "-b:a", "128k", str(p.narration_dir / "01-open.mp3"),
    )  # fmt: skip
    # The take's own words, which the section's 0.5 s lead moves to 0.9, 2.0, 2.25 and 3.0 on its clock.
    write_words(
        p.takes_dir / "01-open.words.json",
        [Word("go", 0.4, 0.6), Word("Watch", 1.5, 1.7), Word("it", 1.75, 2.0), Word("now", 2.5, 2.9)],
    )
    take_index = Takes(script="script.md", model="m", output_format="mp3_44100_128")
    take_index.sections["01"] = Take(
        index=1, chapter="Open", file="01-open.mp3", words_file="01-open.words.json", hash="h",
        word_count=4, estimated_seconds=3.0, duration_seconds=3.5, speech_end_seconds=2.9,
        lead_seconds=0.5, spoken="Go. Watch it, now.",
    )  # fmt: skip
    take_index.save(p.takes_path)

    result = clip(p, 1, start=1.0, end=3.0, out="media/x.mp4", gain_db=-6, hold_seconds=0.4)
    clip_file = tmp_path / "media" / "x.mp4"
    assert (result.video, result.words_file) == (clip_file, tmp_path / "media" / "x.words.json")
    assert (result.first_frame, result.last_frame, result.start, result.end) == (25, 74, 1.0, 3.0)
    assert (result.hold_seconds, result.duration) == (0.4, 2.4)
    assert ffmpeg.probe_duration(clip_file) == pytest.approx(2.4, abs=0.05)
    red, blue = frames.luma_at(clip_file, 0.5)[0], frames.luma_at(clip_file, 1.5)[0]
    assert red > blue + 20, (red, blue)
    assert frames.luma_at(clip_file, 2.2)[0] == pytest.approx(blue, abs=3), "the hold does not show the last frame"
    loud = audio.rms_db(clip_file, 1.05, 0.4)
    assert loud > -40, "the take's tone is not at 1.0 s in the clip"  # The tone is about -24 dB before the -6 dB gain.
    assert audio.rms_db(clip_file, 0.1, 0.8) < -50, "sound before the tone"
    assert audio.rms_db(clip_file, 1.6, 0.7) < -50, "sound after the tone or in the hold"
    # Words wholly inside the span keep the script's spelling, shifted to the clip. "go" crosses the start.
    assert result.words == [Word("Watch", 1.0, 1.2), Word("it,", 1.25, 1.5)]
    assert result.cut_words == ["Go."]
    assert read_words(result.words_file) == result.words

    assert main(["-p", str(tmp_path), "clip", "1", "--start", "1", "--end", "3", "--out", "media/y.mp4"]) == 0
    out = capsys.readouterr().out
    assert "wrote media/y.mp4  (2.00s: frames 25 to 74 of sections/01.mp4, 1.00 to 3.00s, hold 0s, gain +0 dB)" in out
    assert "wrote media/y.words.json  (2 words)" in out
    assert audio.rms_db(tmp_path / "media" / "y.mp4", 1.05, 0.4) == pytest.approx(loud + 6, abs=1)

    # A span inside the lead is silent, and still has its full length.
    early = clip(p, 1, start=0.0, end=0.4, out="media/z.mp4")
    assert early.words == [] and ffmpeg.has_audio(early.video)
    assert ffmpeg.probe_duration(early.video) == pytest.approx(0.4, abs=0.05)


def _take_with_a_noisy_tail(path: Path, *, speech: float) -> None:
    """`speech` seconds of tone, then 0.3 s of noise just under the silence threshold, the way a voice breathes out.

    Re-encoding such a file lets one sample near its end cross the threshold, so any placement that measured
    a file some run had rewritten would drift. This seed and level sit there on purpose.
    """
    ffmpeg.run(
        "-f", "lavfi", "-i", f"sine=f=440:r=44100:d={speech}",
        "-f", "lavfi", "-i", "anoisesrc=r=44100:a=0.0125:c=white:seed=6:d=0.3",
        "-filter_complex", "[0:a]volume=0.25[s];[s][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
        "-c:a", "libmp3lame", "-b:a", "128k", str(path),
    )  # fmt: skip


def test_changing_one_section_leaves_its_neighbours_placed_as_they_were(tmp_path):
    """A rebuild after one section's words change keeps every other section's length, words and recording key.

    A take's place in the narration is a pure function of the take and its own section's settings, so a
    section that was voiced by the last run and reused by this one lands exactly where it did.
    """
    from decktalk.artifacts import Word
    from decktalk.model import Project
    from decktalk.speech import register_speech_provider
    from decktalk.stages.narrate import narrate
    from decktalk.stages.record import jobs

    class BreathingVoice:
        name = "breathing-voice"
        calls: list[str] = []

        def speak(self, request):
            BreathingVoice.calls.append(request.text)
            words = request.text.split()
            speech = 1.2 if len(words) <= 3 else 1.6
            src = tmp_path / "voice.mp3"
            _take_with_a_noisy_tail(src, speech=speech)
            per = speech / len(words)
            timed = [
                Word(w.strip(".,"), round(i * per, 3), round((i + 1) * per - 0.02, 3)) for i, w in enumerate(words)
            ]
            return src.read_bytes(), timed

        def cache_key(self, request):
            return self.name

    register_speech_provider(BreathingVoice.name, lambda context: BreathingVoice())
    (tmp_path / "a.html").write_text("<!doctype html><title>a</title>", encoding="utf-8")
    script = tmp_path / "script.md"
    script.write_text("## 1. Open\n\nHello there.\n\n## 2. Middle\n\nA middle line.\n\n## 3. Close\n\nBye now.\n")
    (tmp_path / "decktalk.toml").write_text(
        f"[narration]\nmin_tail_seconds = 1.3\n[voice]\nprovider = '{BreathingVoice.name}'\n"
        + "".join(f"[[section]]\nnumber = {n}\npage = 'a.html'\nscene = {n}\n" for n in (1, 2, 3)),
        encoding="utf-8",
    )

    def placed() -> dict[str, tuple[float | None, list[Word], str]]:
        p = Project.load(tmp_path, environ={})
        takes = p.takes()
        assert takes is not None
        keys = {job.section.key: job.input_hash for job in jobs(p, None, None, use_cues=False)}
        return {
            key: (takes.span(key), p.section_words(key, takes.sections[key].words_file), keys[key])
            for key in ("01", "02", "03")
        }

    first = narrate(Project.load(tmp_path, environ={}))
    assert first.synthesized == ["01", "02", "03"]
    before = placed()

    script.write_text(script.read_text(encoding="utf-8").replace("A middle line.", "A longer middle line than before."))
    second = narrate(Project.load(tmp_path, environ={}))
    assert second.synthesized == ["02"] and second.cached == ["01", "03"]
    after = placed()
    assert after["02"] != before["02"]
    for key in ("01", "03"):
        assert after[key] == before[key], f"section {key} moved although only section 02 changed"
