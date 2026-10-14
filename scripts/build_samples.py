"""Write every docs sample `scripts/samples.toml` names from a real run of DeckTalk on a fresh starter.

    uv run scripts/build_samples.py --write    # run each row and write its block under its comment
    uv run scripts/build_samples.py --check    # exit 1 if a block no longer says what a run says

A page marks a sample with `{/* sample: <id>, ... */}` on the line above its fenced block, or with
`<!-- sample: <id>, ... -->` in a Markdown page such as the README, and the row of that id says how the
project was made, what ran on it and what the block shows: a file the run wrote, the names in a folder
it wrote, or what its last command printed. A comment that names no row is refused, because a block
that looks generated and is not goes stale with nothing to say so.

Each run is `decktalk` itself in a fresh `decktalk init my-lesson`, with no key and no voice id in its
environment, an empty machine file and a take store of its own, so a sample is what any machine sees.
The one machine key a sample shows the effect of, how many recorders `record` runs at once, is given
to every run rather than chosen from the CPUs of the machine that runs it.

A row's steps are commands and the edits between them. Rows that begin with the same steps share
the project those steps made: each later step runs in a copy of it, so the starter is built once
for every sample that reads a built starter.

A command that serves until it is stopped, such as `serve` or `build --watch`, is read for the lines a
row names and then stopped the way Ctrl-C stops it.

A row that needs a voiced take reads its script with a fake `dtsp` voice on a loopback port, which
answers the DeckTalk speech protocol with a tone the length of the words it was sent. It needs no key
and spends nothing, and the take it makes is a voiced take like any other.

The runs need ffmpeg and Chromium, so this runs in the e2e row beside the tools it needs rather than
in generated. The encoder decides a take's bytes, so a row names the keys and the patterns whose
values a run decides, and `--check` holds those in its own block by their place rather than by their
value. The machine decides how long a stage takes, so every block holds a stage's time that way
without a row naming it. Every run prints at one width in UTF-8, and `--check` reads both sides
without their line endings and trailing space, so the same run on Linux, macOS and Windows passes.
The recorder steps the section clock one frame at a time, so a slower machine records the same
frames and every verdict a block shows is the verdict any machine prints.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any

import generated
from decktalk.machine import MACHINE_FILE_VARIABLE, Machine, Toolchain
from decktalk.media import ffmpeg
from decktalk.pipeline import Stage
from decktalk.settings import BY_ID
from decktalk.speech import DECLARED
from decktalk.speech.dtsp import SPEECH_PATH
from decktalk.stages.record.pool import MOST_AT_ONCE
from decktalk.toolchain.cache import standard_cache_dir

ROOT = generated.ROOT
ROWS = ROOT / "scripts" / "samples.toml"

PROJECT = "my-lesson"
"""The folder every sample's project is written to, which is the name the docs give the starter."""

COMMENT = re.compile(r"^(?:\{/\*|<!--) sample: (?P<id>[^,]*?)(?:,.*?)? ?(?:\*/\}|-->)$")
"""The comment above a block this generator writes, which names the row that writes it and says what ran.

Any comment that opens `sample:` is one, whatever follows, so a comment whose first words are not a row's
id is read as naming that missing row and refused rather than passed over.
"""

FORMS = {".mdx": ("{/* ", " */}"), ".md": ("<!-- ", " -->")}
"""How a page of each kind writes a comment: MDX in braces, and Markdown as HTML, which renders nothing."""

FENCE = "```"

LINE_WIDTH = 100
"""Calibration: the widest line a docs code block shows before it scrolls, at the docs site's body width."""

WORD_SECONDS = 1 / 3
"""Calibration: the fake voice reads three words a second, near a person reading aloud."""

TONE_HZ = 220
"""Calibration: the fake voice's tone, low enough that nobody mistakes the take for a placeholder."""

KEPT_VARIABLES = (BY_ID["tools.cache_dir"].environment,)
"""The only DeckTalk variables a run keeps from this process: where the fetched tools are."""

SAMPLE_RECORDERS = MOST_AT_ONCE
"""Calibration: the `record.concurrency` every run records with, the most an automatic choice ever runs.

The recorders split a section into chunks, so a recording log's `pieces` and `steps` follow their
number, and left automatic that number follows the CPUs of the machine that runs this. Every machine
records with the number a machine of twelve or more CPUs chooses, so the docs show a long section
split the way a large machine splits it, and every machine writes the same sample.
"""

STREAMS = ("stdout", "stderr", "both")
"""What a row may show of its last command: one stream, or both in the order they were printed."""

FENCES = {".json": "json", ".jsonl": "jsonl", ".md": "markdown"}
"""The fence a file's block opens with, by the file's suffix."""

EMPTY = "{empty}"
"""What a row's `env` value names an empty folder by, such as a tools cache that holds nothing."""

HIDDEN = "..."
"""What a row's `hide` patterns are written as, which is what `--check` reads in their place on both sides."""

STOP_GRACE_SECONDS = 30
"""Calibration: how long a command that serves is given to close its origin after Ctrl-C before it is killed."""

FINDINGS_EXIT = 1
"""Truth: the exit of a command that ran and found something, which a step before the last may end with."""

STAGE_TIME = re.compile(rf"^(\s*(?:{'|'.join(stage.value.title() for stage in Stage)}) .*?   )\d+(?::\d\d){{1,2}}\b")
"""The time on a stage's line, which the machine decides and `--check` holds by its place in every block."""


@dataclass(frozen=True)
class Edit:
    """A find and replace on one project file, refused when the file no longer says what it finds."""

    file: str
    find: str
    replace: str


@dataclass(frozen=True)
class Delete:
    """One project file removed."""

    file: str


Step = tuple[str, ...] | Edit | Delete
"""A command, as its argv after `decktalk`, or a change to the project between commands."""


@dataclass(frozen=True)
class Row:
    """One sample: the steps that make it, and what of the run it shows."""

    id: str
    page: Path
    what: str
    steps: tuple[Step, ...]
    voice: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    init: bool = True
    folder: bool = False
    exit: int = 0
    file: str | None = None
    listing: str | None = None
    stream: str | None = None
    lines: int | None = None
    fence: str | None = None
    head: int | None = None
    grep: str | None = None
    hide: tuple[str, ...] = ()
    key: str | None = None
    fragment: bool = False
    only: tuple[str, ...] = ()
    keep: tuple[tuple[str, int], ...] = ()
    varies: tuple[str, ...] = ()
    masks: tuple[str, ...] = ()

    @property
    def start(self) -> tuple[object, ...]:
        """What every run of this row starts from, before its first step."""
        return (self.voice, self.env, self.init, self.folder)


def step(raw: object) -> Step:
    """One step as `samples.toml` writes it: an argv list, `{ edit = [file, find, replace] }` or `{ delete = file }`."""
    if isinstance(raw, list):
        return tuple(raw)
    if isinstance(raw, dict) and "edit" in raw:
        return Edit(*raw["edit"])
    if isinstance(raw, dict) and "delete" in raw:
        return Delete(raw["delete"])
    raise SystemExit(f"{ROWS.name} has a step that is neither a command, an edit nor a delete: {raw!r}.")


def rows() -> list[Row]:
    """Every row of `samples.toml`, in the order the file gives them."""
    table = tomllib.loads(ROWS.read_text(encoding="utf-8"))
    every = []
    for name, row in table.items():
        edits = tuple(Edit(*edit) for edit in row.get("edits", ()))
        made = Row(
            id=name,
            page=ROOT / row["page"],
            what=row["what"],
            steps=(*edits, *(step(raw) for raw in row["run"])),
            voice=row.get("voice"),
            env=tuple(row.get("env", {}).items()),
            init=row.get("init", True),
            folder=row.get("folder", False),
            exit=row.get("exit", 0),
            file=row.get("file"),
            listing=row.get("listing"),
            stream=row.get("stream"),
            lines=row.get("lines"),
            fence=row.get("fence"),
            head=row.get("head"),
            grep=row.get("grep"),
            hide=tuple(row.get("hide", ())),
            key=row.get("key"),
            fragment=row.get("fragment", False),
            only=tuple(row.get("only", ())),
            keep=tuple(row.get("keep", {}).items()),
            varies=tuple(row.get("varies", ())),
            masks=tuple(row.get("masks", ())),
        )
        if [made.file, made.listing, made.stream].count(None) != 2 or (made.stream and made.stream not in STREAMS):
            raise SystemExit(
                f"{ROWS.name}: the sample {name} shows one file, one folder's names or one of {', '.join(STREAMS)}."
            )
        if made.lines is not None and made.stream not in ("stdout", "stderr"):
            raise SystemExit(f"{ROWS.name}: the sample {name} reads lines of a command that serves from no one stream.")
        if made.stream is not None and not (made.steps and isinstance(made.steps[-1], tuple)):
            raise SystemExit(f"{ROWS.name}: the sample {name} shows what a command printed and ends on no command.")
        every.append(made)
    return every


# ---- the fake voice ------------------------------------------------------------------------------


def timed(pieces: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """A time for every word of `pieces` at the fake voice's pace, with each piece's pause after it, and the length."""
    words: list[dict[str, Any]] = []
    at = 0.0
    for piece in pieces:
        for word in piece["text"].split():
            words.append({"word": word, "start": round(at, 3), "end": round(at + WORD_SECONDS, 3)})
            at += WORD_SECONDS
        at += piece["pause"] or 0.0
    return words, at


def spoken(body: dict[str, Any]) -> dict[str, Any]:
    """The reply to one section: a tone as long as its words and pauses, and a time for every word."""
    words, seconds = timed(body["pieces"])
    with tempfile.TemporaryDirectory() as scratch:
        take = Path(scratch) / "take.mp3"
        tone = f"sine=f={TONE_HZ}:r=44100"
        ffmpeg.run("-f", "lavfi", "-i", tone, "-t", f"{seconds:.3f}", "-c:a", "libmp3lame", str(take))
        audio = base64.b64encode(take.read_bytes()).decode()
    return {"audio_base64": audio, "format": body["format"], "words": words}


class Voice(ThreadingHTTPServer):
    """A loopback server holding the toolchain its handlers encode with, which no thread inherits on its own."""

    def __init__(self, toolchain: Toolchain) -> None:
        super().__init__(("127.0.0.1", 0), Speaker)
        self.toolchain = toolchain


class Speaker(BaseHTTPRequestHandler):
    """The DeckTalk speech protocol's one request, answered by `spoken`."""

    server: Voice

    def do_POST(self) -> None:
        if self.path != SPEECH_PATH:
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.toolchain.bound():
            reply = json.dumps(spoken(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def fake_voice(environ: dict[str, str], scratch: Path) -> Iterator[str]:
    """A `dtsp` server on a loopback port, yielding its base URL, encoding with the ffmpeg a run would find."""
    here = Machine.of(
        environ=environ,
        machine_file=Path(environ[MACHINE_FILE_VARIABLE]),
        cwd=scratch,
        cache_dir=standard_cache_dir(environ, Path.home()),
    )
    server = Voice(here.toolchain)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ---- a run ---------------------------------------------------------------------------------------


def environment(scratch: Path) -> dict[str, str]:
    """What every run sees: this process's environment with no DeckTalk variable but the tools' cache.

    That leaves out the API key, the voice id and any machine file, so the run is a fresh
    machine's, and its take store is a folder of its own rather than this user's. It records with
    `SAMPLE_RECORDERS` recorders whatever this machine's CPUs would choose.
    """
    kept = {name: value for name, value in os.environ.items() if not name.startswith("DECKTALK_")}
    kept |= {name: os.environ[name] for name in KEPT_VARIABLES if name in os.environ}
    for name in ("VIRTUAL_ENV", *(declared.key_variable for declared in DECLARED.values() if declared.key_variable)):
        kept.pop(name, None)
    machine = scratch / "machine.toml"
    machine.write_text("", encoding="utf-8")
    return {
        **kept,
        MACHINE_FILE_VARIABLE: str(machine),
        BY_ID["narration.store_dir"].environment: str(scratch / "store"),
        BY_ID["record.concurrency"].environment: str(SAMPLE_RECORDERS),
        "TTY_COMPATIBLE": "0",
        # Each write reaches the pipe as it is made, so both streams read in the order they were printed.
        "PYTHONUNBUFFERED": "1",
        # A table is laid out at the width and in the characters the docs show, whatever console runs this:
        # rich reads the width from COLUMNS, and draws its boxes in ASCII on a stream that is not UTF-8.
        "COLUMNS": str(LINE_WIDTH),
        "PYTHONIOENCODING": "utf-8",
    }


def decktalk(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environ: dict[str, str],
    stream: str | None,
    exits: set[int],
    lines: int | None = None,
) -> str:
    """One command and what it printed on `stream`, refused when its exit is not one of `exits`.

    A sample of a run that exited some other way would mislead, so the refusal says what it printed.
    A command that serves until stopped is read for its first `lines` lines instead, and stopped.
    """
    if lines is not None:
        assert stream is not None
        return first_lines(
            [sys.executable, "-m", "decktalk", *argv], cwd=cwd, environ=environ, stream=stream, lines=lines
        )[0]
    done = subprocess.run(
        [sys.executable, "-m", "decktalk", *argv],
        cwd=cwd,
        env=environ,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if stream == "both" else subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=generated.COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if done.returncode not in exits:
        raise SystemExit(
            f"decktalk {' '.join(argv)} exited {done.returncode}, not {' or '.join(map(str, sorted(exits)))}:\n"
            f"{done.stdout}{done.stderr or ''}"
        )
    return done.stderr if stream == "stderr" else done.stdout


def first_lines(
    argv: list[str], *, cwd: Path, environ: dict[str, str], stream: str, lines: int
) -> tuple[str, subprocess.Popen[str]]:
    """The first `lines` lines a command that serves until stopped prints on `stream`, and the process, stopped.

    The command runs in a process group of its own, so the Ctrl-C that stops it reaches it and
    nothing else, and it is killed when it has not closed within `STOP_GRACE_SECONDS`.
    """
    piped = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, stream: subprocess.PIPE}
    windows = sys.platform == "win32"
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environ,
        text=True,
        encoding="utf-8",
        stdout=piped["stdout"],
        stderr=piped["stderr"],
        start_new_session=not windows,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0,
    )
    pipe = process.stdout if stream == "stdout" else process.stderr
    assert pipe is not None
    read: list[str] = []

    def take(source: IO[str]) -> None:
        read.extend(source.readline() for _ in range(lines))

    reader = threading.Thread(target=take, args=(pipe,), daemon=True)
    reader.start()
    reader.join(generated.COMMAND_TIMEOUT_SECONDS)
    stop(process)
    pipe.close()
    printed = "".join(read)
    if reader.is_alive() or printed.count("\n") < lines:
        raise SystemExit(f"{' '.join(argv)} printed {printed.count(chr(10))} lines, not {lines}:\n{printed}")
    return printed, process


def stop(process: subprocess.Popen[str]) -> None:
    """Stop a command that serves the way Ctrl-C does, and kill it when it does not close in time."""
    if sys.platform == "win32":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def apply(change: Edit | Delete, project: Path, *, row: str) -> None:
    """One change a row makes to its project, refused when the file no longer says what an edit finds."""
    path = project / change.file
    if isinstance(change, Delete):
        path.unlink()
        return
    text = path.read_text(encoding="utf-8")
    if change.find not in text:
        raise SystemExit(f"the sample {row} edits {change.file}, which no longer says {change.find!r}.")
    path.write_text(text.replace(change.find, change.replace, 1), encoding="utf-8")


@dataclass(frozen=True)
class Ran:
    """A folder after some steps, and what the last command of them printed."""

    folder: Path
    printed: str = ""


class Runs:
    """Every state the rows reach, so rows that begin with the same steps run them once.

    A state is a folder holding `my-lesson` after a row's first steps. The next step runs in a copy
    of it, so a step never changes a folder another row reads.
    """

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.states: dict[tuple[object, ...], Ran] = {}
        self.printed: dict[tuple[object, ...], Ran] = {}
        self.machines: dict[tuple[object, ...], Path] = {}
        self.folders = itertools.count()

    def machine(self, row: Row) -> Path:
        """The folder holding the machine file and the take store every copy that starts as `row` does reads.

        A build records where the take store is, so a copy that read another store would find its
        build out of date. Every copy of one start keeps the one machine.
        """
        if row.start not in self.machines:
            self.machines[row.start] = self.scratch / f"machine-{next(self.folders)}"
            self.machines[row.start].mkdir()
        return self.machines[row.start]

    def reach(self, row: Row) -> Ran:
        """The folder after every step of `row`, with what its last command printed on the row's stream."""
        last = row.steps[-1] if row.steps else None
        if not isinstance(last, tuple):
            return self.state(row, row.steps)
        stream = row.stream
        if stream is None and (row.start, row.steps) in self.states:
            # A row that shows a file needs no printed output, so any run of the same steps serves it.
            return self.states[(row.start, row.steps)]
        key = (row.start, row.steps, stream, row.lines)
        if key not in self.printed:
            folder = self.copy(self.state(row, row.steps[:-1]).folder)
            printed = self.command(row, last, folder, stream=stream, exits={row.exit}, lines=row.lines)
            self.printed[key] = Ran(folder, printed)
            self.states.setdefault((row.start, row.steps), self.printed[key])
        return self.printed[key]

    def state(self, row: Row, steps: tuple[Step, ...]) -> Ran:
        """The folder after `steps`, made from the longest run of them already made."""
        key = (row.start, steps)
        if key in self.states:
            return self.states[key]
        if not steps:
            folder = self.scratch / str(next(self.folders))
            folder.mkdir()
            if row.init:
                environ = environment(self.machine(row))
                decktalk(
                    ("init", PROJECT, "--no-input", "--no-skills"), cwd=folder, environ=environ, stream=None, exits={0}
                )
            made = Ran(folder)
        else:
            folder = self.copy(self.state(row, steps[:-1]).folder)
            last = steps[-1]
            if isinstance(last, tuple):
                self.command(row, last, folder, stream=None, exits={0, FINDINGS_EXIT})
            else:
                apply(last, folder / PROJECT, row=row.id)
            made = Ran(folder)
        self.states[key] = made
        return made

    def copy(self, folder: Path) -> Path:
        """A copy of a state, to run the next step in."""
        copied = self.scratch / str(next(self.folders))
        shutil.copytree(folder, copied, symlinks=True)
        return copied

    def command(
        self,
        row: Row,
        argv: tuple[str, ...],
        folder: Path,
        *,
        stream: str | None,
        exits: set[int],
        lines: int | None = None,
    ) -> str:
        """One command of `row` run in `folder`, read by the fake voice when the row names it."""
        machine = self.machine(row)
        environ = environment(machine)
        for name, value in row.env:
            empty = machine / "empty"
            empty.mkdir(exist_ok=True)
            environ[name] = value.replace(EMPTY, str(empty))
        cwd = folder if row.folder or not row.init else folder / PROJECT
        with fake_voice(environ, machine) if row.voice == "dtsp" else nullcontext("") as base_url:
            if base_url:
                environ[BY_ID["dtsp.base_url"].environment] = base_url
            return decktalk(argv, cwd=cwd, environ=environ, stream=stream, exits=exits, lines=lines)


# ---- a block -------------------------------------------------------------------------------------


def scalars(value: object) -> bool:
    """Whether `value` holds no object and no list, so it can be printed on one line."""
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return isinstance(value, dict | list) and not any(isinstance(item, dict | list) for item in items)


def one_line(value: object) -> str | None:
    """`value` on one line, when it is an object or a list of scalars, or None when it holds either."""
    if not scalars(value):
        return None
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item) for item in value) + "]"
    assert isinstance(value, dict)
    if not value:
        return "{}"
    return "{ " + ", ".join(f"{json.dumps(key)}: {json.dumps(item)}" for key, item in value.items()) + " }"


def dumped(value: object, depth: int = 0) -> str:
    """`value` as JSON indented by two, with an object or a list of scalars on one line wherever it fits the width."""
    pad, inner = "  " * depth, "  " * (depth + 1)
    line = one_line(value)
    if line is not None and len(pad) + len(line) <= LINE_WIDTH:
        return line
    if isinstance(value, dict) and value:
        items = [f"{inner}{json.dumps(key)}: {dumped(item, depth + 1)}" for key, item in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, list) and value:
        return "[\n" + ",\n".join(inner + dumped(item, depth + 1) for item in value) + f"\n{pad}]"
    return json.dumps(value)


def part_at(value: object, path: str) -> object:
    """The part of `value` a dotted path names, where a number is a list's index."""
    for part in path.split("."):
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise SystemExit(f"{ROWS.name} names {path}, and {part} is under a value that holds no parts.")
    return value


def cut(
    value: object, *, key: str | None = None, only: tuple[str, ...] = (), keep: tuple[tuple[str, int], ...] = ()
) -> object:
    """The part of a JSON value a row shows: one part, some of its keys, and lists cut to their first items."""
    if key is not None:
        value = part_at(value, key)
    if only:
        if not isinstance(value, dict):
            raise SystemExit(f"{ROWS.name} keeps {', '.join(only)} of a value that is not an object.")
        value = {name: value[name] for name in only}
    for path, count in keep:
        holder, _, name = path.rpartition(".")
        parent = part_at(value, holder) if holder else value
        if not isinstance(parent, dict) or not isinstance(parent.get(name), list):
            raise SystemExit(f"{ROWS.name} cuts {path}, which is not a list.")
        parent[name] = parent[name][:count]
    return value


def fragment(path: str, value: object) -> str:
    """`value` as the entry of its own key, the way a reader pastes it into the file it came from."""
    return f"{json.dumps(path.rpartition('.')[2])}: {dumped(value)}"


def shown_text(printed: str, *, head: int | None, grep: str | None, hide: tuple[str, ...] = ()) -> str:
    """What a command printed, cut to the lines a row picks, with no trailing space and no blank first or last line.

    Each match of `hide` is written as `HIDDEN`, so a block names no path or version of the machine
    that wrote it, and `--check` reads the same dots on both sides.
    """
    lines = [line.rstrip() for line in printed.splitlines()]
    if grep is not None:
        lines = [line for line in lines if re.search(grep, line)]
    if head is not None:
        lines = lines[:head]
    for pattern in hide:
        lines = [re.sub(pattern, HIDDEN, line) for line in lines]
    return "\n".join(lines).strip("\n")


def listed(folder: Path) -> str:
    """The names in `folder` as `ls` prints them into a pipe: one to a line, sorted, and none that opens with a dot."""
    return "\n".join(sorted(path.name for path in folder.iterdir() if not path.name.startswith(".")))


def block(row: Row, ran: Ran) -> str:
    """The fenced block `row` writes: its file or what its last command printed, cut to what it keeps."""
    project = ran.folder if row.folder or not row.init else ran.folder / PROJECT
    if row.file is not None:
        name = row.file
        if "{digest}" in name:
            takes = json.loads((project / "build" / "narrate" / "takes.json").read_text(encoding="utf-8"))
            name = name.format(digest=takes["sections"][0]["digest"])
        if "{run}" in name:
            # A copied state keeps its files' times, so the newest events file is the last command's.
            newest = max((project / "build" / "events").glob("*.jsonl"), key=lambda path: path.stat().st_mtime_ns)
            name = name.format(run=newest.stem)
        path = project / name
        fence = f"{row.fence or FENCES.get(path.suffix, 'text')} {name}"
        text = path.read_text(encoding="utf-8")
    elif row.listing is not None:
        return f"{FENCE}{row.fence or 'text'}\n{listed(project / row.listing)}\n{FENCE}\n"
    else:
        fence = row.fence or "text"
        text = ran.printed
    if fence.split(" ")[0] == "json":
        value = cut(json.loads(text), key=row.key, only=row.only, keep=row.keep)
        shown = fragment(row.key, value) if row.fragment and row.key else dumped(value)
    else:
        shown = shown_text(text, head=row.head, grep=row.grep, hide=row.hide)
    return f"{FENCE}{fence}\n{shown}\n{FENCE}\n"


def spliced(text: str, blocks: dict[str, tuple[str, str]], *, where: Path) -> str:
    """`text` with each sample comment and the block under it written as its row says, at the comment's indent."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    at = 0
    while at < len(lines):
        found = COMMENT.match(lines[at].strip())
        if not found or found["id"] not in blocks:
            out.append(lines[at])
            at += 1
            continue
        comment, written = blocks[found["id"]]
        indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
        out.append(f"{indent}{comment}\n")
        at += 1
        if at >= len(lines) or not lines[at].strip().startswith(FENCE):
            raise SystemExit(f"{where.relative_to(ROOT)}: the sample {found['id']} has no fenced block under it.")
        end = next((i for i in range(at + 1, len(lines)) if lines[i].strip() == FENCE), None)
        if end is None:
            raise SystemExit(f"{where.relative_to(ROOT)}: the sample {found['id']} has a block that never closes.")
        out.extend(f"{indent}{line}" if line.strip() else line for line in written.splitlines(keepends=True))
        at = end + 1
    return "".join(out)


def masked(text: str, keys: tuple[str, ...], patterns: tuple[str, ...] = ()) -> str:
    """`text` with the value of every key in `keys`, each match of `patterns` and each stage's time written as `...`."""
    text = STAGE_TIME.sub(r"\g<1>...", text)
    for key in keys:
        text = re.sub(rf'("{re.escape(key)}": ?)("[^"]*"|[-0-9.eE]+|null|true|false)', r"\1...", text)
    for pattern in patterns:
        text = re.sub(pattern, "...", text)
    return text


def masked_page(text: str, masks: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> str:
    """`text` with each sample's block masked by its own row's keys and patterns, and every other line as it is."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    row: str | None = None
    inside = False
    for line in lines:
        found = COMMENT.match(line.strip())
        if found:
            row, inside = found["id"], False
        elif row is not None and line.strip().startswith(FENCE) and inside:
            row, inside = None, False
        elif row is not None and row in masks:
            # The opening fence is masked with the block, since it names a file the run named.
            inside = inside or line.strip().startswith(FENCE)
            line = masked(line, *masks[row])
        elif row is not None:
            inside = inside or line.strip().startswith(FENCE)
        out.append(line)
    return "".join(out)


# ---- the generator -------------------------------------------------------------------------------


def comment(page: Path, row: str, what: str) -> str:
    """The comment above row `row`'s block, in the form `page` writes comments in."""
    opens, closes = FORMS[page.suffix]
    return f"{opens}sample: {row}, {what}{closes}"


def pages() -> list[Path]:
    """Every page a sample may sit on: the README and every docs page."""
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.mdx"))]


def unbacked(texts: dict[Path, str], *, ids: set[str]) -> list[str]:
    """Each sample comment in `texts` that names no row of `ids`, as the page, the line and the id it names."""
    return [
        f"{page.relative_to(ROOT)}:{number} names {found['id']!r}"
        for page, text in texts.items()
        for number, line in enumerate(text.splitlines(), start=1)
        if (found := COMMENT.match(line.strip())) and found["id"] not in ids
    ]


def files() -> dict[Path, str]:
    """Every page a row names, with every block written from a real run.

    A comment on any page that names no row is refused before anything runs.
    """
    every = rows()
    missing = unbacked({page: page.read_text(encoding="utf-8") for page in pages()}, ids={row.id for row in every})
    if missing:
        raise SystemExit(f"These sample comments name no row of {ROWS.name}:\n" + "\n".join(missing))
    blocks: dict[Path, dict[str, tuple[str, str]]] = {}
    with tempfile.TemporaryDirectory(prefix="decktalk-samples-") as scratch:
        runs = Runs(Path(scratch))
        # Shorter runs first, so a longer run that begins with one finds its folder already made.
        for row in sorted(every, key=lambda row: len(row.steps)):
            blocks.setdefault(row.page, {})[row.id] = (comment(row.page, row.id, row.what), block(row, runs.reach(row)))
    written = {page: spliced(page.read_text(encoding="utf-8"), found, where=page) for page, found in blocks.items()}
    for page, found in blocks.items():
        named = {match["id"] for line in written[page].splitlines() if (match := COMMENT.match(line.strip()))}
        absent = sorted(set(found) - named)
        if absent:
            raise SystemExit(f"{page.relative_to(ROOT)} has no comment for the samples {', '.join(absent)}.")
    return written


def lines_of(text: str) -> list[str]:
    """`text` as lines with no line ending, no trailing space and no blank line after the last, on every platform."""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def why_differs(committed: str, written: str, masks: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> str | None:
    """The first line of `committed` that `written` changes once every value a run decides is masked, or None.

    The reason quotes the line on both sides as it is, before the masks, so a failed check says what
    the page holds and what this run wrote rather than only where they part.
    """
    pages = lines_of(committed), lines_of(written)
    held, wrote = (lines_of(masked_page("\n".join(page), masks)) for page in pages)
    if held == wrote:
        return None
    parted = (at for at, (old, new) in enumerate(zip(held, wrote, strict=False)) if old != new)
    line = next(parted, min(len(held), len(wrote)))
    page, run = (repr(lines[line]) if line < len(lines) else "the end of the page" for lines in pages)
    return f"line {line + 1} is {page} where this run writes {run}"


def why_stale(path: Path, text: str) -> str | None:
    """`generated.differs`, with every value a run decides held in its own block by its place alone."""
    masks = {row.id: (row.varies, row.masks) for row in rows() if row.page == path}
    if not path.exists():
        return generated.differs(path, text)
    return why_differs(path.read_text(encoding="utf-8"), text, masks)


if __name__ == "__main__":
    sys.exit(generated.run(files, why_stale=why_stale))
