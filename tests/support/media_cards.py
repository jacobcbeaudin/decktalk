"""Synthetic video and audio that more than one media test measures.

The card is built from ffmpeg's lavfi sources with coloured edges, because a comparison that leaves
YUV reports changed pixels on coloured edges that did not change, and a grey test card would hide
that. It runs past the coarse seek in `frame_seek`, and its keyframes are sparse, the way an
assembled section's are. Each module wraps these in a module-scoped fixture of its own, so nothing
here collects and nothing here is a fixture.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.media import ffmpeg

FPS = 25
W, H = 480, 270
REVEAL_FRAME = 125  # 5.00 s. A small black square appears on this frame.
FULL_FRAME = 126  # 5.04 s. The full accent panel covers it on the next frame.
PANEL_PERCENT = 100 * 60 / (W * H) * 100  # the 100 x 60 panel's share of the picture


def write_card(out: Path) -> Path:
    """Six seconds of a static coloured card, then a two-frame reveal at 5.00 s."""
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


def write_tone_with_tail(path: Path, *, tail: float, rate: int = 44100, bitrate: str = "128k") -> Path:
    """One second of tone, then `tail` seconds of silence, the way a voice leaves a pause after its last word."""
    ffmpeg.run(
        "-f", "lavfi", "-i", f"sine=f=440:r={rate}:d=1", "-af", f"apad=pad_dur={tail}",
        "-c:a", "libmp3lame", "-b:a", bitrate, str(path),
    )  # fmt: skip
    return path
