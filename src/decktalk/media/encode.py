"""The settings and the tags every output shares, so each file DeckTalk writes is made the same way.

A section render, a clip and the concatenated film all pass through `Encoder`, which means a
frame that survives one of them survives all of them, and a change of quality is one change here.
The language tag the final mp4 carries is mapped here for the same reason, because the container
takes a code that `[project] language` does not spell.
"""

from __future__ import annotations

from ..settings import VideoConfig

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


def iso_639_2(tag: str) -> str:
    """The three-letter code an mp4 stream is tagged with, from a BCP 47 tag such as `en` or `pt-BR`.

    Every tag is looked up, including a three-letter one, because a code this table does not name is
    written as `und` rather than passed through to a player that would have to guess at it.
    """
    primary = tag.strip().lower().split("-")[0]
    return ISO_639_2.get(primary, UNKNOWN_LANGUAGE)


class Encoder:
    """The x264 and AAC settings every intermediate and the final file share.

    The frame rate comes from the fps filter in `fit`, so the encoder takes no -r of its
    own. Every output is tagged BT.709 and keyframed every two seconds. The tag is set
    twice on purpose: the encoder flags cover older ffmpeg builds, and the setparams filter
    covers ffmpeg 9, which takes the colour properties from the frames rather than from
    those flags.
    """

    def __init__(self, video: VideoConfig) -> None:
        self.v = video
        self.fit = (
            f"scale={video.width}:{video.height}:force_original_aspect_ratio=decrease,"
            f"pad={video.width}:{video.height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={video.fps},format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"
        )
        gop = str(2 * video.fps)
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
            f"color=c={color}:s={self.v.width}x{self.v.height}:r={self.v.fps}",
        ]
