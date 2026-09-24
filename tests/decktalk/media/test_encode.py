"""The settings every output shares, the language tag it carries, and the one spelling of a colour."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from decktalk.media.encode import Encoder, css_color, iso_639_2


@dataclass
class Video:
    """What `[video]` hands the encoder, stood in for here so this test needs no settings file."""

    width: int = 1920
    height: int = 1080
    output_fps: int = 30
    crf: int = 18
    preset: str = "medium"
    sample_rate: int = 48000
    channels: int = 2
    audio_bitrate: str = "192k"


@pytest.mark.parametrize(
    ("tag", "want"),
    [
        ("en", "eng"),
        ("EN", "eng"),
        (" en-US ", "eng"),
        ("pt-BR", "por"),
        ("zh-Hans", "zho"),
        ("fil", "fil"),
        ("kw", "und"),
        ("qqq", "und"),
        ("cmn", "und"),
        ("", "und"),
    ],
)
def test_the_stream_language_is_a_code_the_container_knows(tag, want):
    """A player names the audio from this tag, so a code the table does not hold is `und` and not a guess."""
    assert iso_639_2(tag) == want


@pytest.mark.parametrize(
    ("setting", "want"),
    [("0x0e1116", "#0e1116"), ("0X0E1116", "#0E1116"), ("#0e1116", "#0e1116"), ("black", "black"), (" 0xfff ", "#fff")],
)
def test_the_slate_colour_is_one_setting_a_stylesheet_and_an_encoder_both_read(setting, want):
    """`[video] slate_color` is written the way ffmpeg writes a colour, and converted where a page needs it."""
    assert css_color(setting) == want


def test_the_encoder_tags_every_output_bt709_and_mixes_in_float():
    """One encoder means a frame that survives a section render survives the film."""
    enc = Encoder(Video())
    assert "-colorspace" in enc.venc and enc.venc[enc.venc.index("-colorspace") + 1] == "bt709"
    assert "setparams=color_primaries=bt709" in enc.fit
    assert enc.aenc[:2] == ["-c:a", "aac"] and enc.amix[:2] == ["-c:a", "pcm_f32le"]


def test_the_encoder_takes_its_rate_from_the_output_key_and_keyframes_on_it():
    """`[video] output_fps` is the encoder's rate, so the recorder's own rate never reaches these arguments."""
    enc = Encoder(Video(output_fps=50))
    assert "fps=50," in enc.fit
    assert enc.venc[enc.venc.index("-g") + 1] == "100"
    assert enc.color_source("black", 1.0)[-1].endswith(":r=50")
