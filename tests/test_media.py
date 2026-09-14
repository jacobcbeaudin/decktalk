"""Frame analysis against real ffmpeg on a synthetic video.

These tests need the ffmpeg that `decktalk setup` fetches, so they carry the media marker:

    uv run pytest -m media

The video is built from ffmpeg's lavfi sources with colored edges, because a comparison that
leaves YUV reports changed pixels on colored edges that did not change, and a gray test card
would hide that. It runs past the coarse seek in frame_seek, and its keyframes are sparse, the
way an assembled section's are.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from decktalk.config import Settings
from decktalk.media import ffmpeg
from decktalk.stages.verify import first_change_offset

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
    series = ffmpeg.changed_series(card, ref_t, ref_t, ref_t + 0.4, fps=FPS, level=12, width=W, height=H)
    assert series, "ffmpeg returned no frames"
    # The first pair is the reference compared with itself, on the frame at or after ref_t.
    assert series[0][0] == _grid(ref_t)
    # Nothing on the card changes before 5.00 s, so no frame may report a changed pixel.
    assert [p for _, p in series] == [0.0] * len(series)


def test_changed_series_times_are_the_frames_own_positions(card):
    series = ffmpeg.changed_series(card, 4.93, 4.93, 5.2, fps=FPS, level=12, width=W, height=H)
    shares = dict(series)
    assert shares[4.96] == 0.0
    assert 0.2 < shares[5.0] < 0.45  # the black square alone, 400 px
    assert PANEL_PERCENT * 0.9 < shares[5.04] < PANEL_PERCENT * 1.1


def test_changed_pixels_percent_reads_zero_for_the_same_colored_picture(card):
    assert ffmpeg.changed_pixels_percent(card, 1.0, 4.0, level=12, width=W, height=H) == 0.0
    assert ffmpeg.changed_pixels_percent(card, 4.43, 4.44, level=12, width=W, height=H) == 0.0
    share = ffmpeg.changed_pixels_percent(card, 4.9, 5.5, level=12, width=W, height=H)
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
    series = ffmpeg.changed_series(moving_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    motion = max(p for t, p in series if t < FULL_FRAME / FPS)
    # The dot is a real change of a few pixels, and it must stay under the onset threshold.
    assert 0.0 < motion < cfg.onset_percent
    assert first_change_offset(moving_card, before, 5.7, FULL_FRAME / FPS, cfg, FPS) == 0


@pytest.mark.parametrize("before", [4.88, 4.89, 4.90, 4.92, 4.93, 4.959])
def test_onset_is_the_first_revealed_frame_at_every_grid_phase(card, before):
    cfg = Settings().verify
    reveal = REVEAL_FRAME / FPS
    assert first_change_offset(card, before, 5.7, reveal, cfg, FPS) == 0
    # A cue between two frames reports the distance to the frame that shows the reveal.
    assert first_change_offset(card, before, 5.7, reveal + 0.02, cfg, FPS) == -20


def test_mix_pauses_the_narration_for_a_clip_between_page_sections(tmp_path):
    """Pages 1 and 3 around a clip at 2, where section 3's words resume after the clip's own sound."""
    from decktalk.artifacts import Timeline, TimelineSection
    from decktalk.project import Project
    from decktalk.stages.assemble import RenderedSection, mix_input_args, plan_mix
    from decktalk.stages.verify import click_offset_ms

    (tmp_path / "decktalk.toml").write_text(
        "[[section]]\nnumber = 1\npage = 'a.html'\n[[section]]\nnumber = 2\nclip = 'broll.m4a'\n"
        "[[section]]\nnumber = 3\npage = 'a.html'\n"
    )
    p = Project.load(tmp_path, environ={})
    p.audio_dir.mkdir(parents=True)
    # The track holds section 1 from 0 to 2 s and section 3 from 2 to 4 s, with a click half a second into each.
    ffmpeg.write_clicks(p.audio_dir / "narration.mp3", 4.0, [0.5, 2.5], sample_rate=48000, bitrate="128k")
    clip = tmp_path / "broll.m4a"
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=660:r=48000:d=2", "-c:a", "aac", str(clip))
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=4.0,
        sections={"01": TimelineSection("A", 0.0, 2.0, 2.0, 0.6), "03": TimelineSection("C", 2.0, 4.0, 2.0, 2.6)},
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 2.0, "page"),
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 1.5, "clip", audio=clip),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.0, "page"),
    ]
    plan = plan_mix(p, rows, tl, nomix=True)
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
    assert ffmpeg.rms_db(out, 2.1, 1.3) > -30, "the clip's own sound is missing from the pause"
    assert ffmpeg.rms_db(out, 3.55, 0.4) < -50, "something sounds between the clip and section 3's first word"


def test_a_cached_take_is_padded_to_a_longer_min_tail_once_and_never_voiced_again(tmp_path):
    """A take voiced under a short tail keeps its hash when min_tail_seconds grows, so narrate pads it in place."""
    from decktalk.artifacts import Manifest, ManifestSegment, Word, write_words
    from decktalk.project import Project
    from decktalk.providers.speech import register
    from decktalk.stages.narrate import narrate, script_segments, text_hash

    class NeverSpeaks:
        name = "never"

        def speak(self, request):
            raise AssertionError("a cached take was sent to the voice again")

        def cache_key(self, request):
            return "never-voice"

    register("never", lambda project: NeverSpeaks())
    (tmp_path / "script.md").write_text("## 1. Open\n\nHello there.\n")
    (tmp_path / "decktalk.toml").write_text(
        "[narration]\nmin_tail_seconds = 0.9\n[voice]\nprovider = 'never'\n[[section]]\nnumber = 1\npage = 'a.html'\n"
    )
    p = Project.load(tmp_path, environ={})
    cfg = p.settings.narration
    p.audio_dir.mkdir(parents=True)
    # One second of tone for the speech, then the 0.4 s tail an earlier min_tail_seconds left.
    take = p.audio_dir / "01-open.mp3"
    ffmpeg.run(
        "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1", "-af", "apad=pad_dur=0.4",
        "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, str(take),
    )  # fmt: skip
    write_words(p.audio_dir / "01-open.words.json", [Word("Hello", 0.0, 0.5), Word("there", 0.5, 1.0)])
    _all, spoken = script_segments(p)
    seg = spoken[0]
    settings = p.voice.api_settings()
    digest = text_hash(seg, cfg, "never-voice", settings)
    before = ffmpeg.probe_duration(take)
    manifest = Manifest(script="script.md", model="m", output_format=cfg.output_format)
    manifest.segments[seg.key] = ManifestSegment(
        index=1, title="Open", file=seg.filename, words_file=seg.words_filename, hash=digest,
        words=2, est_seconds=1.0, duration_seconds=before, speech_end_seconds=1.0, tail_padded_seconds=0.1,
    )  # fmt: skip
    manifest.save(p.manifest_path)
    assert ffmpeg.trailing_silence(take) < 0.5

    first = narrate(p)
    assert first.cached == [seg.key] and first.synthesized == []
    assert ffmpeg.trailing_silence(take) >= cfg.min_tail_seconds
    padded = Manifest.load(p.manifest_path).segments[seg.key]
    assert padded.hash == digest, "padding changed the cache key"
    assert padded.duration_seconds > before + 0.4
    assert padded.duration_seconds == pytest.approx(ffmpeg.probe_duration(take), abs=0.001)
    assert padded.tail_padded_seconds > 0.5
    assert first.timeline.sections[seg.key].duration == pytest.approx(padded.duration_seconds, abs=0.06)

    second = narrate(p)
    assert second.cached == [seg.key] and second.synthesized == []
    again = Manifest.load(p.manifest_path).segments[seg.key]
    assert again.duration_seconds == padded.duration_seconds, "a second run padded the take again"
    assert again.tail_padded_seconds == padded.tail_padded_seconds
