"""Where a take stops sounding, which is what places every take in the narration track."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.media import audio, ffmpeg
from support.media_cards import write_tone_with_tail

# What `[narration] sound_end_noise_dbfs` and `sound_end_min_run_seconds` default to, which is what
# a project that has tuned neither of them hands this module.
NOISE_DBFS = -35.0
MIN_RUN_SECONDS = 0.05


def ends(path: Path) -> float:
    """Where the sound ends, at the two bounds a project that tuned nothing would pass."""
    return audio.sound_end(path, noise_dbfs=NOISE_DBFS, min_run_seconds=MIN_RUN_SECONDS)


def test_sound_end_counts_a_silence_ending_0_0502_s_before_the_end_as_running_to_it(monkeypatch):
    """The numbers of the demo's 08-the-edit.mp3, whose last silence ends in the encoder padding."""
    detect = (
        "  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, mono, fltp, 128 kb/s\n"
        "[silencedetect @ 0x1] silence_start: 13.914717\n"
        "[silencedetect @ 0x1] silence_end: 13.974331 | silence_duration: 0.0596145\n"
        "[silencedetect @ 0x1] silence_start: 14.380816\n"
        "[silencedetect @ 0x1] silence_end: {end} | silence_duration: 1.320998\n"
    )
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 15.752)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.701814))
    assert ends(Path("08-the-edit.mp3")) == 14.381
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: detect.format(end=15.5))  # sound after the silence
    assert ends(Path("08-the-edit.mp3")) == 15.752
    # At 8 kHz one mp3 frame lasts 0.144 s, longer than the fixed tolerance.
    low = detect.replace("44100 Hz", "8000 Hz").format(end=15.62)
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: low)
    assert ends(Path("08-the-edit.mp3")) == 14.381
    # A silence silencedetect never closed runs to the end, and a file with no silence sounds to its end.
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: "Audio: mp3, 44100 Hz\nsilence_start: 15.629\n")
    assert ends(Path("a.mp3")) == 15.629
    monkeypatch.setattr(ffmpeg, "stderr", lambda *args: "Audio: mp3, 44100 Hz\n")
    assert ends(Path("a.mp3")) == 15.752


@pytest.mark.media
def test_sound_end_finds_the_last_sound_whether_or_not_the_container_counts_the_encoder_padding(tmp_path):
    """The sound ends where the tone ends, whether or not the container length counts the encoder padding.

    Some ffmpeg builds, such as the static 7.0 build for Apple silicon, count the mp3 encoder padding in the
    container length, so the silence ends just over 0.05 s before the container end. Newer builds, and the
    static builds for Linux and Windows, write the padding into the header, so the two lengths match.
    """
    take = write_tone_with_tail(tmp_path / "take.mp3", tail=1.3)
    assert ends(take) == pytest.approx(1.0, abs=0.03)


@pytest.mark.media
def test_a_span_of_samples_is_read_at_the_rate_the_project_renders_at(tmp_path):
    """`pcm_span` takes its rate from `[video] sample_rate`, so nothing below the mix defaults one."""
    take = write_tone_with_tail(tmp_path / "tone.mp3", tail=0.2)
    samples = audio.pcm_span(take, 0.0, 0.5, sample_rate=48000)
    assert len(samples) == pytest.approx(24000, abs=2000)
    assert max(abs(s) for s in samples) > 1000


@pytest.mark.media
def test_a_span_of_samples_from_a_file_ffmpeg_cannot_read_is_a_tool_error(tmp_path):
    """The decode goes through `ffmpeg.raw`, so a failure is refused rather than read as silence."""
    broken = tmp_path / "not-audio.mp3"
    broken.write_bytes(b"this is not an mp3")
    with pytest.raises(ToolError):
        audio.pcm_span(broken, 0.0, 0.5, sample_rate=48000)
