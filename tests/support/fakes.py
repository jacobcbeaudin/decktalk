"""The tools a stage reaches for, faked at the seam the stage imports.

A stage test runs the real stage. What it must not run is ffmpeg, Chromium and a provider that bills,
so ffmpeg is replaced at the one module attribute the stage reads and a fake voice stands in behind
the shipped adapter's `for_context`, each fixture handing back the record of what the stage asked
for, and `FakePage` is the page a browser seam hands a stage.
`FakeChromium` stands one level lower, as the Playwright a launch is handed.
Faking the seam rather than the stage is what keeps these tests about the stage: an argument list, a
page call or a speech request that changes shape shows up here rather than passing unread.

The fixtures that install a fake are in `tests/decktalk/conftest.py`, and a test imports a class from
here to type the fixture it asked for or to build one of its own.
"""

from __future__ import annotations

import io
import os
import wave
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Browser, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import ProviderError
from decktalk.media import pages
from decktalk.media.browser import TRUSTED, Chromium, PagePolicy
from decktalk.results import Word
from decktalk.speech import (
    ALIGNERS,
    PROVIDERS,
    Aligner,
    AlignerContext,
    AlignerIdentity,
    AlignmentRequest,
    Speech,
    SpeechFactory,
    SpeechRequest,
)
from decktalk.speech.dtsp import Dtsp
from decktalk.speech.elevenlabs import ElevenLabs
from decktalk.speech.sound import SOUNDS, SoundFactory
from decktalk.speech.sound.elevenlabs import ElevenLabsSound
from decktalk.tomlmap import variable

BROWSERS_VARIABLE = "PLAYWRIGHT_BROWSERS_PATH"
"""The variable Playwright's driver reads for where its browsers live, spelled here as Playwright documents it."""

FAKE_VOICE_NAME = "elevenlabs"
"""The provider a project under test reads its voice with, which `fake_voice` stands in for.

A fake stands in behind the shipped name, so the project reads the shipped voice's own table, its
default model and its stated rate. The machine is handed the shipped factory under that name, which
is the only factory a name DeckTalk ships may carry, and the fake answers when that factory builds
its adapter, for the length of the test alone.
"""

FREE_VOICE_NAME = "dtsp"
"""The voice DeckTalk ships that bills nothing, which a project under test names to be read by a free voice."""

NARRATOR_ID = variable("voices.narrator.id")
"""The variable that names the voice id of `narrator`, the default voice every project under test is read in."""

ADAPTERS: dict[str, type[ElevenLabs | Dtsp]] = {"elevenlabs": ElevenLabs, "dtsp": Dtsp}
"""The adapter class each shipped voice factory builds through its `for_context`, which is where a fake stands in."""

SOUND_ADAPTERS: dict[str, type[ElevenLabsSound]] = {"elevenlabs": ElevenLabsSound}
"""The adapter class each shipped sound factory builds through its `for_context`, which is where a fake stands in."""


class StandIns[F](dict[str, F]):
    """The fakes a test stands in for the providers DeckTalk ships, by name, beside any provider of a host's own.

    A name DeckTalk ships holds the fake its adapter answers with, and registering the shipped factory
    there reaches the real adapter. Any other name is a host's own provider, handed to the machine as it is.
    """

    def __init__(self, shipped: Mapping[str, F], fakes: Mapping[str, F]) -> None:
        super().__init__(fakes)
        self.shipped = shipped

    @property
    def table(self) -> dict[str, F]:
        """The table a machine is handed: the shipped factory under each shipped name, else the test's own."""
        return {name: self.shipped.get(name, factory) for name, factory in self.items()}


def stand_in(monkeypatch: pytest.MonkeyPatch) -> StandIns[SpeechFactory]:
    """Every shipped voice adapter answering with the fake a test registers under its name, refusing till then.

    A run that forgot its fake fails at the build and never reaches a real adapter or a voice served on
    this machine. The adapters are back as they were when the test ends.
    """
    return _standing_in(monkeypatch, PROVIDERS, ADAPTERS)


def stand_in_sounds(monkeypatch: pytest.MonkeyPatch) -> StandIns[SoundFactory]:
    """Every shipped sound adapter answering with the fake a test registers under its name, refusing till then."""
    return _standing_in(monkeypatch, SOUNDS, SOUND_ADAPTERS)


def _standing_in[F](
    monkeypatch: pytest.MonkeyPatch,
    shipped: Mapping[str, F],
    adapters: Mapping[str, type[ElevenLabs | Dtsp | ElevenLabsSound]],
) -> StandIns[F]:
    """Each adapter in `adapters` built by the fake under its name in the stand-ins, or by itself when asked."""
    assert sorted(adapters) == sorted(shipped), "a shipped provider has no adapter here for a fake to stand in for"
    fakes: StandIns[F] = StandIns(shipped, {name: cast("F", _refused(name)) for name in shipped})
    for name, adapter in adapters.items():
        monkeypatch.setattr(adapter, "for_context", _built_by(fakes, name, adapter.for_context))
    return fakes


def _built_by[F](fakes: StandIns[F], name: str, real: Callable[[Any], object]) -> Callable[[Any], object]:
    """How the shipped adapter `name` is built while a test stands in for it: its fake, or itself when asked."""

    def build(context: object) -> object:
        factory = fakes[name]
        return real(context) if factory is fakes.shipped[name] else cast("Callable[[Any], object]", factory)(context)

    return build


def _refused(name: str, what: str = "provider") -> Callable[[Any], Any]:
    """A factory that refuses to build the shipped `what` called `name`."""

    def build(_context: object) -> object:
        raise AssertionError(f"the test built the shipped {what} {name!r}, which it never faked")

    return build


def refusing_aligners() -> dict[str, Callable[[AlignerContext], Aligner]]:
    """An aligner table naming the aligner DeckTalk ships, refusing to be built, for a test to put its fake in.

    A run on this table that needs an aligner and was given no fake fails at the build, so no test
    ever reaches a local server by a table it left alone.
    """
    return {name: _refused(name, "aligner") for name in ALIGNERS}


@dataclass
class FakeFfmpeg:
    """Every ffmpeg call a stage made, in order, with the answers it was given."""

    calls: list[list[str]] = field(default_factory=list)
    stderr_text: str = ""
    raw_bytes: bytes = b""
    duration_seconds: float = 1.0
    sounds: bool = True

    def wrote(self, suffix: str) -> list[Path]:
        """Every output path with this suffix, the output being the last argument of an ffmpeg call."""
        return [Path(call[-1]) for call in self.calls if call[-1].endswith(suffix)]


NOTHING_REPORTED: dict[str, object] = {
    "version": "0.5.0",
    "mode": "record",
    "scene": None,
    "slide": None,
    "warnings": [],
    "catalog": [],
    "cues": [],
    "words": [],
    "frameGaps": [],
    "longFrames": [],
}
"""What a page with the runtime on it and nothing to say answers `report()` with."""


class FakePage:
    """A Chromium page that answers every call and remembers what was asked of it.

    It is also a context or a page a script is added to, which is what `pages.instrument` is handed.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.init_scripts: list[str] = []
        self.html = ""
        self.report: dict[str, object] = dict(NOTHING_REPORTED)

    def page(self) -> Page:
        """This page as the Playwright page it stands in for."""
        return cast("Page", self)

    def on(self, event: str, handler: object) -> None:
        """A recorder listens for the page's own exceptions, and this page throws none."""

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)

    def set_content(self, html: str) -> None:
        self.html = html

    def evaluate(self, script: str, *_args: object) -> object:
        self.scripts.append(script)
        if pages.REPORT_JS in script:
            return dict(self.report)
        if pages.HAS_CATALOG_JS in script:
            return True
        return None

    def wait_for_function(self, script: str, **_kwargs: object) -> None:
        self.scripts.append(script)

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by skipping it."""

    def screenshot(self, *, path: str | Path, **_kwargs: object) -> None:
        Path(path).write_bytes(b"png")

    def close(self) -> None:
        """A page a test opened is closed the way a real one is, and closing it does nothing here."""


@dataclass
class FakeVoice:
    """The requests a provider was sent, and the audio and the words it answered with."""

    name: str = FAKE_VOICE_NAME
    requests: list[SpeechRequest] = field(default_factory=list)
    audio: bytes = b"take"
    words: list[Word] | None = field(
        default_factory=lambda: [Word(word="hello", start=0.0, end=0.5), Word(word="there", start=0.5, end=1.0)]
    )
    """The words every take comes with, or None for a voice that sends no word times."""
    model_digest: str | None = None

    def speak(self, request: SpeechRequest) -> Speech:
        self.requests.append(request)
        return Speech(self.audio, tuple(self.words) if self.words else None, self.model_digest)


class BareBrowser:
    """A launched Chromium, which opens pages that answer every call and closes without a sound."""

    version = "0.0.0.0"

    def __init__(self) -> None:
        self.closed = False

    def opened(self, policy: PagePolicy = TRUSTED) -> Chromium:
        """This browser as a Chromium launched under `policy`, which is what `chromium()` yields."""
        return Chromium(cast("Browser", self), policy)

    def new_page(self, **_kwargs: object) -> FakePage:
        return FakePage()

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    """`pw.chromium` as a launch uses it: an executable path, and a launch that needs the file there.

    Each launch's options are kept in `asked`. A launch works when the file it would run is on disk,
    which is what a fetch puts there. `refusal` is the other way a launch fails: the browser is on disk
    and still will not start, for the first `refusals` launches or, when that is None, for every one.
    """

    def __init__(self, executable: Path, *, refusal: str = "", refusals: int | None = None) -> None:
        self.executable_path = str(executable)
        self.refusal, self.refusals = refusal, refusals
        self.asked: list[dict[str, object]] = []
        self.looked_in: list[str | None] = []

    def launch(self, **options: object) -> BareBrowser:
        self.asked.append(options)
        target = Path(str(options.get("executable_path") or self.executable_path))
        if not target.is_file():
            raise PlaywrightError(f"Executable doesn't exist at {target}\nPlaywright was just installed")
        if self.refusal and (self.refusals is None or len(self.asked) <= self.refusals):
            raise PlaywrightError(self.refusal)
        return BareBrowser()

    def driver(self) -> Playwright:
        """This Chromium inside the Playwright a launch is handed, typed as the one it stands in for."""
        return cast("Playwright", SimpleNamespace(chromium=self))

    def started(self) -> Callable[[], AbstractContextManager[Playwright]]:
        """`sync_playwright` as a seam that starts this Chromium's driver, for code that opens its own.

        Each start keeps, in `looked_in`, the browser directory a real driver would read from the
        environment it copies as it starts.
        """

        def start() -> AbstractContextManager[Playwright]:
            self.looked_in.append(os.environ.get(BROWSERS_VARIABLE))
            return nullcontext(self.driver())

        return start


class FakeRoute:
    """Playwright's route object, as far as a route handler uses it: the answer, or what it did instead."""

    def __init__(self) -> None:
        self.answer: dict[str, object] | None = None
        self.continued = False
        self.aborted: str | None = None

    def fulfill(self, **kwargs: object) -> None:
        self.answer = kwargs

    def continue_(self) -> None:
        self.continued = True

    def abort(self, error_code: str) -> None:
        self.aborted = error_code


class FakeRouter:
    """A Playwright page or context, as far as `route_pages` uses it: it keeps the handler it is given."""

    def __init__(self) -> None:
        self.handler: Callable[[FakeRoute, object], None] | None = None

    def route(self, _pattern: str, handler: Callable[[FakeRoute, object], None]) -> None:
        self.handler = handler

    def request(self, url: str) -> FakeRoute:
        """What the kept handler does with one request for `url`."""
        assert self.handler is not None, "nothing was routed through this page"
        route = FakeRoute()
        self.handler(route, SimpleNamespace(url=url))
        return route

    def page(self) -> Page:
        """This router as the page `route_pages` is handed, typed as the one it stands in for."""
        return cast("Page", self)


FAKE_IDENTITY = AlignerIdentity(id="fake-aligner", revision="1", model_digest="sha256:0f00")
"""Who the fake aligner says it is, unless a test names another."""


@dataclass
class FakeAligner:
    """An aligner in this process: who it says it is, what it was asked, and the words it answers.

    It answers `words` when a test gives them, and otherwise spreads the words it was sent evenly over
    the WAV it was sent, or over `seconds` when the WAV holds no samples. `down` makes every request
    fail as a server that went away does, `fails` is the failure every request raises instead,
    `handshake` is the failure building it raises, and
    `on_align` runs before each answer, so a test can cancel or interrupt the run mid-pass.
    """

    identity: AlignerIdentity = FAKE_IDENTITY
    words: list[Word] | None = None
    seconds: float = 1.0
    requests: list[AlignmentRequest] = field(default_factory=list)
    contexts: list[AlignerContext] = field(default_factory=list)
    down: bool = False
    fails: ProviderError | None = None
    handshake: ProviderError | None = None
    on_align: Callable[[AlignmentRequest], None] | None = None

    def build(self, context: AlignerContext) -> FakeAligner:
        """This aligner as a factory builds it, failing as `handshake` says."""
        self.contexts.append(context)
        if self.handshake is not None:
            raise self.handshake
        return self

    def align(self, request: AlignmentRequest) -> list[Word]:
        self.requests.append(request)
        if self.on_align is not None:
            self.on_align(request)
        if self.down:
            raise ProviderError("could not reach the fake aligner: Connection refused", reached=False)
        if self.fails is not None:
            raise self.fails
        if self.words is not None:
            return list(self.words)
        seconds = _wav_seconds(request.wav) or self.seconds
        step = seconds / len(request.words)
        return [
            Word(word=word, start=round(i * step, 3), end=round((i + 1) * step, 3))
            for i, word in enumerate(request.words)
        ]


def _wav_seconds(wav: bytes) -> float:
    """How long a WAV plays, or zero when it is not one or holds no samples."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as sound:
            return sound.getnframes() / sound.getframerate()
    except (wave.Error, EOFError):
        return 0.0
