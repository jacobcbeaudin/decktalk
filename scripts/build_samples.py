"""Write every docs sample `scripts/samples.toml` names from a real run of DeckTalk on a fresh starter.

    uv run scripts/build_samples.py --write    # run each row and write its block under its comment
    uv run scripts/build_samples.py --check    # exit 1 if a block no longer says what a run says

A page marks a sample with `{/* sample: <id>, ... */}` on the line above its fenced block, and the row of
that id says how the project was made, what ran on it and which file the block shows. Each run is
`decktalk` itself in a fresh `decktalk init my-lesson`, with no key and no voice id in its
environment, an empty machine file and a take store of its own, so a sample is what any machine sees.

A row that needs a voiced take reads its script with a fake `dtsp` voice on a loopback port, which
answers the DeckTalk speech protocol with a tone the length of the words it was sent. It needs no key
and spends nothing, and the take it makes is a voiced take like any other.

The runs need ffmpeg, so this runs in the e2e row beside the tools it needs rather than in generated.
The encoder decides a take's bytes, so a row names the keys whose values it decides, and `--check`
holds those by key rather than by value, so the same run on another platform passes.
"""

from __future__ import annotations

import base64
import json
import os
import re
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
"""Calibration: the fake voice's tone, low enough that nobody mistakes the take for a click track."""

KEPT_VARIABLES = (BY_ID["tools.cache_dir"].environment,)
"""The only DeckTalk variables a run keeps from this process: where the fetched tools are."""


@dataclass(frozen=True)
class Row:
    """One sample: the run that makes it, and the file it shows."""

    id: str
    page: Path
    what: str
    edits: tuple[tuple[str, str, str], ...]
    voice: str | None
    run: tuple[tuple[str, ...], ...]
    file: str
    keep: tuple[tuple[str, int], ...]
    varies: tuple[str, ...]

    @property
    def project(self) -> tuple[object, ...]:
        """What makes the project this row reads, which rows that agree on it share."""
        return (self.edits, self.voice, self.run)


def rows() -> list[Row]:
    """Every row of `samples.toml`, in the order the file gives them."""
    table = tomllib.loads(ROWS.read_text(encoding="utf-8"))
    return [
        Row(
            id=name,
            page=ROOT / row["page"],
            what=row["what"],
            edits=tuple((file, find, replace) for file, find, replace in row.get("edits", ())),
            voice=row.get("voice"),
            run=tuple(tuple(argv) for argv in row["run"]),
            file=row["file"],
            keep=tuple(row.get("keep", {}).items()),
            varies=tuple(row.get("varies", ())),
        )
        for name, row in table.items()
    ]


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

    That leaves out the voice key, the voice id and any machine file, so the run is a fresh
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
    }


def decktalk(argv: tuple[str, ...], *, cwd: Path, environ: dict[str, str]) -> None:
    """One command, refused with what it said when it fails, since a sample of a failed run would mislead."""
    done = subprocess.run(
        [sys.executable, "-m", "decktalk", *argv],
        cwd=cwd,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=generated.COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"decktalk {' '.join(argv)} exited {done.returncode}:\n{done.stdout}{done.stderr}")


def made(row: Row, scratch: Path) -> Path:
    """The project `row` reads, written fresh, edited and run as the row says."""
    environ = environment(scratch)
    decktalk(("init", PROJECT, "--no-input", "--no-skills"), cwd=scratch, environ=environ)
    project = scratch / PROJECT
    for file, find, replace in row.edits:
        path = project / file
        text = path.read_text(encoding="utf-8")
        if find not in text:
            raise SystemExit(f"the sample {row.id} edits {file}, which no longer says {find!r}.")
        path.write_text(text.replace(find, replace, 1), encoding="utf-8")
    with fake_voice(environ, scratch) if row.voice == "dtsp" else nullcontext("") as base_url:
        if base_url:
            environ[BY_ID["dtsp.base_url"].environment] = base_url
        for argv in row.run:
            decktalk(argv, cwd=project, environ=environ)
    return project


# ---- a block -------------------------------------------------------------------------------------


def one_line(value: object) -> str | None:
    """`value` on one line, when it is an object of scalars, or None when it holds an object or a list."""
    if not isinstance(value, dict) or any(isinstance(item, dict | list) for item in value.values()):
        return None
    return "{ " + ", ".join(f"{json.dumps(key)}: {json.dumps(item)}" for key, item in value.items()) + " }"


def dumped(value: object, depth: int = 0) -> str:
    """`value` as JSON indented by two, with an object of scalars on one line wherever it fits the width."""
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


def block(row: Row, project: Path) -> str:
    """The fenced block `row` writes: its file, cut to the rows it keeps, under the file's own name."""
    name = row.file
    if "{digest}" in name:
        takes = json.loads((project / "build" / "narrate" / "takes.json").read_text(encoding="utf-8"))
        name = name.format(digest=takes["sections"][0]["digest"])
    data = json.loads((project / name).read_text(encoding="utf-8"))
    for key, count in row.keep:
        data[key] = data[key][:count]
    return f"{FENCE}json {name}\n{dumped(data)}\n{FENCE}\n"


def spliced(text: str, blocks: dict[str, tuple[str, str]], *, where: Path) -> str:
    """`text` with each sample comment and the fenced block under it written as its row says."""
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
        if at >= len(lines) or not lines[at].startswith(FENCE):
            raise SystemExit(f"{where.relative_to(ROOT)}: the sample {found['id']} has no fenced block under it.")
        end = next((i for i in range(at + 1, len(lines)) if lines[i].rstrip() == FENCE), None)
        if end is None:
            raise SystemExit(f"{where.relative_to(ROOT)}: the sample {found['id']} has a block that never closes.")
        out.append(written)
        at = end + 1
    return "".join(out)


def masked(text: str, keys: tuple[str, ...]) -> str:
    """`text` with the value of every key in `keys` written as `...`, so only the key is held."""
    for key in keys:
        text = re.sub(rf'("{re.escape(key)}": )("[^"]*"|[-0-9.eE]+|null)', r"\1...", text)
    return text


# ---- the generator -------------------------------------------------------------------------------


def files() -> dict[Path, str]:
    """Every page a row names, with every block written from a real run."""
    every = rows()
    runs: dict[tuple[object, ...], Path] = {}
    blocks: dict[Path, dict[str, tuple[str, str]]] = {}
    with tempfile.TemporaryDirectory(prefix="decktalk-samples-") as scratch:
        for row in every:
            if row.project not in runs:
                folder = Path(scratch) / str(len(runs))
                folder.mkdir()
                runs[row.project] = made(row, folder)
            comment = f"{{/* sample: {row.id}, {row.what} */}}"
            blocks.setdefault(row.page, {})[row.id] = (comment, block(row, runs[row.project]))
    pages = {page: spliced(page.read_text(encoding="utf-8"), found, where=page) for page, found in blocks.items()}
    for page, found in blocks.items():
        named = {match["id"] for line in pages[page].splitlines() if (match := COMMENT.match(line.strip()))}
        missing = sorted(set(found) - named)
        if missing:
            raise SystemExit(f"{page.relative_to(ROOT)} has no comment for the samples {', '.join(missing)}.")
    return pages


def why_stale(path: Path, text: str) -> str | None:
    """`generated.differs`, with every value the encoder decides held by its key alone."""
    keys = tuple(key for row in rows() if row.page == path for key in row.varies)
    if path.exists() and masked(path.read_text(encoding="utf-8"), keys) == masked(text, keys):
        return None
    return generated.differs(path, text)


if __name__ == "__main__":
    sys.exit(generated.run(files, why_stale=why_stale))
