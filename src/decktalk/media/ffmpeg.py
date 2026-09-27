"""Finding ffmpeg and ffprobe for this machine, running them, and probing what they read.

`[tools] ffmpeg` and `ffprobe` win, the pinned build that `toolchain/ffmpeg_fetch.py` downloads
comes next, and a build on PATH is the fallback, so every machine renders with the same ffmpeg
unless it is told otherwise. Those two keys are a pair: a machine that names one half and not the
other is refused rather than quietly rendered with a build it did not ask for.

The keys reach this module through `using_tools`, which the machine opens for a run, because `run`
and `stderr` are called from inside a filter chain and threading a settings object through every one
of them would put a project in the middle of an audio filter. The pair those keys name is worked out
once per binding and dies with it, so nothing a process resolved for one machine is ever handed to
the next, and a machine that already knows its pair binds it and resolves nothing at all.

Every call goes through `_checked`, so a return code other than zero raises `ToolError` carrying
the tail of what the tool said. A measurement that read a failure as silence, as blackness or as a
missing audio stream would turn a broken tool into a verdict about the film, which is the one
mistake this module exists to prevent. The same function is where a call is stopped: it polls the
run's cancel token while the tool works and stops a call that outlives `[tools] timeout_seconds`, so
one stuck encode can hold a worker for no longer than the machine allows.

`audio.py` and `frames.py` build on the five calls here: `run`, `stderr`, `raw`, `probe_duration`
and `has_audio`.

A file a project supplies is untrusted input. A clip or a music bed is a container that can name
other files and other hosts, as an HLS playlist or a concat list does, and ffmpeg follows those names
by default. So every input a caller opens goes through `source`, which allows the file protocol alone
and a closed set of demuxers that read one file and name nothing else.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import IO

from ..errors import Cancel, ToolError
from ..findings import Location
from ..settings import ToolsConfig
from ..toolchain import ffmpeg_fetch
from ..toolchain.cache import caching_in

log = logging.getLogger(__name__)

NAMED = ("tools.ffmpeg", "tools.ffprobe")
"""The two keys that name a build of the machine's own, which are set together or not at all."""


class Bound:
    """What one run renders with: the keys that name its toolchain, the pair they resolve to, and its cancel token.

    The pair is worked out the first time a call needs it and kept for as long as the binding is
    open, so a run resolves once and a second run, bound afresh, resolves for itself. A caller that
    already holds the pair, as a machine does once its toolchain is on disk, hands it over and
    nothing is resolved. The lock is there because a run records its sections on several threads,
    and each of them may be the first to ask.
    """

    def __init__(
        self, tools: ToolsConfig, paths: tuple[Path, Path] | None = None, cancel: Cancel | None = None
    ) -> None:
        self.tools = tools
        self.cancel = cancel
        self._paths = (str(paths[0]), str(paths[1])) if paths is not None else None
        self._lock = threading.Lock()

    def paths(self) -> tuple[str, str]:
        """(ffmpeg, ffprobe) for this binding, resolved on the first question and kept after it."""
        with self._lock:
            if self._paths is None:
                self._paths = _resolve(self.tools)
            return self._paths


TOOLS: ContextVar[Bound | None] = ContextVar("decktalk_tools", default=None)
"""What this run renders with, which `using_tools` sets and every call below reads."""


def bound_tools() -> ToolsConfig:
    """The tools in force, which is what a run was bound to or a machine that named none of its own."""
    bound = TOOLS.get()
    return bound.tools if bound is not None else ToolsConfig()


@contextmanager
def using_tools(
    tools: ToolsConfig, *, paths: tuple[Path, Path] | None = None, cancel: Cancel | None = None
) -> Iterator[None]:
    """Render with the executables and the cache directory `[tools]` names, while this is open.

    One call binds a run to a machine's own toolchain, so nothing between the machine and an audio
    filter has to carry a settings object to say which ffmpeg this is. `paths` is the pair the
    machine already resolved, when it has one, which spares the run a second resolution by a second
    rule. `cancel` is the run's token, which every call polls while its tool works.
    """
    token = TOOLS.set(Bound(tools, paths, cancel))
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


POLL_SECONDS = 0.1
"""Calibration: how often a running call looks at the cancel token and the clock, which a person never waits on."""

READ_BYTES = 1 << 16
"""Truth: one pipe's worth of output at a time, which keeps a decoder writing while it is read."""

Sink = Callable[[bytes], None]
"""Where a call's stdout goes as it arrives, for a caller that reads a stream rather than one result."""


def _spawn(cmd: list[str]) -> subprocess.Popen[bytes]:
    """Start one tool with both outputs piped and no input, so it can neither wait on a terminal nor block on one."""
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class _Reader(threading.Thread):
    """One output of a running tool, read to its end on a thread of its own so neither pipe can fill and stall it.

    A sink that raises stops the reading, and the error is kept for the caller to raise, because an
    exception on this thread would otherwise be lost and the call would read as finished.
    """

    def __init__(self, stream: IO[bytes] | None, sink: Sink) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.sink = sink
        self.failed: BaseException | None = None

    def run(self) -> None:
        if self.stream is None:
            return
        try:
            while chunk := self.stream.read(READ_BYTES):
                self.sink(chunk)
        except BaseException as exc:  # noqa: BLE001  (kept and raised by the caller, on the caller's thread)
            self.failed = exc


def _stop(proc: subprocess.Popen[bytes], readers: Iterable[_Reader]) -> None:
    """Kill a call that has to stop, and wait for it and its readers, so nothing of it outlives the call."""
    proc.kill()
    proc.wait()
    for reader in readers:
        reader.join()


def _watch(
    proc: subprocess.Popen[bytes],
    readers: tuple[_Reader, ...],
    what: str,
    cancel: Cancel | None,
    limit: float,
    location: Location | None,
) -> None:
    """Wait for a call to end, and stop it when the run is cancelled, the clock runs out or a reader fails."""
    deadline = time.monotonic() + limit
    while True:
        try:
            proc.wait(timeout=POLL_SECONDS)
            return
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                _stop(proc, readers)
                cancel.check()
            if failed := next((reader.failed for reader in readers if reader.failed), None):
                _stop(proc, readers)
                raise failed from None
            if time.monotonic() > deadline:
                _stop(proc, readers)
                raise ToolError(
                    f"{what} ran for longer than the {limit:g} seconds that tools.timeout_seconds allows, "
                    "so it was stopped.",
                    hint="Raise tools.timeout_seconds for a film this long, or look for what held the tool up.",
                    location=location,
                ) from None


def _checked(
    cmd: list[str], what: str, *, location: Location | None = None, into: Sink | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run one tool call and hand back what it wrote, or raise `ToolError` carrying the tail of its complaint.

    Every invocation in this package comes through here, so no caller can read a failed run as a
    measurement. The output is captured as bytes, because a decoder writes samples to stdout and a
    filter writes its report to stderr in the same call shape. `into` takes stdout as it arrives
    instead, for a caller that keeps a few frames of a long decode rather than all of them.

    The call is watched while it runs. A cancelled run kills it and raises `Cancelled`, and a call
    that outlives `[tools] timeout_seconds` is killed and refused as a `ToolError`, so neither a
    stopped run nor a stuck encoder keeps a worker.
    """
    bound = TOOLS.get()
    cancel = bound.cancel if bound is not None else None
    limit = bound_tools().timeout_seconds
    out: list[bytes] = []
    err: list[bytes] = []
    with _spawn(cmd) as proc:
        readers = (_Reader(proc.stdout, into or out.append), _Reader(proc.stderr, err.append))
        for reader in readers:
            reader.start()
        _watch(proc, readers, what, cancel, limit, location)
        for reader in readers:
            reader.join()
    if failed := next((reader.failed for reader in readers if reader.failed), None):
        raise failed
    stderr = b"".join(err)
    if proc.returncode != 0:
        raise ToolError(f"{what} failed: {_tail(stderr)}", location=location)
    return subprocess.CompletedProcess(cmd, proc.returncode, b"".join(out), stderr)


SOURCE_PROTOCOLS = "file"
"""The one protocol an opened input may use, so no file can make ffmpeg reach a host, a pipe or a subfile."""

SOURCE_FORMATS = (
    "mov",
    "matroska",
    "avi",
    "mp3",
    "wav",
    "ogg",
    "flac",
    "aac",
    "png_pipe",
    "jpeg_pipe",
    "webp_pipe",
)
"""The demuxers an opened input may be read by, each of which reads one file and follows no name inside it.

`mov` also answers for mp4 and m4a, and `matroska` for webm, because ffmpeg matches a demuxer by any
of its names. The playlist and list demuxers, `hls`, `dash`, `concat` and `image2` among them, are
absent on purpose, because each of them opens files or hosts that the project never named.
"""


def source(path: Path | str) -> list[str]:
    """The arguments that open one input, allowing the file protocol and the demuxers in `SOURCE_FORMATS` alone.

    Every caller that hands ffmpeg or ffprobe a file uses this in place of a bare `-i`, and the
    options it returns apply to that one input, so a caller may still put its own input options,
    such as a seek, ahead of it.
    """
    return [
        "-protocol_whitelist",
        SOURCE_PROTOCOLS,
        "-format_whitelist",
        ",".join(SOURCE_FORMATS),
        "-i",
        str(path),
    ]


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
    """(ffmpeg, ffprobe) executables for the tools this run is bound to.

    A call outside any run resolves afresh every time, because there is no run for an answer to
    belong to, and keeping one for the process is the leak the binding exists to prevent.
    """
    bound = TOOLS.get()
    return bound.paths() if bound is not None else _resolve(ToolsConfig())


def _resolve(tools: ToolsConfig) -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables for one set of tools, which a binding asks for once.

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


def stream(*args: str, into: Sink) -> None:
    """ffmpeg run whose stdout is handed to `into` as it arrives, for a decode too long to hold whole."""
    _checked([ffmpeg(), "-v", "error", *args], "ffmpeg", into=into)


def concat_line(path: Path | str) -> str:
    """One line of an ffmpeg concat list, quoted so that any path a person can write survives it.

    The demuxer reads a single-quoted path, so an apostrophe inside one ends the quoting and the rest
    of the name becomes arguments. It is written as `'\''`, which closes the quote, escapes one
    apostrophe and opens the quote again, and that is the one form both of ffmpeg's readings accept.
    A build under `jacob's films/` died at the concatenation step without it.
    """
    quoted = str(path).replace("'", "'\\''")
    return f"file '{quoted}'\n"


def concat_list(paths: Iterable[Path | str]) -> str:
    """The whole of a concat list, which is the one place a path is written for the demuxer to read."""
    return "".join(concat_line(path) for path in paths)


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
        *source(path),
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
        *source(path),
    ]
    proc = _checked(cmd, f"ffprobe on {Path(path).name}", location=_at(path))
    return bool(proc.stdout.decode(errors="replace").strip())
