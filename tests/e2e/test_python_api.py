"""Every Python example on the Python API reference runs as it is written, against the real package.

    uv run pytest -m e2e tests/e2e/test_python_api.py

A reader copies these blocks into a script, so a field the page names that a result has not got is an
`AttributeError` in the reader's hands. Each block is read off `docs/reference/python-api.mdx` and run
in page order, in a fresh namespace, on a starter project written by `decktalk.init` in a directory
of its own. A block that does not compile is a signature, and a test holds every such block to that
shape, so an example cannot hide from the run by being mistyped.

Nothing here needs a key, a network or a paid call. The shipped ElevenLabs adapter is stood in for by
a voice that answers each request with a tone as long as its words and a word every half second, so
a block that spends buys from it. The machine is the one the environment makes, with its file, its
take store and every DeckTalk variable of the person running the suite set aside, and its tool cache
kept, because that is where the Chromium and the ffmpeg a build reaches for live.

Four kinds of value a block leaves to its host are given here, in `HOST`. The paths a service block
names, such as `/jobs/1234`, are moved under the test's directory, and the tool cache it names is the
machine's own. `Origin.wait` closes the origin before it waits, which is a person stopping a preview.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import decktalk
from decktalk.media import ffmpeg
from decktalk.project import Origin
from decktalk.results import Word
from decktalk.settings import BY_ID, MACHINE_FILE_VARIABLE
from decktalk.speech import Speech, SpeechRequest
from support.fakes import FAKE_VOICE_NAME, stand_in
from support.paths import REPO
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(BASE_BUDGET_SECONDS + FIRST_FETCH_SECONDS))]

PAGE = REPO / "docs" / "reference" / "python-api.mdx"

PROJECT = "my-lesson"
"""The directory most blocks open, which is the name the page gives the reader's project."""

PROJECTS = (PROJECT, "lessons/averages", "demos/tour")
"""Every project a block opens by its path, each written by `decktalk.init` before the first block runs."""

WORD_SECONDS = 0.5
"""How far apart the stand-in voice puts its words, which is the pace the page's own `Silent` voice keeps."""

WORD_LENGTH_SECONDS = 0.4
"""How long each of the stand-in voice's words lasts, which leaves a gap before the next one."""

TONE_HZ = 220
"""The pitch of the tone the stand-in voice answers with, which is sound rather than silence."""

UNNAMED = 'id = ""'
"""How the starter leaves its narrator's voice id, for its author to fill."""

VOICE_ID = "voice-under-test"
"""The voice id every project here commits, which the stand-in voice answers to."""

SANDBOX_SWITCH = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
"""The Linux switch that refuses Chromium the sandbox an untrusted page needs, which ci.yml lifts for one row."""

SIGNATURE = re.compile(r"^[\w.]+\((?:[^()]|\([^()]*\))*\) -> [\w\[\], |]+$")
"""One call signature as the page writes it: a dotted name, its parameters and the type it returns."""


@dataclass(frozen=True)
class Example:
    """One fenced Python block of the page, with the line its fence opens on."""

    line: int
    source: str


def examples() -> list[Example]:
    """Every fenced Python block on the page, in page order."""
    found: list[Example] = []
    lines = PAGE.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() == "```python":
            end = lines.index("```", index + 1)
            found.append(Example(line=index + 1, source="\n".join(lines[index + 1 : end]) + "\n"))
            index = end
        index += 1
    return found


def compiles(example: Example) -> bool:
    try:
        compile(example.source, f"{PAGE.name}:{example.line}", "exec")
    except SyntaxError:
        return False
    return True


RUNNABLE = [example for example in examples() if compiles(example)]
SIGNATURES = [example for example in examples() if not compiles(example)]


def signature_lines(source: str) -> list[str]:
    """Each signature a block holds, with the lines a long one is wrapped over joined back into one."""
    joined: list[str] = []
    for line in source.splitlines():
        if joined and line.startswith(" "):
            joined[-1] = f"{joined[-1]} {line.strip()}"
        elif line.strip():
            joined.append(line.strip())
    return joined


@dataclass
class Ledger:
    """The host's own ledger the page's metering block writes to, which keeps every row it was handed."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def record(self, **row: Any) -> None:
        self.rows.append(row)


HOST: dict[str, Any] = {"key": "a-key-the-stand-in-never-sends", "ceiling": 1.0, "job_id": "job-1"}
"""The values a block names and leaves to its host: the voice job's key, its ceiling and the job's id."""


@dataclass
class ToneVoice:
    """A voice that answers every request with a tone as long as its words, and a word every half second."""

    folder: Path
    name: str = FAKE_VOICE_NAME

    def speak(self, request: SpeechRequest) -> Speech:
        spoken = [word for piece in request.pieces for word in piece.text.split()]
        words = tuple(
            Word(word=word, start=index * WORD_SECONDS, end=index * WORD_SECONDS + WORD_LENGTH_SECONDS)
            for index, word in enumerate(spoken)
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        take = self.folder / f"{len(list(self.folder.iterdir()))}.mp3"
        seconds = len(spoken) * WORD_SECONDS
        ffmpeg.run(
            "-f", "lavfi", "-i", f"sine=f={TONE_HZ}:d={seconds}", "-c:a", "libmp3lame", "-b:a", "64k", str(take),
        )  # fmt: skip
        return Speech(take.read_bytes(), words)


@dataclass
class Page:
    """The directory the blocks run in, and the host's ledger the metering block writes to."""

    root: Path
    cache: Path
    ledger: Ledger = field(default_factory=Ledger)

    def relocated(self, source: str) -> str:
        """The block with every path a host names moved into this directory, and the tool cache kept."""
        host = self.root / "host"
        return (
            source.replace('"/jobs/1234"', f'"{(self.root / PROJECT).as_posix()}"')
            .replace('"/opt/decktalk-cache"', f'"{self.cache.as_posix()}"')
            .replace('"/etc/decktalk/', f'"{host.as_posix()}/')
        )


def closing_wait(origin: Origin) -> None:
    """`Origin.wait` as a person who stops the preview at once meets it: closed, then waited on."""
    origin.close()
    WAIT(origin)


WAIT = Origin.wait


@pytest.fixture(scope="module")
def page(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Page]:
    """A starter project in a directory of its own, on this machine with only its tool cache kept."""
    root = tmp_path_factory.mktemp("python-api")
    kept = BY_ID["tools.cache_dir"].environment
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith(("DECKTALK_", "ELEVENLABS_")) and name != kept:
                patch.delenv(name)
        patch.setenv(MACHINE_FILE_VARIABLE, str(root / "machine.toml"))
        patch.setenv(BY_ID["narration.store_dir"].environment, str(root / "store"))
        patch.chdir(root)
        voices = stand_in(patch)
        voices[FAKE_VOICE_NAME] = lambda _context: ToneVoice(root / "tones")
        patch.setattr(Origin, "wait", closing_wait)
        for project in PROJECTS:
            decktalk.init(project)
            named(root / project)
        (root / "host").mkdir()
        silence = root / "silence.mp3"
        ffmpeg.run("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:a", "libmp3lame", str(silence))
        yield Page(root=root, cache=decktalk.Machine.from_environment().cache_dir)


def named(root: Path) -> None:
    """Commit the narrator's voice id to the project, as the page's reader has, so every job reads the same voice."""
    toml = root / "decktalk.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count(UNNAMED) == 1, f"the starter no longer leaves its voice id empty as {UNNAMED!r}"
    toml.write_text(text.replace(UNNAMED, f'id = "{VOICE_ID}"'), encoding="utf-8")


def buys_again(page: Page) -> None:
    """Set every take aside, so a block that spends has something to buy."""
    shutil.rmtree(page.root / PROJECT / "takes", ignore_errors=True)
    shutil.rmtree(page.root / "store", ignore_errors=True)


def test_every_block_that_does_not_run_is_a_signature() -> None:
    assert RUNNABLE, f"{PAGE.name} has no Python example that runs, so the test below says nothing"
    for example in SIGNATURES:
        for line in signature_lines(example.source):
            assert SIGNATURE.match(line), f"{PAGE.name}:{example.line} neither runs nor reads as a signature: {line}"


@pytest.mark.parametrize("example", RUNNABLE, ids=lambda example: f"line-{example.line}")
def test_the_example_runs_as_written(example: Example, page: Page) -> None:
    if "max_cost=0.01" in example.source or "def meter" in example.source:
        buys_again(page)
    if "page_policy=untrusted" in example.source and "project.record()" in example.source:
        if SANDBOX_SWITCH.is_file() and SANDBOX_SWITCH.read_text(encoding="utf-8").strip() == "1":
            pytest.skip("this Linux refuses Chromium the sandbox an untrusted page needs, as ci.yml says")
    namespace: dict[str, Any] = {"__name__": "__example__", "ledger": page.ledger, **HOST}
    try:
        exec(compile(page.relocated(example.source), f"{PAGE.name}:{example.line}", "exec"), namespace)
    finally:
        for thread in threading.enumerate():
            if isinstance(thread, threading.Timer):
                thread.cancel()
    if "def meter" in example.source:
        assert page.ledger.rows, "the metering block bought takes and recorded none of them"
