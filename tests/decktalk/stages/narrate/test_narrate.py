"""Stage one: the script becomes one take per section, indexed by content hash."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import Takes, take_file, words_file
from decktalk.cli import main
from decktalk.errors import ApprovalRequired, ErrorCode, InputError, ProviderError
from decktalk.events import Log, Progress, SectionStart, SpendEvent, TakeCharged, Unit
from decktalk.findings import Certainty, Code
from decktalk.inputs import Inputs
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, SpendState, TakeStatus, Word
from decktalk.settings import CONFIG_VARIABLE
from decktalk.speech import PROVIDERS, SpeechRequest, VoiceContext
from decktalk.stages import narrate as narrate_stage
from decktalk.stages.narrate import narrate
from decktalk.stages.narrate.plan import VOICE_VARIABLE
from support.fakes import FAKE_VOICE_NAME, FakeVoice
from support.interrupts import aimed_signals, interrupts_raise, press_ctrl_c
from support.logs import data_of, decisions
from support.projects import load_project
from support.runs import Watched

from .conftest import ENVIRON, SCRIPT, TOML, VOICE_ID

KEY = "ELEVENLABS_API_KEY"
"""The credential a paid take is bought with, which a run that buys nothing never reads."""

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every narrate test writes audio, and none of them may run ffmpeg to do it."""


def placeholder(inputs: Inputs, watched: Watched, **options: Any) -> NarrateResult:
    return narrate(inputs, watched.run, **options)


def test_a_run_without_voice_writes_a_take_for_every_spoken_section(inputs: Inputs, watched: Watched) -> None:
    result = placeholder(inputs, watched)
    assert isinstance(result, NarrateResult)
    assert result.spending is False
    assert [row.section for row in result.sections] == [1, 2, 3]
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}
    assert result.ok is True
    assert missing_sections(result) == [1, 2, 3]


def test_every_file_the_run_wrote_is_reported(inputs: Inputs, watched: Watched) -> None:
    result = placeholder(inputs, watched)
    written = {path.as_posix() for path in result.written}
    assert "build/narrate/takes.json" in written
    assert "build/narrate/narration.mp3" in written
    assert result.takes is not None
    assert result.takes.as_posix() == "build/narrate/takes.json"
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    for row in index.sections:
        assert take_file(row.hash) in {path.name for path in result.written}
        assert words_file(row.hash) in {path.name for path in result.written}


def test_the_index_is_written_in_section_order(inputs: Inputs, watched: Watched) -> None:
    """The index is the one order the narration is joined in, so it is the order the sections play."""
    placeholder(inputs, watched)
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2, 3]
    assert index.script == "script.md"
    assert index.estimated is True


def test_a_second_run_keeps_every_take_it_already_has(inputs: Inputs, watched: Watched) -> None:
    placeholder(inputs, watched)
    again = placeholder(inputs, watched)
    assert {row.status for row in again.sections} == {TakeStatus.KEPT}


def test_every_take_kept_or_made_says_why_and_the_worker_count_is_recorded(
    inputs: Inputs, watched: Watched, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with caplog.at_level("DEBUG", logger="decktalk"):
        placeholder(inputs, watched)
        workers = [data_of(record) for record in caplog.records if "workers" in data_of(record)]
        assert workers and workers[0]["jobs"] == 3
        said = sorted(decisions(caplog, "take", "section", "hit", "why"))
        assert said == [(1, False, "to-make"), (2, False, "to-make"), (3, False, "to-make")]
        placeholder(inputs, watched)
        said = sorted(decisions(caplog, "take", "section", "hit", "why"))
        assert said == [(1, True, "unchanged"), (2, True, "unchanged"), (3, True, "unchanged")]
        # A take the plan found and the worker then did not is told apart from one never made.
        monkeypatch.setattr(narrate_stage, "is_cached", lambda *_: False)
        placeholder(inputs, watched)
        said = sorted(decisions(caplog, "take", "section", "hit", "why"))
        assert said == [(1, False, "take-missing"), (2, False, "take-missing"), (3, False, "take-missing")]


def test_a_run_told_to_make_them_again_replaces_them(inputs: Inputs, watched: Watched) -> None:
    placeholder(inputs, watched)
    again = placeholder(inputs, watched, force=True)
    assert {row.status for row in again.sections} == {TakeStatus.PLACEHOLDER}


def test_a_section_the_run_left_out_keeps_the_take_it_had(inputs: Inputs, watched: Watched) -> None:
    placeholder(inputs, watched)
    result = placeholder(inputs, watched, only=[2], force=True)
    assert [row.section for row in result.sections] == [2]
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2, 3]


def test_a_selection_that_matches_no_spoken_section_is_refused(inputs: Inputs, watched: Watched) -> None:
    with pytest.raises(InputError) as refused:
        placeholder(inputs, watched, only=[9])
    assert "nothing to narrate" in str(refused.value)
    assert refused.value.hint == "The spoken sections are [1, 2, 3]."


def test_a_script_whose_headings_count_backwards_is_refused(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = make_inputs(script="## 2. Two\n\nA bowl.\n\n## 1. One\n\nA ball.\n")
    with pytest.raises(InputError) as refused:
        narrate(project, make_run(project).run)
    assert "do not ascend" in str(refused.value)
    assert refused.value.hint is not None
    assert "## 1. One" in refused.value.hint


def test_a_script_the_voice_would_read_out_is_refused_before_anything_is_written(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = make_inputs(script=SCRIPT.replace("A bowl.", "A bowl {x}."))
    with pytest.raises(InputError):
        narrate(project, make_run(project).run)
    assert not project.workspace.takes_path.exists()


def one_at_a_time(make_inputs: Callable[..., Inputs]) -> Inputs:
    """The project on a machine that voices one section at a time, so the pool works in script order."""
    return Inputs.load(make_inputs().root, environ=ENVIRON, machine={"narration": {"concurrency": 1}})


def test_the_run_reports_one_take_at_a_time(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = one_at_a_time(make_inputs)
    watched = make_run(project)
    placeholder(project, watched)
    lines = watched.of(Progress)
    assert [line.done for line in lines] == [1, 2, 3]
    assert {line.total for line in lines} == {3}
    assert {line.unit for line in lines} == {Unit.TAKE}
    assert {line.stage for line in lines} == {Stage.NARRATE}
    assert [line.section for line in watched.of(SectionStart)] == [1, 2, 3]


def test_a_paid_run_sends_one_request_per_section_and_reports_what_it_charged(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    watched = make_run(inputs, spend=True)
    result = narrate(inputs, watched.run)
    assert len(fake_voice.requests) == 3
    assert {row.status for row in result.sections} == {TakeStatus.VOICED}
    assert result.spend.state is SpendState.CHARGED
    assert result.spend.sections == (1, 2, 3)
    assert result.spend.dollars > 0
    charged = watched.of(TakeCharged)
    assert sorted(line.section for line in charged) == [1, 2, 3]
    assert sum(line.characters for line in charged) == result.spend.characters


@pytest.mark.usefixtures("fake_voice")
def test_a_take_the_run_found_on_disk_is_never_charged(inputs: Inputs, make_run: Callable[..., Watched]) -> None:
    """A ledger counts what was bought, so a take the cache answered puts no charge on the stream."""
    narrate(inputs, make_run(inputs, spend=True).run)
    again = make_run(inputs, spend=True)
    narrate(inputs, again.run)
    assert again.of(TakeCharged) == []


def test_a_paid_request_carries_the_published_voice_and_its_neighbours(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    project = one_at_a_time(make_inputs)
    narrate(project, make_run(project, spend=True).run)
    sent: list[SpeechRequest] = fake_voice.requests
    assert {request.voice_id for request in sent} == {"voice-under-test"}
    assert sent[0].previous_text is None
    assert sent[0].next_text == "It steps down the bowl."
    assert sent[-1].next_text is None


def test_the_price_is_approved_before_anything_is_sent(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    watched = make_run(inputs, spend=True)
    narrate(inputs, watched.run)
    priced = watched.of(SpendEvent)
    assert priced
    assert priced[0].spend.state is SpendState.ESTIMATE
    assert fake_voice.requests


def test_a_run_over_its_ceiling_buys_nothing(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    watched = make_run(inputs, spend=True, max_cost=0.001)
    with pytest.raises(ApprovalRequired):
        narrate(inputs, watched.run)
    assert fake_voice.requests == []
    assert not inputs.workspace.takes_path.exists()


class Recorded(Mapping[str, str]):
    """An environment that remembers every variable a run looked up, so a test can say which it read."""

    def __init__(self, values: dict[str, str]) -> None:
        self.held = values
        self.read: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.read.append(key)
        return self.held[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.held)

    def __len__(self) -> int:
        return len(self.held)


@dataclass
class CountedVoice:
    """A provider factory that counts how often a run built a voice, and asks for the key as ElevenLabs does."""

    voice: FakeVoice = field(default_factory=FakeVoice)
    built: int = 0

    def __call__(self, context: VoiceContext) -> FakeVoice:
        self.built += 1
        context.secrets.require(KEY)
        return self.voice


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> CountedVoice:
    """The `test-voice` provider, counting every time a run builds it."""
    voice = CountedVoice()
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, voice)
    return voice


def without_the_key(inputs: Inputs) -> tuple[Inputs, Recorded]:
    """The same project on a machine that names the voice and holds no key, with every lookup recorded."""
    environ = Recorded({VOICE_VARIABLE: VOICE_ID})
    return Inputs.load(inputs.root, environ=environ), environ


def missing_sections(result: NarrateResult) -> list[int | None]:
    """The section of every `TAKE_MISSING` finding, in the order the run reported them."""
    return [found.location.section for found in result.findings if found.code is Code.TAKE_MISSING]


def test_a_run_that_does_not_spend_plays_every_paid_take_with_no_key_and_no_voice(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """Takes bought on one machine build on another that holds the voice name and no key."""
    narrate(inputs, make_run(inputs, spend=True).run)
    assert counted.built == 1
    elsewhere, environ = without_the_key(inputs)
    result = narrate(elsewhere, make_run(elsewhere).run)
    assert {row.status for row in result.sections} == {TakeStatus.KEPT}
    assert result.findings == ()
    assert result.spend.dollars == 0
    assert counted.built == 1, "a run with every take on disk built a voice"
    assert KEY not in environ.read
    assert len(counted.voice.requests) == 3


def test_a_missing_take_without_spend_is_a_placeholder_and_one_finding(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run, only=[1])
    elsewhere, environ = without_the_key(inputs)
    result = narrate(elsewhere, make_run(elsewhere).run)
    assert [row.status for row in result.sections] == [TakeStatus.KEPT, TakeStatus.PLACEHOLDER, TakeStatus.PLACEHOLDER]
    assert missing_sections(result) == [2, 3]
    assert [found.code for found in result.findings] == [Code.TAKE_MISSING, Code.TAKE_MISSING]
    assert all(found.certainty is Certainty.UNCERTAIN for found in result.findings)
    assert "decktalk narrate --section 2 --spend" in result.findings[0].message
    assert result.ok is True
    assert counted.built == 1
    assert KEY not in environ.read


@pytest.mark.usefixtures("counted")
def test_paid_takes_that_cannot_be_matched_say_why_and_a_fresh_project_says_nothing(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """Without the voice id no paid take can be matched, which is worth a line only where one exists."""
    fresh = Inputs.load(inputs.root, environ={})
    first = make_run(fresh)
    narrate(fresh, first.run)
    assert not [line for line in first.of(Log) if VOICE_VARIABLE in line.message]
    narrate(inputs, make_run(inputs, spend=True).run, force=True)
    nameless = Inputs.load(inputs.root, environ={})
    again = make_run(nameless)
    result = narrate(nameless, again.run)
    assert [line for line in again.of(Log) if VOICE_VARIABLE in line.message]
    assert missing_sections(result) == [1, 2, 3]
    assert VOICE_VARIABLE in result.findings[0].message


def test_a_run_that_may_spend_with_every_take_on_disk_builds_no_voice(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    elsewhere, environ = without_the_key(inputs)
    result = narrate(elsewhere, make_run(elsewhere, spend=True).run)
    assert {row.status for row in result.sections} == {TakeStatus.KEPT}
    assert result.spending is True
    assert counted.built == 1
    assert KEY not in environ.read


def test_a_run_that_may_spend_buys_the_one_missing_take(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run, only=[1, 3])
    sent = len(counted.voice.requests)
    result = narrate(inputs, make_run(inputs, spend=True).run)
    assert [row.status for row in result.sections] == [TakeStatus.KEPT, TakeStatus.VOICED, TakeStatus.KEPT]
    assert len(counted.voice.requests) == sent + 1
    assert result.spend.sections == (2,)
    assert result.findings == ()


def test_a_run_that_may_not_spend_never_buys_even_from_a_voice_that_bills_nothing(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """The free rule is the command line's to apply, so `spend=False` is a promise to buy nothing."""
    free = make_inputs(toml=TOML.replace("price_per_1000_characters = 0.30", "price_per_1000_characters = 0"))
    result = narrate(free, make_run(free).run)
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert counted.built == 0


@pytest.mark.usefixtures("counted")
def test_a_run_told_to_replace_paid_takes_while_spending_buys_them_again(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    result = narrate(inputs, make_run(inputs, spend=True).run, only=[2], replace_voiced=True)
    assert [row.status for row in result.sections] == [TakeStatus.VOICED]
    assert len(counted.voice.requests) == 4


def test_two_sections_with_the_same_words_and_no_take_are_two_findings_and_one_placeholder(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    doubled = make_inputs(script=SCRIPT.replace("It steps down the bowl.", "Every picture waited for its word."))
    result = narrate(doubled, make_run(doubled).run)
    assert missing_sections(result) == [1, 2, 3]
    assert [row.status for row in result.sections] == [TakeStatus.PLACEHOLDER, TakeStatus.PLACEHOLDER, TakeStatus.KEPT]
    assert result.sections[1].hash == result.sections[2].hash


def test_the_command_line_with_spend_still_refuses_a_plan_over_its_ceiling(
    inputs: Inputs, counted: CountedVoice, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in {**ENVIRON, CONFIG_VARIABLE: str(inputs.root / "machine.toml")}.items():
        monkeypatch.setenv(name, value)
    code = main(["-p", str(inputs.root), "--json", "narrate", "--spend", "--max-cost", "0.001"])
    refused = json.loads(capsys.readouterr().out)
    assert code == ErrorCode.APPROVAL.exit_code
    assert refused["error"]["code"] == ErrorCode.APPROVAL.value
    assert "--max-cost" in refused["error"]["message"]
    assert counted.built == 0
    assert counted.voice.requests == []


@pytest.mark.usefixtures("fake_voice")
def test_a_run_told_to_make_takes_again_without_spend_keeps_every_paid_take(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    result = narrate(inputs, make_run(inputs).run, force=True)
    assert {row.status for row in result.sections} == {TakeStatus.KEPT}
    assert result.findings == ()


@pytest.mark.usefixtures("fake_voice")
def test_a_run_told_to_replace_a_paid_take_does(inputs: Inputs, make_run: Callable[..., Watched]) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    result = narrate(inputs, make_run(inputs).run, replace_voiced=True)
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert "told to replace its paid take" in result.findings[0].message
    again = narrate(inputs, make_run(inputs).run)
    assert {row.status for row in again.sections} == {TakeStatus.KEPT}, "the paid takes stayed on disk"


@pytest.mark.usefixtures("fake_voice")
def test_a_run_over_the_sections_nobody_paid_for_is_the_cheap_rehearsal(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run, only=[1])
    result = narrate(inputs, make_run(inputs).run, only=[2, 3])
    assert [row.section for row in result.sections] == [2, 3]
    assert missing_sections(result) == [2, 3]


class RefusesOneSection:
    """A voice that answers every section but one, which it refuses as a busy provider would.

    It refuses only once another section has been answered, so a take was paid for before the run
    failed whichever worker the pool handed which section first.
    """

    name = "test-voice"

    def __init__(self, refused: str) -> None:
        self.refused = refused
        self.answered = threading.Event()

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        if request.text == self.refused:
            assert self.answered.wait(timeout=10), "no other section was answered"
            raise ProviderError("the voice stopped answering.", retryable=True)
        self.answered.set()
        return b"take", [Word(word="A", start=0.0, end=0.4)]

    def cache_key(self, _request: SpeechRequest) -> str:
        return self.name


def test_the_index_is_checkpointed_after_every_take(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that fails keeps every take it has already paid for, so the next run reuses them."""
    (second,) = [segment for segment in inputs.spoken() if segment.index == 2]
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: RefusesOneSection(second.tts_text))
    watched = make_run(inputs, spend=True)
    with pytest.raises(ProviderError):
        narrate(inputs, watched.run)
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    indexed = [row.section for row in index.sections]
    assert 1 in indexed
    assert 2 not in indexed
    # Every take that was bought is on the stream, even though the run failed after it.
    assert sorted(line.section for line in watched.of(TakeCharged)) == indexed


class Overlapping:
    """A voice that counts its requests in flight and can hold the first `hold` until all have arrived.

    Holding two requests until both are in flight is what only a pool can satisfy, so a run that
    voiced one section at a time would break the barrier rather than pass by luck.
    """

    name = "test-voice"

    def __init__(self, hold: int = 2) -> None:
        self.hold = hold
        self.together = threading.Barrier(hold, timeout=10) if hold else None
        self.lock = threading.Lock()
        self.in_flight = 0
        self.most = 0
        self.sent = 0

    def speak(self, _request: SpeechRequest) -> tuple[bytes, list[Word]]:
        with self.lock:
            self.sent += 1
            self.in_flight += 1
            self.most = max(self.most, self.in_flight)
            held = self.together is not None and self.sent <= self.hold
        try:
            if held and self.together is not None:
                self.together.wait()
            return b"take", [Word(word="A", start=0.0, end=0.4)]
        finally:
            with self.lock:
                self.in_flight -= 1

    def cache_key(self, _request: SpeechRequest) -> str:
        return self.name


def test_sections_are_voiced_concurrently_and_reported_in_script_order(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests are in flight at once under the default, and the result still reads in order."""
    voice = Overlapping()
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: voice)
    watched = make_run(inputs, spend=True)
    result = narrate(inputs, watched.run)
    assert voice.most == 2
    assert [row.section for row in result.sections] == [1, 2, 3]
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2, 3]
    assert [line.done for line in watched.of(Progress)] == [1, 2, 3]


def test_a_concurrency_of_one_voices_one_section_at_a_time(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = Overlapping(hold=0)
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: voice)
    project = one_at_a_time(make_inputs)
    narrate(project, make_run(project, spend=True).run)
    assert voice.most == 1


def test_two_sections_with_the_same_words_buy_one_take_under_a_pool(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    """The section that shares its words is indexed after the pool, so it reads the take and buys none."""
    doubled = make_inputs(script="## 1. Open\n\nA bowl.\n\n## 2. Middle\n\nA bowl.\n\n## 3. Close\n\nA ball.\n")
    watched = make_run(doubled, spend=True)
    result = narrate(doubled, watched.run)
    assert len(fake_voice.requests) == 2
    assert [row.status for row in result.sections] == [TakeStatus.VOICED, TakeStatus.KEPT, TakeStatus.VOICED]
    assert sorted(line.section for line in watched.of(TakeCharged)) == [1, 3]


def test_a_section_that_left_the_script_leaves_the_index(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = make_inputs()
    narrate(project, make_run(project).run)
    shorter = make_inputs(
        toml=TOML.replace('[[section]]\nnumber = 3\npage = "deck/index.html"\nscene = "3"\n', ""),
        script=SCRIPT.split("## 3.")[0],
    )
    narrate(shorter, make_run(shorter).run)
    index = Takes.read(shorter.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2]


class HeldVoice:
    """A voice that counts the requests it was sent and answered, and holds the first `hold` until `release`.

    The count is the bill: a request that started is one the provider charges for.
    """

    name = "test-voice"

    def __init__(self, hold: int) -> None:
        self.hold = hold
        self.lock = threading.Lock()
        self.sent = 0
        self.answered = 0
        self.in_flight = threading.Event()
        self.release = threading.Event()

    def speak(self, _request: SpeechRequest) -> tuple[bytes, list[Word]]:
        with self.lock:
            self.sent += 1
            held = self.sent <= self.hold
            if self.sent == self.hold:
                self.in_flight.set()
        if held:
            assert self.release.wait(timeout=10), "the test never let the takes in flight go"
        with self.lock:
            self.answered += 1
        return b"take", [Word(word="A", start=0.0, end=0.4)]

    def cache_key(self, _request: SpeechRequest) -> str:
        return self.name


def six_sections(make_inputs: Callable[..., Inputs]) -> Inputs:
    """The project with six spoken sections, so most of them are still queued when the run is stopped."""
    numbers = range(1, 7)
    toml = TOML.split("[[section]]")[0] + "".join(
        f'[[section]]\nnumber = {n}\npage = "deck/index.html"\nscene = "{n}"\n\n' for n in numbers
    )
    script = "".join(f"## {n}. Part {n}\n\nThe words of part {n}.\n\n" for n in numbers)
    return make_inputs(toml=toml, script=script)


@aimed_signals
def test_ctrl_c_during_a_paid_run_buys_the_takes_in_flight_and_none_still_queued(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pool that waits for every queued section after Ctrl-C buys all of them, and only those in flight are wanted.

    The takes in flight are let go the moment the signal is sent, without waiting for the run to halt,
    so a worker that started the next take itself would buy a third. The signal is pending on the
    caller's thread once it is sent, and that thread raises it before it can hand out another take.
    """
    project = six_sections(make_inputs)
    voice = HeldVoice(hold=project.settings.narration.concurrency)
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: voice)

    def press() -> None:
        assert voice.in_flight.wait(timeout=10)
        press_ctrl_c()
        voice.release.set()

    pressing = threading.Thread(target=press, daemon=True)
    try:
        with interrupts_raise(), pytest.raises(KeyboardInterrupt):
            pressing.start()
            narrate(project, make_run(project, spend=True).run)
    finally:
        voice.release.set()
        pressing.join(timeout=10)
    assert voice.sent == 2, f"{voice.sent} takes were bought, and only the 2 in flight should have been"
    assert voice.answered == 2
    index = Takes.read(project.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2], "the takes already paid for are kept"


# ---- a project's own takes_dir ------------------------------------------------------------

IN_THE_PROJECT = TOML.replace("[narration]\n", '[narration]\ntakes_dir = "voice"\n')
"""The test project with its takes kept in `voice/`, which is the directory an author commits."""


def tracked_copy(inputs: Inputs, to: Path) -> Path:
    """What a fresh clone of this project holds: every file but the build directory and `.env`."""
    shutil.copytree(inputs.root, to, ignore=shutil.ignore_patterns("build", ".env"))
    return to


def take_files(directory: Path) -> set[str]:
    """Every take and words file in one directory, by name, leaving the index and the joined track out."""
    named = (path.name for path in directory.glob("*"))
    return {name for name in named if name.endswith((".mp3", ".words.json")) and name != "narration.mp3"}


def test_a_clone_of_a_project_that_commits_its_takes_plays_them_with_no_key(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], counted: CountedVoice, tmp_path: Path
) -> None:
    """The GitHub Action's case: the takes travel with the repository, so the clone buys and reads nothing."""
    bought = make_inputs(toml=IN_THE_PROJECT)
    narrate(bought, make_run(bought, spend=True).run)
    assert counted.built == 1
    clone = tracked_copy(bought, tmp_path / "clone")
    assert not (clone / "build").exists()
    environ = Recorded({VOICE_VARIABLE: VOICE_ID})
    fresh = Inputs.load(clone, environ=environ)
    result = narrate(fresh, make_run(fresh).run)
    assert {row.status for row in result.sections} == {TakeStatus.KEPT}
    assert {row.file.parent for row in result.sections if row.file is not None} == {Path("voice")}
    assert missing_sections(result) == []
    assert result.findings == ()
    assert counted.built == 1, "a clone with every take committed built a voice"
    assert KEY not in environ.read
    assert len(counted.voice.requests) == 3
    assert take_files(fresh.workspace.narrate_dir) == set()


@pytest.mark.usefixtures("counted")
def test_a_paid_run_writes_its_takes_their_words_and_the_index_into_the_projects_takes_dir(
    make_run: Callable[..., Watched], tmp_path: Path
) -> None:
    """A project's own directory is where a bought take lands, and a machine's shared store is only read."""
    shared = tmp_path / "machine-takes"
    machine = {"narration": {"cache_dir": str(shared)}}
    project = load_project(tmp_path / "proj", IN_THE_PROJECT, script=SCRIPT, environ=ENVIRON, machine=machine)
    result = narrate(project, make_run(project, spend=True).run)
    voice = project.root / "voice"
    hashes = {row.hash for row in result.sections}
    assert take_files(voice) == {take_file(h) for h in hashes} | {words_file(h) for h in hashes}
    assert (voice / "takes.json").is_file()
    assert project.workspace.takes_path == voice / "takes.json"
    assert result.takes == Path("voice/takes.json")
    assert not shared.exists() or take_files(shared) == set()
    assert take_files(project.workspace.narrate_dir) == set()
    assert project.workspace.narration_path.is_file(), "the joined track is build output"


def test_a_placeholder_is_build_output_and_never_lands_in_the_projects_takes_dir(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = make_inputs(toml=IN_THE_PROJECT)
    result = narrate(project, make_run(project).run)
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}
    assert take_files(project.root / "voice") == set()
    assert len(take_files(project.workspace.narrate_dir)) == 6


def test_a_take_only_the_machine_cache_holds_is_found_and_kept_in_the_project(
    make_run: Callable[..., Watched], counted: CountedVoice, tmp_path: Path
) -> None:
    """The project's directory is the first place a take is looked for, and the machine's store the second."""
    shared = {"narration": {"cache_dir": str(tmp_path / "machine-takes")}}
    elsewhere = load_project(tmp_path / "other", TOML, script=SCRIPT, environ=ENVIRON, machine=shared)
    narrate(elsewhere, make_run(elsewhere, spend=True).run)
    assert len(take_files(tmp_path / "machine-takes")) == 6
    load_project(tmp_path / "proj", IN_THE_PROJECT, script=SCRIPT)
    environ = Recorded({VOICE_VARIABLE: VOICE_ID})
    project = Inputs.load(tmp_path / "proj", environ=environ, machine=shared)
    watched = make_run(project)
    result = narrate(project, watched.run)
    assert {row.status for row in result.sections} == {TakeStatus.KEPT}
    assert result.findings == ()
    assert counted.built == 1
    assert KEY not in environ.read
    # The project keeps every take it plays, so committing its directory carries the film.
    assert take_files(project.root / "voice") == take_files(tmp_path / "machine-takes")
    assert len(take_files(tmp_path / "machine-takes")) == 6, "the machine's store is read, never moved"


@pytest.mark.parametrize("named", ["../outside", "/elsewhere/voice", "."])
def test_a_takes_dir_that_is_not_a_directory_inside_the_project_is_refused_at_load(tmp_path: Path, named: str) -> None:
    toml = TOML.replace("[narration]\n", f'[narration]\ntakes_dir = "{named}"\n')
    with pytest.raises(InputError, match=r"\[narration\] takes_dir") as refused:
        load_project(tmp_path / "proj", toml, script=SCRIPT, environ=ENVIRON)
    assert refused.value.hint
