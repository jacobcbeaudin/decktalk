"""Write every docs sample `scripts/samples.toml` names from a real run of DeckTalk on a fresh starter.

    uv run scripts/build_samples.py --write    # run each row and write its block under its comment
    uv run scripts/build_samples.py --check    # exit 1 if a block no longer says what a run says

A page marks a sample with `{/* sample: <id>, ... */}` on the line above its fenced block, and the row of
that id says how the project was made, what ran on it and what the block shows: a file the run wrote,
or what its last command printed. Each run is `decktalk` itself in a fresh `decktalk init my-lesson`,
with no key and no voice id in its environment, an empty machine file and a take store of its own,
so a sample is what any machine sees.

A row's steps are commands and the edits between them. Rows that begin with the same steps share
the project those steps made: each later step runs in a copy of it, so the starter is built once
for every sample that reads a built starter.

A row that needs a voiced take reads its script with a fake `dtsp` voice on a loopback port, which
answers the DeckTalk speech protocol with a tone the length of the words it was sent. It needs no key
and spends nothing, and the take it makes is a voiced take like any other.

The runs need ffmpeg and Chromium, so this runs in the e2e row beside the tools it needs rather than
in generated. The encoder decides a take's bytes and the machine decides how long a stage takes, so
a row names the keys and the patterns whose values a run decides, and `--check` holds those in its
own block by their place rather than by their value, so the same run on another platform passes.

A row whose last command verifies the film names in `measured` the findings a slower machine can
add to that verdict, because a page is recorded in real time and a starved recording lands a reveal
late where the docs machine's did not. Their lines are left out of the block, and the findings exit
they bring is accepted only when every error the run names is one of them. What DeckTalk measures
and the limits it holds are unchanged: the block is what the docs machine printed.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import re
import shutil
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
from typing import Any

import generated
from decktalk.findings import Code, Severity
from decktalk.machine import MACHINE_FILE_VARIABLE, Machine, Toolchain
from decktalk.media import ffmpeg
from decktalk.settings import BY_ID
from decktalk.speech import DECLARED
from decktalk.speech.dtsp import SPEECH_PATH
from decktalk.toolchain.cache import standard_cache_dir

ROOT = generated.ROOT
ROWS = ROOT / "scripts" / "samples.toml"

PROJECT = "my-lesson"
"""The folder every sample's project is written to, which is the name the docs give the starter."""

COMMENT = re.compile(r"^\{/\* sample: (?P<id>[a-z0-9-]+)\b.*\*/\}$")
"""The comment above a block this generator writes, which names the row that writes it and says what ran."""

FENCE = "```"

LINE_WIDTH = 100
"""Calibration: the widest line a docs code block shows before it scrolls, at the docs site's body width."""

WORD_SECONDS = 1 / 3
"""Calibration: the fake voice reads three words a second, near a person reading aloud."""

TONE_HZ = 220
"""Calibration: the fake voice's tone, low enough that nobody mistakes the take for a placeholder."""

KEPT_VARIABLES = (BY_ID["tools.cache_dir"].environment,)
"""The only DeckTalk variables a run keeps from this process: where the fetched tools are."""

STREAMS = ("stdout", "stderr", "both")
"""What a row may show of its last command: one stream, or both in the order they were printed."""

FENCES = {".json": "json", ".jsonl": "jsonl", ".md": "markdown"}
"""The fence a file's block opens with, by the file's suffix."""

EMPTY = "{empty}"
"""What a row's `env` value names an empty folder by, such as a tools cache that holds nothing."""

FINDINGS_EXIT = 1
"""Truth: the exit of a command that ran and found something, which a step before the last may end with."""

CODE_WORD = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
"""A word shaped like a finding code, which output names in a line, a JSON field or an event."""


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
    stream: str | None = None
    fence: str | None = None
    head: int | None = None
    grep: str | None = None
    key: str | None = None
    fragment: bool = False
    only: tuple[str, ...] = ()
    keep: tuple[tuple[str, int], ...] = ()
    varies: tuple[str, ...] = ()
    masks: tuple[str, ...] = ()
    measured: tuple[str, ...] = ()

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
            stream=row.get("stream"),
            fence=row.get("fence"),
            head=row.get("head"),
            grep=row.get("grep"),
            key=row.get("key"),
            fragment=row.get("fragment", False),
            only=tuple(row.get("only", ())),
            keep=tuple(row.get("keep", {}).items()),
            varies=tuple(row.get("varies", ())),
            masks=tuple(row.get("masks", ())),
            measured=tuple(row.get("measured", ())),
        )
        unknown = [code for code in made.measured if code not in Code.__members__]
        if unknown:
            raise SystemExit(f"{ROWS.name}: the sample {name} names {', '.join(unknown)}, which no finding is.")
        if (made.file is None) == (made.stream is None) or (made.stream and made.stream not in STREAMS):
            raise SystemExit(f"{ROWS.name}: the sample {name} shows one file or one of {', '.join(STREAMS)}.")
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
    machine's, and its take store is a folder of its own rather than this user's.
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
        "TTY_COMPATIBLE": "0",
        # Each write reaches the pipe as it is made, so both streams read in the order they were printed.
        "PYTHONUNBUFFERED": "1",
        "COLUMNS": str(LINE_WIDTH),
    }


def decktalk(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environ: dict[str, str],
    stream: str | None,
    exits: set[int],
    measured: tuple[str, ...] = (),
) -> str:
    """One command and what it printed on `stream`, refused when its exit is not one of `exits`.

    A findings exit where 0 was wanted is accepted when every error the run names is `measured`.
    A sample of a run that failed some other way would mislead, so the refusal says what it printed.
    """
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
    slower = (
        done.returncode == FINDINGS_EXIT and 0 in exits and only_measured(done.stdout + (done.stderr or ""), measured)
    )
    if done.returncode not in exits and not slower:
        raise SystemExit(
            f"decktalk {' '.join(argv)} exited {done.returncode}, not {' or '.join(map(str, sorted(exits)))}:\n"
            f"{done.stdout}{done.stderr or ''}"
        )
    return done.stderr if stream == "stderr" else done.stdout


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
        key = (row.start, row.steps, stream)
        if key not in self.printed:
            folder = self.copy(self.state(row, row.steps[:-1]).folder)
            printed = self.command(row, last, folder, stream=stream, exits={row.exit}, measured=row.measured)
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
        measured: tuple[str, ...] = (),
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
            return decktalk(argv, cwd=cwd, environ=environ, stream=stream, exits=exits, measured=measured)


# ---- a measured verdict --------------------------------------------------------------------------


def errors_named(printed: str) -> set[str]:
    """Every error code `printed` names, in a finding line, a JSON result or an event."""
    named = {Code.__members__.get(word) for word in CODE_WORD.findall(printed)}
    return {code.name for code in named if code is not None and code.severity is Severity.ERROR}


def only_measured(printed: str, measured: tuple[str, ...]) -> bool:
    """Whether a findings exit is one a slower machine added: it names an error, and every one it names is measured."""
    named = errors_named(printed)
    return bool(named) and named <= set(measured)


def unmeasured(text: str, measured: tuple[str, ...]) -> str:
    """`text` without the lines of a measured finding, each with the fix line printed under it."""
    if not measured:
        return text
    naming = re.compile(rf"^\S+: (?:{'|'.join(map(re.escape, measured))}) ")
    out: list[str] = []
    dropping = False
    for line in text.splitlines():
        dropping = bool(naming.match(line)) or (dropping and line.startswith("  fix"))
        if not dropping:
            out.append(line)
    return "\n".join(out)


def unmeasured_value(value: object, measured: tuple[str, ...]) -> object:
    """A JSON result without its measured findings, with every other finding as it was."""
    if not measured or not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        return value
    kept = [found for found in value["findings"] if not (isinstance(found, dict) and found.get("code") in measured)]
    return {**value, "findings": kept}


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


def shown_text(printed: str, *, head: int | None, grep: str | None) -> str:
    """What a command printed, cut to the lines a row picks, with no trailing space and no blank first or last line."""
    lines = [line.rstrip() for line in printed.splitlines()]
    if grep is not None:
        lines = [line for line in lines if re.search(grep, line)]
    if head is not None:
        lines = lines[:head]
    return "\n".join(lines).strip("\n")


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
    else:
        fence = row.fence or "text"
        text = ran.printed
    if fence.split(" ")[0] == "json":
        value = cut(unmeasured_value(json.loads(text), row.measured), key=row.key, only=row.only, keep=row.keep)
        shown = fragment(row.key, value) if row.fragment and row.key else dumped(value)
    else:
        shown = shown_text(unmeasured(text, row.measured), head=row.head, grep=row.grep)
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
    """`text` with the value of every key in `keys`, and every match of `patterns`, written as `...`."""
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


def files() -> dict[Path, str]:
    """Every page a row names, with every block written from a real run."""
    every = rows()
    blocks: dict[Path, dict[str, tuple[str, str]]] = {}
    with tempfile.TemporaryDirectory(prefix="decktalk-samples-") as scratch:
        runs = Runs(Path(scratch))
        # Shorter runs first, so a longer run that begins with one finds its folder already made.
        for row in sorted(every, key=lambda row: len(row.steps)):
            comment = f"{{/* sample: {row.id}, {row.what} */}}"
            blocks.setdefault(row.page, {})[row.id] = (comment, block(row, runs.reach(row)))
    pages = {page: spliced(page.read_text(encoding="utf-8"), found, where=page) for page, found in blocks.items()}
    for page, found in blocks.items():
        named = {match["id"] for line in pages[page].splitlines() if (match := COMMENT.match(line.strip()))}
        missing = sorted(set(found) - named)
        if missing:
            raise SystemExit(f"{page.relative_to(ROOT)} has no comment for the samples {', '.join(missing)}.")
    return pages


def why_stale(path: Path, text: str) -> str | None:
    """`generated.differs`, with every value a run decides held in its own block by its place alone."""
    masks = {row.id: (row.varies, row.masks) for row in rows() if row.page == path}
    if not path.exists():
        return generated.differs(path, text)
    committed, written = (masked_page(page, masks) for page in (path.read_text(encoding="utf-8"), text))
    # The line named is the first that differs once every value a run decides is masked on both sides.
    return generated.moved(committed, written)


if __name__ == "__main__":
    sys.exit(generated.run(files, why_stale=why_stale))
