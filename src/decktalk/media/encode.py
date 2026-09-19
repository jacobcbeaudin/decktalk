"""The one encoder, so every intermediate file and the final mp4 are made the same way.

A section render, a clip and the concatenated film all pass through `Encoder`, which means a
frame that survives one of them survives all of them, and a change of quality is one change here.
"""

from __future__ import annotations

from ..settings import VideoConfig


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
