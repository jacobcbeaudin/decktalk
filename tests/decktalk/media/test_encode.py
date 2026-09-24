"""The settings and the language tag every output shares."""

from __future__ import annotations

import pytest

from decktalk.media.encode import Encoder, iso_639_2
from decktalk.settings import Settings


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


def test_the_encoder_tags_every_output_bt709_and_mixes_in_float():
    """One encoder means a frame that survives a section render survives the film."""
    enc = Encoder(Settings().video)
    assert "-colorspace" in enc.venc and enc.venc[enc.venc.index("-colorspace") + 1] == "bt709"
    assert "setparams=color_primaries=bt709" in enc.fit
    assert enc.aenc[:2] == ["-c:a", "aac"] and enc.amix[:2] == ["-c:a", "pcm_f32le"]
