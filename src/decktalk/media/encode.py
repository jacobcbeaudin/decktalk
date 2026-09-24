"""The settings and the tags every output shares, so each file DeckTalk writes is made the same way.

A section render, a clip and the concatenated film all pass through `Encoder`, which means a
frame that survives one of them survives all of them, and a change of quality is one change here.
The language tag the final mp4 carries is mapped here for the same reason, because the container
takes a code that `[project] language` does not spell, and the slate colour is converted here
because a stylesheet and an encoder write one colour two ways.
"""

from __future__ import annotations

from typing import Protocol


class VideoSettings(Protocol):
    """What one output is made from, which is the whole of `[video]` this module reads.

    The encoder names the keys it reads rather than importing the settings class, because what an
    output is made from is a fact about encoding and a project is what supplies it. Every member is
    read-only, because an encoder reads its settings and never writes them, and a frozen record of
    the same keys therefore satisfies this without being cast to it.
    """

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def output_fps(self) -> int: ...

    @property
    def crf(self) -> int: ...

    @property
    def preset(self) -> str: ...

    @property
    def sample_rate(self) -> int: ...

    @property
    def channels(self) -> int: ...

    @property
    def audio_bitrate(self) -> str: ...

    @property
    def slate_color(self) -> str: ...


# An mp4 stream's language is an ISO 639-2 three-letter code, while `[project] language` is the BCP 47
# tag the page and the caption files carry, so the primary subtag is mapped here. A language this
# table does not name is written as `und`, which is what a player shows for a stream that says nothing.
ISO_639_2 = {
    "ar": "ara", "bg": "bul", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng",
    "es": "spa", "fi": "fin", "fil": "fil", "fr": "fra", "he": "heb", "hi": "hin", "hr": "hrv",
    "hu": "hun", "id": "ind", "it": "ita", "ja": "jpn", "ko": "kor", "ms": "msa", "nl": "nld",
    "no": "nor", "pl": "pol", "pt": "por", "ro": "ron", "ru": "rus", "sk": "slk", "sv": "swe",
    "ta": "tam", "th": "tha", "tr": "tur", "uk": "ukr", "vi": "vie", "zh": "zho",
}  # fmt: skip
UNKNOWN_LANGUAGE = "und"


FFMPEG_HEX_PREFIX = "0x"
"""How ffmpeg writes a colour it is given as hex, which a stylesheet writes with a hash instead."""


def css_color(value: str) -> str:
    """The colour a `[video]` setting names, written the way a page's stylesheet reads it.

    ffmpeg takes `0xRRGGBB` and a stylesheet takes `#RRGGBB`, and both take a colour name. The one
    setting that says what a missing clip is drawn on is therefore spelled once, in the notation the
    encoder reads, and converted here rather than restated in a second notation beside the page.
    """
    color = value.strip()
    return "#" + color[len(FFMPEG_HEX_PREFIX) :] if color.lower().startswith(FFMPEG_HEX_PREFIX) else color


def iso_639_2(tag: str) -> str:
    """The three-letter code an mp4 stream is tagged with, from a BCP 47 tag such as `en` or `pt-BR`.

    Every tag is looked up, including a three-letter one, because a code this table does not name is
    written as `und` rather than passed through to a player that would have to guess at it.
    """
    primary = tag.strip().lower().split("-")[0]
    return ISO_639_2.get(primary, UNKNOWN_LANGUAGE)


KEYFRAME_SECONDS = 2
"""Truth: a player seeks to a keyframe, and two seconds is the longest wait a scrub should cost."""


class Encoder:
    """The x264 and AAC settings every intermediate and the final file share.

    The frame rate comes from the fps filter in `fit`, so the encoder takes no -r of its
    own. Every output is tagged BT.709 and keyframed every two seconds. The tag is set
    twice on purpose: the encoder flags cover older ffmpeg builds, and the setparams filter
    covers ffmpeg 9, which takes the colour properties from the frames rather than from
    those flags.
    """

    def __init__(self, video: VideoSettings) -> None:
        self.v = video
        self.fit = (
            f"scale={video.width}:{video.height}:force_original_aspect_ratio=decrease,"
            f"pad={video.width}:{video.height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={video.output_fps},format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"
        )
        gop = str(KEYFRAME_SECONDS * video.output_fps)
        self.venc = [
            "-c:v", "libx264",
            "-preset", video.preset,
            "-crf", str(video.crf),
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-g", gop,
            "-keyint_min", gop,
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
        ]  # fmt: skip
        self.aenc = [
            "-c:a", "aac",
            "-b:a", video.audio_bitrate,
            "-ar", str(video.sample_rate),
            "-ac", str(video.channels),
        ]  # fmt: skip
        # The soundtrack is mixed into this before anything is measured or limited, because a fixed
        # point sample clips at 0 dBFS and a sum of layers can pass it before the limiter ever runs.
        self.amix = [
            "-c:a", "pcm_f32le",
            "-ar", str(video.sample_rate),
            "-ac", str(video.channels),
        ]  # fmt: skip
        self.silence = f"anullsrc=r={video.sample_rate}:cl=stereo"

    def color_source(self, color: str, seconds: float) -> list[str]:
        return [
            "-f",
            "lavfi",
            "-t",
            f"{seconds}",
            "-i",
            f"color=c={color}:s={self.v.width}x{self.v.height}:r={self.v.output_fps}",
        ]
