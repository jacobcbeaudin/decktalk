"""Finding ffmpeg and ffprobe for this machine, running them, and probing what they read.

`[tools] ffmpeg` and `ffprobe` win, the pinned build that `toolchain/ffmpeg_fetch.py` downloads
comes next, and a build on PATH is the fallback, so every machine renders with the same ffmpeg
unless it is told otherwise. Those two keys are a pair: a machine that names one half and not the
other is refused rather than quietly rendered with a build it did not ask for.

The keys reach this module through `using_tools`, which the machine opens for a run, because `run`
and `stderr` are called from inside a filter chain and threading a settings object through every one
of them would put a project in the middle of an audio filter.

Every call goes through `_checked`, so a return code other than zero raises `ToolError` carrying
the tail of what the tool said. A measurement that read a failure as silence, as blackness or as a
missing audio stream would turn a broken tool into a verdict about the film, which is the one
mistake this module exists to prevent.

`audio.py` and `frames.py` build on the five calls here: `run`, `stderr`, `raw`, `probe_duration`
and `has_audio`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from ..errors import ToolError
from ..findings import Location
from ..settings import ToolsConfig
from ..toolchain import ffmpeg_fetch
from ..toolchain.cache import caching_in

log = logging.getLogger(__name__)

NAMED = ("tools.ffmpeg", "tools.ffprobe")
"""The two keys that name a build of the machine's own, which are set together or not at all."""

TOOLS: ContextVar[ToolsConfig | None] = ContextVar("decktalk_tools", default=None)
"""What this run renders with, which `using_tools` sets and every call below reads."""


def bound_tools() -> ToolsConfig:
    """The tools in force, which is what a run was bound to or a machine that named none of its own."""
    return TOOLS.get() or ToolsConfig()


@contextmanager
def using_tools(tools: ToolsConfig) -> Iterator[None]:
    """Render with the executables and the cache directory `[tools]` names, while this is open.

    One call binds a run to a machine's own toolchain, so nothing between the machine and an audio
    filter has to carry a settings object to say which ffmpeg this is.
    """
    token = TOOLS.set(tools)
    try:
        with caching_in(tools.cache_dir):
            yield
    finally:
        TOOLS.reset(token)


TAIL_LINES = 6
"""Truth: ffmpeg says what it could not do in its last few lines, and everything above is what it read."""

HINT_CHARS = 200
"""Truth: a hint is read in one glance beside the sentence it follows, so it carries about two lines."""


def _tail(err: bytes) -> str:
    """The last lines of what a tool wrote, as one line, which is where the reason for a failure is."""
    lines = err.decode(errors="replace").strip().splitlines()
    return " | ".join(line.strip() for line in lines[-TAIL_LINES:]) or "it said nothing"


def _checked(cmd: list[str], what: str, *, location: Location | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run one tool call and hand back what it wrote, or raise `ToolError` carrying the tail of its complaint.

    Every invocation in this package comes through here, so no caller can read a failed run as a
    measurement. The output is captured as bytes, because a decoder writes samples to stdout and a
    filter writes its report to stderr in the same call shape.
    """
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise ToolError(f"{what} failed: {_tail(proc.stderr)}", location=location)
    return proc


def _at(path: Path | str) -> Location:
    """Where a file failure happened, which is the file the tool was given."""
    return Location(where=Path(path).name, file=Path(path))


def _named(tools: ToolsConfig) -> tuple[str, str] | None:
    """The pair `[tools]` names, when both keys are set and both name a file that is there."""
    if not (tools.ffmpeg and tools.ffprobe):
        return None
    return (tools.ffmpeg, tools.ffprobe) if not missing_tools(tools) else None


def missing_tools(tools: ToolsConfig | None = None) -> list[str]:
    """The keys that name a file which is not there, so a typo is reported and never resolved.

    `doctor` reports this and `ffmpeg_paths` refuses on it, which is the difference between a command
    whose work is to report what a machine has and one that needs the tool to do anything at all.
    """
    named = _stated(tools or bound_tools())
    return [key for key, value in named.items() if not Path(value).is_file()]


def unpaired_tool(tools: ToolsConfig | None = None) -> list[str]:
    """The half of the pair that is not set, when the other half is, which is never a usable build.

    ffmpeg and ffprobe are one build, so naming one of them and leaving the other to PATH renders
    with two builds. The half that is missing is named rather than ignored, because a machine that
    set one key meant to set both and would otherwise never learn that nothing happened.
    """
    stated = _stated(tools or bound_tools())
    return [] if len(stated) != 1 else [key for key in NAMED if key not in stated]


def _stated(tools: ToolsConfig) -> dict[str, str]:
    """Each of the two keys a machine actually filled in, against the path it filled in."""
    return {key: value for key, value in zip(NAMED, (tools.ffmpeg, tools.ffprobe), strict=True) if value}


def _path_pair() -> tuple[str, str] | None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ff, fp) if ff and fp else None


def _refuse_half_a_build(tools: ToolsConfig) -> None:
    """Refuse a machine that named one executable of the pair, naming the key it left out."""
    if unpaired := unpaired_tool(tools):
        raise ToolError(
            f"{', '.join(unpaired)} is not set, and ffmpeg and ffprobe have to come from one build.",
            hint=f"Set {' and '.join(NAMED)} together, or clear both to use the pinned build.",
        )


def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables for the tools this run is bound to."""
    return _resolve(bound_tools())


RESOLUTIONS_KEPT = 4
"""Truth: a process renders for one machine, and a handful of bound toolchains covers every test of it."""


@lru_cache(maxsize=RESOLUTIONS_KEPT)
def _resolve(tools: ToolsConfig) -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables, worked out once per set of tools a process is asked for.

    The keys win, and half a build is refused. The pinned build comes next,
    fetched when it is not on disk yet, so every machine renders with the same ffmpeg. A build on
    PATH is the fallback when there is no pinned build for this platform or the download cannot run.
    A download whose digest does not match is never used and never falls back, because that is the
    one failure that must stop a run.
    """
    _refuse_half_a_build(tools)
    if missing := missing_tools(tools):
        raise ToolError(
            f"{', '.join(missing)} names a file that is not there.",
            hint="Point the key at an executable, or clear it to use the pinned build.",
        )
    if named := _named(tools):
        return named
    if installed := ffmpeg_fetch.installed_pinned():
        return installed
    on_path = _path_pair()
    if ffmpeg_fetch.pinned_build() is None:
        if on_path:
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: DeckTalk pins no build for {ffmpeg_fetch.platform_key()} and none is on "
            f"PATH. Install ffmpeg, or set {' and '.join(NAMED)}."
        )
    try:
        return ffmpeg_fetch.fetch_ffmpeg()
    except OSError as exc:
        if on_path:
            log.warning("could not download the pinned ffmpeg (%s), so %s is used instead", exc, on_path[0])
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: the pinned build could not be downloaded ({exc}) and none is on PATH. "
            "Run `decktalk install` with network access, or install ffmpeg."
        ) from exc


def unnamed_paths() -> tuple[str, str] | None:
    """The pair a machine has without `[tools]`, which is what `doctor` falls back to.

    A key that names a file which is not there tells nothing about the other component, so the row
    for the component that is fine still reports the build it would really use.
    """
    return ffmpeg_fetch.installed_pinned() or _path_pair()


def installed_paths(tools: ToolsConfig | None = None) -> tuple[str, str] | None:
    """The (ffmpeg, ffprobe) pair that ffmpeg_paths() would return without downloading anything.

    None means only a fetch could provide them, or that `[tools]` names half a build. `decktalk
    doctor` reports on that instead of triggering it.
    """
    tools = tools or bound_tools()
    if unpaired_tool(tools):
        return None
    return _named(tools) or ffmpeg_fetch.installed_pinned() or _path_pair()


def ffmpeg() -> str:
    return ffmpeg_paths()[0]


def ffprobe() -> str:
    return ffmpeg_paths()[1]


def run(*args: str) -> None:
    """ffmpeg -hide_banner -loglevel error -y ARGS, which writes a file and reports nothing."""
    _checked([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], "ffmpeg")


def stderr(*args: str) -> str:
    """ffmpeg run whose useful output is on stderr (metadata=print, silencedetect, loudnorm).

    A filter reports on stderr and exits zero, so a non-zero code here is the tool failing rather
    than the film measuring badly, and the caller is told so instead of reading an empty report.
    """
    proc = _checked([ffmpeg(), "-hide_banner", "-nostats", *args], "ffmpeg")
    return proc.stderr.decode(errors="replace")


def raw(*args: str) -> bytes:
    """ffmpeg run whose useful output is the bytes on stdout, such as decoded samples."""
    return _checked([ffmpeg(), "-v", "error", *args], "ffmpeg").stdout


def probe_duration(path: Path | str) -> float:
    """The length the container reports, in seconds."""
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = _checked(cmd, f"ffprobe on {Path(path).name}", location=_at(path))
    text = proc.stdout.decode(errors="replace").strip()
    if not text:
        raise ToolError(
            f"ffprobe could not read the length of {Path(path).name}.",
            hint=proc.stderr.decode(errors="replace").strip()[-HINT_CHARS:] or None,
            location=_at(path),
        )
    return round(float(text), 3)


def has_audio(path: Path | str) -> bool:
    """Whether the file carries an audio stream at all.

    A failed probe is a tool failure and never an answer, because reading it as no audio would let a
    broken ffprobe publish a verdict about a film it never opened.
    """
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = _checked(cmd, f"ffprobe on {Path(path).name}", location=_at(path))
    return bool(proc.stdout.decode(errors="replace").strip())
