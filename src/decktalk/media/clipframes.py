"""The frames of a pane clip, decoded on the clip's own clock and handed to a page one second at a time.

A pane clip is drawn inside a page as a picture its origin answers, rather than as a video the
browser plays at its own rate, so the stepped page shows exactly the frame of the second it was moved
to and a recording of it is a function of its seconds like every other. The page asks for the frame
showing at one second on the clip's clock, and the frame showing at a second is the last one that has
begun by then at the film's rate, which is exactly what a player shows.

Each clip is decoded once, in order, by one ffmpeg that runs for as long as the page does and writes
every frame at the film's rate as a BMP, which the browser decodes without inflating anything. A
recording asks for its frames in order, so each request reads on from the last. A request for a frame
already passed starts the decode again from the clip's first frame, which is slower and gives the same
frames, because a decode from the start never depends on where the one before it stopped. Once the
clip has ended its last frame is held, which is what the pane shows after the clip has played.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import ffmpeg

log = logging.getLogger(__name__)

HALF_MILLISECOND = 0.0005
"""Truth: half the precision a second is written to, which a second read back may sit either side of its frame by."""

BMP_HEADER_BYTES = 14
"""Truth: the length of a BMP's file header, whose bytes two to six hold the whole file's length."""

BMP_LENGTH = slice(2, 6)
"""Truth: where a BMP's file header holds the whole file's length, as a little-endian number."""

FRAME_TYPE = "image/bmp"
"""What one frame is answered as, which the browser decodes as a picture."""

LANDINGS_SUFFIX = ".landings.json"
"""What the file beside a clip that holds the clip's own landings is called after the clip's own name."""


def landings_of(clip: Path) -> Path:
    """Where a clip's own landings sit, beside it under its name, such as `tone.landings.json` beside `tone.mp4`.

    A film DeckTalk built writes its own there, and a command that makes a clip may, so a pane that
    plays either has the clip's landings for verify to measure on the film's clock.
    """
    return clip.with_name(f"{clip.stem}{LANDINGS_SUFFIX}")


def frame_index(second: float, fps: int) -> int:
    """The frame of a clip decoded at `fps` that shows at `second` on its own clock, which is the last one begun.

    A second arrives written to the millisecond, so it is read as late as that rounding allows, and a
    second that falls on a frame's start shows that frame rather than the one before it.
    """
    return max(0, math.floor((second + HALF_MILLISECOND) * fps))


@dataclass
class Decode:
    """One clip being decoded in order, the frame it last read and how far it has read."""

    executable: str
    path: Path
    fps: int
    width: int | None
    height: int | None
    pipe: ffmpeg.Pulled | None = None
    read: int = 0
    """How many frames the decode has handed over, so the last one read is frame `read - 1`."""
    last: bytes | None = None
    ended: bool = False

    def args(self) -> list[str]:
        """The decode: every frame at the film's rate, no larger than the film's frame, as a stream of BMPs."""
        fit = (
            f",scale=w='min(iw,{self.width})':h='min(ih,{self.height})':force_original_aspect_ratio=decrease"
            if self.width and self.height
            else ""
        )
        return [
            *ffmpeg.source(self.path), "-an", "-sn", "-vf", f"fps={self.fps}{fit}",
            "-pix_fmt", "bgr24", "-c:v", "bmp", "-f", "image2pipe", "-",
        ]  # fmt: skip

    def restart(self) -> None:
        self.close()
        self.pipe = ffmpeg.pulled(self.executable, *self.args())
        self.read, self.last, self.ended = 0, None, False

    def next_frame(self) -> bytes | None:
        """The next BMP the decode writes, or None once it has written its last."""
        if self.pipe is None:
            return None
        header = self.pipe.read(BMP_HEADER_BYTES)
        if len(header) < BMP_HEADER_BYTES:
            return None
        length = int.from_bytes(header[BMP_LENGTH], "little")
        body = self.pipe.read(length - BMP_HEADER_BYTES)
        return header + body if len(body) == length - BMP_HEADER_BYTES else None

    def frame(self, index: int) -> bytes | None:
        """Frame `index` of the clip, its last frame once it has ended, or None when it has no frame at all."""
        if self.pipe is None or index < self.read - 1:
            self.restart()
        while self.read <= index and not self.ended:
            got = self.next_frame()
            if got is None:
                self.ended = True
                if self.last is None and self.pipe is not None:
                    log.warning("%s could not be decoded into a frame: %s", self.path.name, self.pipe.said())
                break
            self.last = got
            self.read += 1
        return self.last

    def close(self) -> None:
        if self.pipe is not None:
            self.pipe.close()
            self.pipe = None


@dataclass
class ClipFrames:
    """Every pane clip one page plays, each decoded at the film's rate and no larger than its frame.

    `executable` is the ffmpeg a run resolved, because a page asks for a frame from a callback outside
    the run that bound the machine's tools. Without one, the ffmpeg this machine names is looked up the
    first time a page asks for a frame, so a page with no pane clip never looks for ffmpeg at all.
    Without a frame size a clip is decoded at its own, which is what a still asks for, since a still
    shows only a clip's first frame. One lock serves every request, because a preview server answers
    several at once and a decode is read in order.
    """

    fps: int
    executable: str | None = None
    width: int | None = None
    height: int | None = None
    held: bool = True
    """Whether each decode stays running for the next frame, which a recording asks for in order.

    A still asks a clip for one frame, so its decode is stopped as soon as that frame is read and no
    decode outlives the page that asked for it.
    """
    decodes: dict[Path, Decode] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def frame(self, path: Path, second: float) -> bytes | None:
        """The frame of the clip at `path` showing at `second` on its own clock, or None when it has none."""
        with self.lock:
            decode = self.decodes.get(path)
            if decode is None:
                executable = self.executable or ffmpeg.ffmpeg()
                decode = self.decodes[path] = Decode(executable, path, self.fps, self.width, self.height)
            shown = decode.frame(frame_index(second, self.fps))
            if not self.held:
                decode.close()
                del self.decodes[path]
            return shown

    def close(self) -> None:
        """Stop every decode still running."""
        with self.lock:
            for decode in self.decodes.values():
                decode.close()
            self.decodes.clear()


__all__ = ["FRAME_TYPE", "LANDINGS_SUFFIX", "ClipFrames", "frame_index", "landings_of"]
