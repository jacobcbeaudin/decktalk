"""Stage one: the script becomes one take per section, indexed by input digest."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any

import pytest
from filelock import FileLock

from decktalk.artifacts import (
    PLACEHOLDER_PREFIX,
    PLACEHOLDER_SUFFIX,
    ProviderWords,
    Takes,
    file_digest,
    is_placeholder,
    take_file,
    words_file,
)
from decktalk.cli import main
from decktalk.errors import ApprovalRequired, ErrorCode, InputError, ProjectLocked, ProviderError
from decktalk.events import CostPriced, RunLog, SectionDone, SectionStart, StageProgress, TakeCharged, Unit
from decktalk.findings import Code, Severity
from decktalk.inputs import Inputs
from decktalk.media import audio
from decktalk.pipeline import Artifact, Outcome, Stage
from decktalk.results import CostState, NarrateResult, TakeOutcome, Word, up_to_the_cent
from decktalk.settings import MACHINE_FILE_VARIABLE
from decktalk.speech import DECLARED, FREE, PROVIDERS, Piece, SpeechContext, SpeechRequest
from decktalk.speech.elevenlabs import ElevenLabs
from decktalk.stages import narrate as narrate_stage
from decktalk.stages.narrate import narrate, take_states
from decktalk.stages.narrate.plan import VOICE_ID_VARIABLE
from support.fakes import FAKE_VOICE_NAME, FakeVoice
from support.git import committed_clone, git, tracked_copy
from support.interrupts import aimed_signals, interrupts_raise, press_ctrl_c
from support.logs import data_of, decisions
from support.projects import load_project
from support.runs import Watched, a_voiced_run
from support.takes import TAKE_SUFFIX, damage_take

from .conftest import ENVIRON, SCRIPT, TOML, VOICE_ID

KEY = "ELEVENLABS_API_KEY"
"""The credential a voiced take is bought with, which a run that buys nothing never reads."""

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every narrate test writes audio, and none of them may run ffmpeg to do it."""


def placeholder(inputs: Inputs, watched: Watched, **options: Any) -> NarrateResult:
    return narrate(inputs, watched.run, **options)


def test_a_run_without_voice_writes_a_take_for_every_spoken_section(inputs: Inputs, watched: Watched) -> None:
    result = placeholder(inputs, watched)
    assert isinstance(result, NarrateResult)
    assert result.spend is False
    assert [row.section for row in result.sections] == [1, 2, 3]
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
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
        assert take_file(row.digest, TAKE_SUFFIX) in {path.name for path in result.written}
        assert words_file(row.digest) in {path.name for path in result.written}


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
    assert {row.outcome for row in again.sections} == {TakeOutcome.KEPT}


def test_a_first_run_says_each_take_was_made_and_not_made_again(
    inputs: Inputs, watched: Watched, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing was made before a first run, so its decision lines say made and leave "again" to no one."""
    with caplog.at_level("DEBUG", logger="decktalk"):
        placeholder(inputs, watched)
    said = [record.getMessage() for record in caplog.records if data_of(record).get("cache") == "take"]
    assert said == ["take made (to-make)."] * 3


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
        real = narrate_stage._write_takes

        def gone_before_the_workers(*args: Any, **kwargs: Any) -> object:
            for path in inputs.workspace.narrate_dir.glob(f"{PLACEHOLDER_PREFIX}*{PLACEHOLDER_SUFFIX}"):
                path.unlink()
            return real(*args, **kwargs)

        monkeypatch.setattr(narrate_stage, "_write_takes", gone_before_the_workers)
        placeholder(inputs, watched)
        said = sorted(decisions(caplog, "take", "section", "hit", "why"))
        assert said == [(1, False, "take-missing"), (2, False, "take-missing"), (3, False, "take-missing")]


def test_a_take_the_run_kept_ends_its_section_as_kept(inputs: Inputs, make_run: Callable[..., Watched]) -> None:
    """A section whose take was on disk did not run, so its line on the stream says kept, as its row does."""
    first = make_run(inputs)
    narrate(inputs, first.run)
    assert {line.outcome for line in first.of(SectionDone)} == {Outcome.RAN}
    second = make_run(inputs)
    narrate(inputs, second.run)
    assert [line.outcome for line in second.of(SectionDone)] == [Outcome.KEPT] * 3


def test_a_run_told_to_make_them_again_replaces_them(inputs: Inputs, watched: Watched) -> None:
    placeholder(inputs, watched)
    again = placeholder(inputs, watched, force=True)
    assert {row.outcome for row in again.sections} == {TakeOutcome.PLACEHOLDER}


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
    lines = watched.of(StageProgress)
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
    assert {row.outcome for row in result.sections} == {TakeOutcome.VOICED}
    assert result.cost.state is CostState.CHARGED
    assert result.cost.sections == (1, 2, 3)
    assert result.cost.dollars > 0
    charged = watched.of(TakeCharged)
    assert sorted(line.section for line in charged) == [1, 2, 3]
    assert sum(line.characters for line in charged) == result.cost.characters


def test_a_cap_of_nothing_refuses_a_take_that_costs_under_a_cent(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """A take of 7 characters costs $0.0021, which rounds up to a cent, so a cap of nothing refuses it."""
    tiny = make_inputs(script="## 1. Open\n\nA bowl.\n", name="tiny")
    cost = take_states(tiny).plan(spend=True).cost
    assert cost.dollars == cost.ceiling_dollars == 0.01
    assert cost.sentence == "This run costs $0.01 for 7 characters at $0.30 per 1,000 characters."
    with pytest.raises(ApprovalRequired):
        narrate(tiny, make_run(tiny, spend=True, max_cost=0.0).run)
    assert counted.voice.requests == []


@pytest.mark.usefixtures("fake_voice")
def test_the_charge_lines_a_host_adds_come_to_the_charged_price(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """A host that adds the `take.charged` lines as written and rounds up to the cent reads the charged price."""
    watched = make_run(inputs, spend=True)
    result = narrate(inputs, watched.run)
    charged = watched.of(TakeCharged)
    assert up_to_the_cent(line.dollars for line in charged) == result.cost.dollars == 0.03


@pytest.mark.usefixtures("fake_voice")
def test_a_paid_runs_charged_cost_adds_up_its_charge_lines_and_covers_only_the_sections_it_charged(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """The charged cost is priced from the takes the run paid for, so its charge lines add up to it."""
    watched = make_run(inputs, spend=True)
    result = narrate(inputs, watched.run)
    charged = watched.of(TakeCharged)
    assert result.cost.dollars == up_to_the_cent(line.dollars for line in charged)
    assert result.cost.characters == sum(line.characters for line in charged)
    assert result.cost.sections == tuple(sorted(line.section for line in charged))


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
    priced = watched.of(CostPriced)
    assert priced
    assert priced[0].cost.state is CostState.ESTIMATE
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

    def __call__(self, context: SpeechContext) -> FakeVoice:
        self.built += 1
        context.secrets.require(KEY)
        return self.voice


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> CountedVoice:
    """The fake voice under the shipped voice's name, counting every time a run builds it."""
    voice = CountedVoice()
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, voice)
    return voice


def without_the_key(inputs: Inputs) -> tuple[Inputs, Recorded]:
    """The same project on a machine that names the voice and holds no key, with every lookup recorded."""
    environ = Recorded({VOICE_ID_VARIABLE: VOICE_ID})
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
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.findings == ()
    assert result.cost.dollars == 0
    assert counted.built == 1, "a run with every take on disk built a voice"
    assert KEY not in environ.read
    assert len(counted.voice.requests) == 3


def test_a_missing_take_without_spend_is_a_placeholder_and_one_finding(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run, only=[1])
    elsewhere, environ = without_the_key(inputs)
    result = narrate(elsewhere, make_run(elsewhere).run)
    assert [row.outcome for row in result.sections] == [
        TakeOutcome.KEPT,
        TakeOutcome.PLACEHOLDER,
        TakeOutcome.PLACEHOLDER,
    ]
    assert missing_sections(result) == [2, 3]
    assert [found.code for found in result.findings] == [Code.TAKE_MISSING, Code.TAKE_MISSING]
    assert all(found.severity is Severity.WARNING for found in result.findings)
    assert "decktalk narrate --section 2 --spend" in result.findings[0].message
    assert result.ok is True
    assert counted.built == 1
    assert KEY not in environ.read


@pytest.mark.usefixtures("counted")
def test_paid_takes_that_cannot_be_matched_are_kept_and_say_why_and_a_fresh_project_says_nothing(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """Without the voice id no voiced take can be matched, which is worth a line only where one exists."""
    fresh = Inputs.load(inputs.root, environ={})
    first = make_run(fresh)
    narrate(fresh, first.run)
    assert not [line for line in first.of(RunLog) if VOICE_ID_VARIABLE in line.message]
    narrate(inputs, make_run(inputs, spend=True).run, force=True)
    played = {row.digest for row in Takes.require(inputs.workspace.takes_path, Artifact.TAKES).sections}
    nameless = Inputs.load(inputs.root, environ={})
    again = make_run(nameless)
    result = narrate(nameless, again.run)
    assert [line for line in again.of(RunLog) if VOICE_ID_VARIABLE in line.message]
    assert missing_sections(result) == []
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert {row.digest for row in Takes.require(inputs.workspace.takes_path, Artifact.TAKES).sections} == played


def test_a_run_that_may_spend_with_every_take_on_disk_builds_no_voice(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    elsewhere, environ = without_the_key(inputs)
    result = narrate(elsewhere, make_run(elsewhere, spend=True).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.spend is True
    assert counted.built == 1
    assert KEY not in environ.read


def test_a_run_that_may_spend_buys_the_one_missing_take(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run, only=[1, 3])
    sent = len(counted.voice.requests)
    result = narrate(inputs, make_run(inputs, spend=True).run)
    assert [row.outcome for row in result.sections] == [TakeOutcome.KEPT, TakeOutcome.VOICED, TakeOutcome.KEPT]
    assert len(counted.voice.requests) == sent + 1
    assert result.cost.sections == (2,)
    assert result.findings == ()


@dataclass
class Unreachable:
    """A voice whose server is down: every request it is handed finds nothing listening, and it counts them."""

    requests: list[SpeechRequest] = field(default_factory=list)
    name: str = FAKE_VOICE_NAME

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        self.requests.append(request)
        raise ProviderError("could not reach http://127.0.0.1:1/v1/speech/timed: refused", reached=False)


@pytest.fixture
def free_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped voice's name, declared free, so the project reads a voice that bills nothing."""
    monkeypatch.setitem(DECLARED, FAKE_VOICE_NAME, replace(DECLARED[FAKE_VOICE_NAME], billing=FREE))


@pytest.mark.usefixtures("free_voice")
def test_a_run_that_may_not_spend_voices_every_missing_take_from_a_voice_that_bills_nothing(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """Spend gates money and nothing else, so a free voice makes every missing take with no `--spend`."""
    watched = make_run(inputs)
    result = narrate(inputs, watched.run)
    assert watched.run.spend is False
    assert len(counted.voice.requests) == 3
    assert {row.outcome for row in result.sections} == {TakeOutcome.VOICED}
    assert missing_sections(result) == []
    assert result.cost.free and result.cost.dollars == 0


@pytest.mark.usefixtures("free_voice", "counted")
def test_a_take_from_a_voice_that_bills_nothing_puts_no_charge_on_the_stream(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """`take.charged` is a ledger line of money paid, as `sound.charged` is, so a free take writes none."""
    for spend in (False, True):
        fresh = make_run(inputs, spend=spend)
        result = narrate(inputs, fresh.run, replace_voiced=True)
        assert {row.outcome for row in result.sections} == {TakeOutcome.VOICED}
        assert fresh.of(TakeCharged) == []


def test_a_run_that_may_not_spend_sends_nothing_to_a_voice_that_bills(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    result = narrate(inputs, make_run(inputs).run)
    assert counted.built == 0 and counted.voice.requests == []
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert all("--spend to buy its take" in found.message for found in result.findings)


@pytest.mark.usefixtures("free_voice")
@pytest.mark.parametrize("spend", [False, True])
def test_a_free_voice_whose_server_is_down_plays_placeholders_and_says_to_start_it(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch, spend: bool
) -> None:
    """An unreachable free voice degrades as a missing take does, and its hint starts the server, never buys."""
    down = Unreachable()
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: down)
    result = narrate(inputs, make_run(inputs, spend=spend).run)
    assert result.ok is True
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert all(found.severity is Severity.WARNING for found in result.findings)
    assert all("could not be reached" in found.message for found in result.findings)
    assert all("--spend" not in found.message for found in result.findings)
    assert all(
        "Start " in found.message and "then run decktalk narrate --section" in found.message
        for found in result.findings
    )
    assert 1 <= len(down.requests) <= inputs.settings.narration.concurrency, "the run kept asking a server that is down"


@pytest.mark.usefixtures("free_voice")
def test_a_free_voice_that_could_not_be_reached_is_reported_as_voicing_nothing(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every section played a placeholder, so the run voiced nothing and its charged cost counts nothing."""
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: Unreachable())
    result = narrate(inputs, make_run(inputs).run)
    assert result.cost.state is CostState.CHARGED
    assert result.cost.characters == 0
    assert result.cost.sections == ()
    assert result.cost.sentence == "This run bought nothing."


@pytest.mark.usefixtures("free_voice")
def test_a_section_sharing_words_with_a_take_a_down_voice_never_made_plays_a_placeholder_too(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The section that would read the shared take finds none, so it stands in under a placeholder name."""
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: Unreachable())
    doubled = make_inputs(script="## 1. Open\n\nA bowl.\n\n## 2. Middle\n\nA bowl.\n\n## 3. Close\n\nA ball.\n")
    result = narrate(doubled, make_run(doubled).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert all(is_placeholder(row.digest) for row in result.sections)


@pytest.mark.usefixtures("free_voice")
def test_a_free_voice_with_no_voice_named_plays_placeholders_that_say_to_name_it(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """A free voice makes nothing until a voice is named, and its finding never sends the author to `--spend`."""
    nameless = Inputs.load(inputs.root, environ={})
    result = narrate(nameless, make_run(nameless).run)
    assert counted.built == 0
    assert missing_sections(result) == [1, 2, 3]
    assert all("Name the voice" in found.message and "--spend" not in found.message for found in result.findings)


def test_a_paid_voice_that_cannot_be_reached_still_fails_the_run(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder stands in for a free voice only, because a voiced take the author asked to buy is not optional."""
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: Unreachable())
    with pytest.raises(ProviderError):
        narrate(inputs, make_run(inputs, spend=True).run)


def test_a_run_told_to_make_takes_again_while_it_may_spend_buys_no_take(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """`force` rebuilds what is free, and a take is never free, so only `replace_voiced` buys one again."""
    narrate(inputs, make_run(inputs, spend=True).run)
    bought = len(counted.voice.requests)
    result = narrate(inputs, make_run(inputs, spend=True).run, force=True)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert len(counted.voice.requests) == bought, "a forced run bought a take it already held"
    assert result.cost.sections == ()
    assert counted.built == 1


@pytest.mark.usefixtures("counted")
def test_a_run_told_to_replace_paid_takes_while_it_may_spend_buys_them_again(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    result = narrate(inputs, make_run(inputs, spend=True).run, only=[2], replace_voiced=True)
    assert [row.outcome for row in result.sections] == [TakeOutcome.VOICED]
    assert len(counted.voice.requests) == 4


def test_two_sections_with_the_same_words_and_no_take_are_two_findings_and_one_placeholder(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    doubled = make_inputs(script=SCRIPT.replace("It steps down the bowl.", "Every picture waited for its word."))
    result = narrate(doubled, make_run(doubled).run)
    assert missing_sections(result) == [1, 2, 3]
    assert [row.outcome for row in result.sections] == [
        TakeOutcome.PLACEHOLDER,
        TakeOutcome.PLACEHOLDER,
        TakeOutcome.KEPT,
    ]
    assert result.sections[1].digest == result.sections[2].digest


def test_a_section_sharing_a_missing_take_says_why_the_take_is_missing(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    """The second section with the same words says the first one's reason, since nothing voices either."""
    doubled = make_inputs(script=SCRIPT.replace("It steps down the bowl.", "Every picture waited for its word."))
    result = narrate(doubled, make_run(doubled).run)
    for found in result.findings:
        assert (
            f"Section {found.location.section} plays a placeholder, because it has no voiced take yet." in found.message
        )


@pytest.mark.parametrize("named", [True, False], ids=["voice-named", "no-voice"])
def test_a_second_run_without_spend_says_what_the_first_said_of_each_placeholder(
    inputs: Inputs, make_run: Callable[..., Watched], named: bool
) -> None:
    """The placeholder the first run made is not a voiced take, so the second run's reason is the first's."""
    environ = ENVIRON if named else {key: value for key, value in ENVIRON.items() if key != VOICE_ID_VARIABLE}
    project = Inputs.load(inputs.root, environ=environ)
    first = [found.message for found in narrate(project, make_run(project).run).findings]
    second = [found.message for found in narrate(project, make_run(project).run).findings]
    assert len(first) == 3
    assert second == first
    assert all("because it has no voiced take yet." in message for message in first)


@pytest.mark.usefixtures("counted")
def test_a_voice_change_says_the_voice_may_have_changed_and_not_that_the_text_did(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """A new voice moves every digest, and the old takes still sit on disk, so the plan's own reason is the one said."""
    narrate(inputs, make_run(inputs, spend=True).run)
    revoiced = Inputs.load(inputs.root, environ={**ENVIRON, VOICE_ID_VARIABLE: "another-voice"})
    result = narrate(revoiced, make_run(revoiced).run)
    assert missing_sections(result) == [1, 2, 3]
    for found in result.findings:
        assert "because the text, the voice, the model or the voice settings changed." in found.message
        assert "no take of its current text" not in found.message
        assert "holds none of" not in found.message


@pytest.mark.usefixtures("counted")
def test_a_takes_directory_holding_none_of_the_takes_the_project_played_says_so(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """A renamed or missing folder looks like every take gone at once, which the finding names as such."""
    narrate(inputs, make_run(inputs, spend=True).run)
    assert inputs.workspace.store is None, "a machine with no store, such as the Action, has no second copy"
    inputs.workspace.takes.rename(inputs.root / "voice")
    moved = Inputs.load(inputs.root, environ=ENVIRON)
    result = narrate(moved, make_run(moved).run)
    assert missing_sections(result) == [1, 2, 3]
    for found in result.findings:
        assert "the takes directory takes holds none of the 3 takes this project played before" in found.message
        assert "[narration] takes_dir" in found.message
        assert f"decktalk narrate --section {found.location.section} --spend" in found.message


@pytest.mark.usefixtures("counted")
def test_one_take_gone_from_a_takes_directory_that_holds_the_rest_says_that_take_is_missing(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    bought = narrate(inputs, make_run(inputs, spend=True).run)
    (inputs.workspace.takes / words_file(bought.sections[1].digest)).unlink()
    result = narrate(inputs, make_run(inputs).run)
    [found] = result.findings
    assert "because the take or its words file is missing." in found.message
    assert "holds none of" not in found.message


def test_the_command_line_with_spend_still_refuses_a_plan_over_its_ceiling(
    inputs: Inputs, counted: CountedVoice, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in {**ENVIRON, MACHINE_FILE_VARIABLE: str(inputs.root / "machine.toml")}.items():
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
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.findings == ()


@pytest.mark.usefixtures("fake_voice")
def test_a_run_told_to_replace_a_paid_take_does(inputs: Inputs, make_run: Callable[..., Watched]) -> None:
    narrate(inputs, make_run(inputs, spend=True).run)
    result = narrate(inputs, make_run(inputs).run, replace_voiced=True)
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
    assert missing_sections(result) == [1, 2, 3]
    assert "told to replace its voiced take" in result.findings[0].message
    again = narrate(inputs, make_run(inputs).run)
    assert {row.outcome for row in again.sections} == {TakeOutcome.KEPT}, "the voiced takes stayed on disk"


@pytest.mark.usefixtures("counted")
def test_a_run_with_no_voice_named_keeps_the_voiced_takes_it_cannot_match(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """A voiced take of a section's current text is the one the film plays, voice named or not."""
    bought = narrate(inputs, make_run(inputs, spend=True).run)
    nameless = Inputs.load(inputs.root, environ={})
    result = narrate(nameless, make_run(nameless).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert [row.digest for row in result.sections] == [row.digest for row in bought.sections]
    assert missing_sections(result) == []


INSERTED = SCRIPT.replace("## 2. Middle", "## 2. New\n\nA new thought arrives.\n\n## 3. Middle").replace(
    "## 3. Close", "## 4. Close"
)
"""Section 2 inserted, so the old 2 and 3 are 3 and 4 now."""


@pytest.mark.usefixtures("counted")
def test_one_section_inserted_and_narrated_alone_says_it_has_no_voiced_take(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """Its number's row holds words another section speaks now, which says nothing about the new section."""
    narrate(inputs, make_run(inputs, spend=True).run)
    four = TOML + '\n[[section]]\nnumber = 4\npage = "deck/index.html"\nscene = "4"\n'
    moved = load_project(inputs.root, four, script=INSERTED, environ=ENVIRON)
    result = narrate(moved, make_run(moved).run, only=[2])
    [found] = result.findings
    assert "because it has no voiced take yet." in found.message


@pytest.mark.usefixtures("counted")
def test_a_placeholder_section_in_a_moved_takes_directory_keeps_its_own_reason(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    """Only a section whose row was voiced lost its take with the folder, so only it says the folder moved."""
    narrate(inputs, make_run(inputs, spend=True).run, only=[1, 2])
    narrate(inputs, make_run(inputs).run)
    inputs.workspace.takes.rename(inputs.root / "voice")
    moved = Inputs.load(inputs.root, environ=ENVIRON)
    result = narrate(moved, make_run(moved).run)
    by_section = {found.location.section: found.message for found in result.findings}
    assert "holds none of the 2 takes this project played before" in by_section[1]
    assert "because it has no voiced take yet." in by_section[3]
    assert "holds none of" not in by_section[3]


@pytest.mark.usefixtures("counted")
def test_a_damaged_take_without_spend_and_told_to_replace_plays_a_placeholder_and_says_so(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    bought = narrate(inputs, make_run(inputs, spend=True).run)
    damage_take(inputs, bought.sections[1].digest)
    result = narrate(inputs, make_run(inputs).run, only=[2], replace_voiced=True)
    assert [row.outcome for row in result.sections] == [TakeOutcome.PLACEHOLDER]
    [found] = result.findings
    assert "because this run was told to replace its voiced take." in found.message


def test_a_damaged_take_is_bought_again_when_the_run_is_told_to_replace_it_and_may_spend(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    bought = narrate(inputs, make_run(inputs, spend=True).run)
    damage_take(inputs, bought.sections[1].digest)
    sent = len(counted.voice.requests)
    result = narrate(inputs, make_run(inputs, spend=True).run, only=[2], replace_voiced=True)
    assert [row.outcome for row in result.sections] == [TakeOutcome.VOICED]
    assert len(counted.voice.requests) == sent + 1


@pytest.mark.usefixtures("counted")
def test_a_run_reads_the_take_index_twice(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once for the take states and once for the rows the run rewrites, however many sections it plays."""
    narrate(inputs, make_run(inputs).run)
    reads: list[None] = []
    real = Inputs.takes

    def counted_reads(self: Inputs) -> Takes | None:
        reads.append(None)
        return real(self)

    monkeypatch.setattr(Inputs, "takes", counted_reads)
    narrate(inputs, make_run(inputs).run)
    assert len(reads) == 2


def test_a_take_bought_again_re_places_every_section_that_plays_it(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sections 1 and 3 play one take, so buying it again for 3 measures 1 on the new bytes too."""
    a_voice_saying(monkeypatch, b"FIRST")
    doubled = make_inputs(script=SCRIPT.replace("Every picture waited for its word.", "A bowl. [beat] A ball."))
    narrate(doubled, make_run(doubled, spend=True).run)
    a_voice_saying(monkeypatch, b"A LONGER SECOND TAKE")
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 1.5)
    result = narrate(doubled, make_run(doubled, spend=True).run, only=[3], replace_voiced=True)
    assert [row.section for row in result.sections] == [3, 1]
    index = Takes.read(doubled.workspace.takes_path)
    assert index is not None
    assert [row.sound_end_seconds for row in index.sections] == [1.5, 0.8, 1.5]


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

    name = FAKE_VOICE_NAME

    def __init__(self, refused: tuple[Piece, ...]) -> None:
        self.refused = refused
        self.answered = threading.Event()

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        if request.pieces == self.refused:
            assert self.answered.wait(timeout=10), "no other section was answered"
            raise ProviderError("the voice stopped answering.", retryable=True)
        self.answered.set()
        return b"take", [Word(word="A", start=0.0, end=0.4)]


def test_the_index_is_checkpointed_after_every_take(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that fails keeps every take it has already paid for, so the next run reuses them."""
    (second,) = [section for section in inputs.spoken() if section.number == 2]
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: RefusesOneSection(second.pieces))
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

    name = FAKE_VOICE_NAME

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


def test_sections_are_voiced_concurrently_and_reported_in_script_order(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests are in flight at once under the default, and the result still reads in order."""
    voice = Overlapping()
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: voice)
    watched = make_run(inputs, spend=True)
    result = narrate(inputs, watched.run)
    assert voice.most == 2
    assert [row.section for row in result.sections] == [1, 2, 3]
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2, 3]
    assert [line.done for line in watched.of(StageProgress)] == [1, 2, 3]


def test_a_concurrency_of_one_voices_one_section_at_a_time(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = Overlapping(hold=0)
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: voice)
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
    assert [row.outcome for row in result.sections] == [TakeOutcome.VOICED, TakeOutcome.KEPT, TakeOutcome.VOICED]
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

    name = FAKE_VOICE_NAME

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
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: voice)

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
    clone = tracked_copy(bought.root, tmp_path / "clone")
    assert not (clone / "build").exists()
    environ = Recorded({VOICE_ID_VARIABLE: VOICE_ID})
    fresh = Inputs.load(clone, environ=environ)
    result = narrate(fresh, make_run(fresh).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert {row.file.parent for row in result.sections if row.file is not None} == {Path("voice")}
    assert missing_sections(result) == []
    assert result.findings == ()
    assert counted.built == 1, "a clone with every take committed built a voice"
    assert KEY not in environ.read
    assert len(counted.voice.requests) == 3
    assert take_files(fresh.workspace.narrate_dir) == set()


@pytest.mark.usefixtures("counted")
def test_a_paid_run_writes_its_takes_and_their_words_into_the_projects_takes_dir(
    make_run: Callable[..., Watched], tmp_path: Path
) -> None:
    """A project's own directory is where a voiced take lands, and the machine's store keeps a second copy."""
    shared = tmp_path / "machine-takes"
    machine = {"narration": {"store_dir": str(shared)}}
    project = load_project(tmp_path / "proj", IN_THE_PROJECT, script=SCRIPT, environ=ENVIRON, machine=machine)
    result = narrate(project, make_run(project, spend=True).run)
    voice = project.root / "voice"
    hashes = {row.digest for row in result.sections}
    assert take_files(voice) == {take_file(h, TAKE_SUFFIX) for h in hashes} | {words_file(h) for h in hashes}
    assert not (voice / "takes.json").exists(), "the index is a cache under the build, never committed"
    assert result.takes == Path("build/narrate/takes.json")
    assert take_files(shared) == take_files(voice), "every take bought is written through to the store"
    assert take_files(project.workspace.narrate_dir) == set()
    assert project.workspace.narration_path.is_file(), "the joined track is build output"


def test_a_placeholder_is_build_output_and_never_lands_in_the_projects_takes_dir(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    project = make_inputs(toml=IN_THE_PROJECT)
    result = narrate(project, make_run(project).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.PLACEHOLDER}
    assert take_files(project.root / "voice") == set()
    assert len(take_files(project.workspace.narrate_dir)) == 6


def test_a_take_only_the_machine_cache_holds_is_found_and_kept_in_the_project(
    make_run: Callable[..., Watched], counted: CountedVoice, tmp_path: Path
) -> None:
    """The project's directory is the first place a take is looked for, and the machine's store the second."""
    shared = {"narration": {"store_dir": str(tmp_path / "machine-takes")}}
    elsewhere = load_project(tmp_path / "other", TOML, script=SCRIPT, environ=ENVIRON, machine=shared)
    narrate(elsewhere, make_run(elsewhere, spend=True).run)
    assert len(take_files(tmp_path / "machine-takes")) == 6
    load_project(tmp_path / "proj", IN_THE_PROJECT, script=SCRIPT)
    environ = Recorded({VOICE_ID_VARIABLE: VOICE_ID})
    project = Inputs.load(tmp_path / "proj", environ=environ, machine=shared)
    watched = make_run(project)
    result = narrate(project, watched.run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.findings == ()
    assert counted.built == 1
    assert KEY not in environ.read
    # The project keeps every take it plays, so committing its directory carries the film.
    assert take_files(project.root / "voice") == take_files(tmp_path / "machine-takes")
    assert len(take_files(tmp_path / "machine-takes")) == 6, "the machine's store is read, never moved"


@pytest.mark.parametrize("environ", [ENVIRON, {}], ids=["voice-named", "no-voice"])
def test_a_run_that_does_not_spend_leaves_a_checkout_that_commits_its_takes_clean(
    make_inputs: Callable[..., Inputs],
    make_run: Callable[..., Watched],
    counted: CountedVoice,
    tmp_path: Path,
    environ: dict[str, str],
) -> None:
    """The Action's case: the take index is a cache under the build, so playing committed takes changes no file."""
    bought = make_inputs(toml=IN_THE_PROJECT)
    narrate(bought, make_run(bought, spend=True).run)
    clone = committed_clone(bought.root, tmp_path / "clone")
    fresh = Inputs.load(clone, environ=environ)
    narrate(fresh, make_run(fresh).run)
    assert git(clone, "status", "--porcelain") == ""
    assert fresh.workspace.takes_path == fresh.workspace.build / "narrate" / "takes.json"
    assert counted.built == 1


def test_a_bought_take_is_written_into_the_takes_directory_by_default_and_never_under_the_build(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """`rm -rf build` is free, so a voiced take never lands there, with no setting to remember."""
    result = narrate(inputs, make_run(inputs, spend=True).run)
    hashes = {row.digest for row in result.sections}
    assert inputs.settings.narration.takes_dir == "takes"
    assert take_files(inputs.root / "takes") == {take_file(h, TAKE_SUFFIX) for h in hashes} | {
        words_file(h) for h in hashes
    }
    assert {row.file.parent for row in result.sections if row.file is not None} == {Path("takes")}
    assert take_files(inputs.workspace.build / "narrate") == set()
    assert counted.built == 1


def test_an_empty_takes_dir_is_refused_at_load(tmp_path: Path) -> None:
    """An empty value would leave a voiced take no folder a person keeps, so it is refused rather than read."""
    toml = TOML.replace("[narration]\n", '[narration]\ntakes_dir = ""\n')
    with pytest.raises(InputError, match=r"narration\.takes_dir"):
        load_project(tmp_path / "proj", toml, script=SCRIPT, environ=ENVIRON)


def test_a_bought_take_records_the_size_and_blake3_of_its_audio_in_its_words_file(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """The take's name says what was asked for, so only a fingerprint of the bytes can tell a damaged copy."""
    result = narrate(inputs, make_run(inputs, spend=True).run)
    for row in result.sections:
        audio_file = inputs.workspace.takes / take_file(row.digest, TAKE_SUFFIX)
        words = ProviderWords.read(inputs.workspace.takes / words_file(row.digest))
        assert words is not None and words.audio is not None
        assert words.audio.bytes == audio_file.stat().st_size
        assert words.audio.blake3 == file_digest(audio_file)
    assert counted.built == 1


@pytest.mark.usefixtures("counted")
def test_a_run_stopped_while_it_writes_a_take_leaves_neither_half_of_it(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take and its words are one atomic pair, so audio is never left on disk with no words to make it whole."""
    opened = Path.open

    def stopped(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if mode == "xb" and ".words.json." in self.name:
            raise KeyboardInterrupt
        return opened(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", stopped)
    with pytest.raises(KeyboardInterrupt):
        narrate(inputs, make_run(inputs, spend=True).run, only=[1])
    monkeypatch.setattr(Path, "open", opened)
    assert take_files(inputs.workspace.takes) == set(), "half a pair was left on disk"
    watched = make_run(inputs, spend=True)
    narrate(inputs, watched.run)
    assert len(watched.of(TakeCharged)) == 3


@pytest.mark.parametrize("named", ["../outside", "/elsewhere/voice", "."])
def test_a_takes_dir_that_is_not_a_directory_inside_the_project_is_refused_at_load(tmp_path: Path, named: str) -> None:
    toml = TOML.replace("[narration]\n", f'[narration]\ntakes_dir = "{named}"\n')
    with pytest.raises(InputError, match=r"\[narration\] takes_dir") as refused:
        load_project(tmp_path / "proj", toml, script=SCRIPT, environ=ENVIRON)
    assert refused.value.hint


# ---- a damaged take ---------------------------------------------------------------------------------

STORED = "machine-takes"
"""The machine's take store in these tests, which sits beside the project and never inside it."""


def on_a_machine_with_a_store(
    make_run: Callable[..., Watched], tmp_path: Path, *, name: str = "proj"
) -> tuple[Inputs, Path, list[str]]:
    """A project that bought its three takes, with a copy of each in the machine's store, and their digests."""
    store = tmp_path / STORED
    machine = {"narration": {"store_dir": str(store)}}
    project = load_project(tmp_path / name, TOML, script=SCRIPT, environ=ENVIRON, machine=machine)
    result = narrate(project, make_run(project, spend=True).run)
    store.mkdir(exist_ok=True)
    for kept in project.workspace.takes.iterdir():
        shutil.copyfile(kept, store / kept.name)
    return project, store, [row.digest for row in result.sections]


def said(watched: Watched) -> str:
    """Every sentence the run put on the stream, one per line, which is where a note about a damaged copy goes."""
    return "\n".join(line.message for line in watched.of(RunLog))


def unreadable(directory: Path) -> set[str]:
    """Every file in one directory a run moved aside because it did not read."""
    return {path.name for path in directory.glob("*.unreadable")}


@pytest.mark.usefixtures("counted")
def test_a_copy_set_aside_twice_keeps_both_damaged_copies(make_run: Callable[..., Watched], tmp_path: Path) -> None:
    """Nothing damaged is ever deleted, so a second copy moved aside takes the next number free for both its files."""
    project, _store, (first, *_rest) = on_a_machine_with_a_store(make_run, tmp_path)
    takes = project.workspace.takes
    audio_name, words_name = take_file(first, TAKE_SUFFIX), words_file(first)
    (takes / words_name).write_text("{", encoding="utf-8")
    once = make_run(project)
    narrate(project, once.run)
    (takes / words_name).write_text("{{", encoding="utf-8")
    twice = make_run(project)
    narrate(project, twice.run)
    assert unreadable(takes) == {
        f"{audio_name}.unreadable",
        f"{words_name}.unreadable",
        f"{audio_name}.1.unreadable",
        f"{words_name}.1.unreadable",
    }
    assert (takes / f"{words_name}.unreadable").read_text(encoding="utf-8") == "{"
    assert (takes / f"{words_name}.1.unreadable").read_text(encoding="utf-8") == "{{"
    assert said(once).count("moved aside") == 1
    assert said(twice).count("moved aside") == 1


# ---- the machine's take store ------------------------------------------------------------------------


def in_the_store(tmp_path: Path, name: str, *, toml: str = TOML) -> Inputs:
    """One project on a machine whose take store is `machine-takes` beside it."""
    machine = {"narration": {"store_dir": str(tmp_path / STORED)}}
    return load_project(tmp_path / name, toml, script=SCRIPT, environ=ENVIRON, machine=machine)


def a_voice_saying(monkeypatch: pytest.MonkeyPatch, audio: bytes) -> FakeVoice:
    """A fake voice under the shipped voice's name that answers every request with these bytes."""
    voice = FakeVoice(audio=audio)
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: voice)
    return voice


def test_a_run_that_does_not_spend_never_buys_and_never_fills_an_empty_store(
    make_run: Callable[..., Watched], tmp_path: Path, counted: CountedVoice
) -> None:
    """A run that does not spend never buys. It heals a damaged store copy from a whole takes copy, and leaves an
    empty store empty."""
    project, store, _digests = on_a_machine_with_a_store(make_run, tmp_path)
    sent = len(counted.voice.requests)
    shutil.rmtree(store)
    store.mkdir()
    result = narrate(project, make_run(project).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert take_files(store) == set()
    assert len(counted.voice.requests) == sent


@pytest.mark.parametrize("replace_voiced", [False, True], ids=["plain", "a-replace-run-whose-section-was-missing"])
def test_a_take_another_project_voiced_after_this_run_was_priced_is_played_and_not_bought_again(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_voiced: bool
) -> None:
    """A copy another project bought once this run was priced is a copy the store holds when the lock is won, so it
    plays. Only a run told to replace this take buys it again, and a section this project never voiced is not one."""
    a = in_the_store(tmp_path, "a")
    b = in_the_store(tmp_path, "b")
    voice_a, voice_b = FakeVoice(audio=b"AUDIO-A"), FakeVoice(audio=b"AUDIO-B")
    built: list[None] = []

    def factory(_context: SpeechContext) -> FakeVoice:
        built.append(None)
        if len(built) == 1:
            # b's build, after b's plan and approval: project a buys the same take first.
            narrate(a, make_run(a, spend=True).run, only=[1])
            return voice_b
        return voice_a

    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, factory)
    result = narrate(b, make_run(b, spend=True).run, only=[1], replace_voiced=replace_voiced)
    digest = result.sections[0].digest
    assert len(voice_a.requests) == 1
    assert voice_b.requests == [], "b bought a take a had bought before b took the lock"
    assert (b.workspace.takes / take_file(digest, TAKE_SUFFIX)).read_bytes() == b"AUDIO-A"
    assert result.sections[0].outcome is TakeOutcome.KEPT


def test_a_take_another_project_voiced_first_is_reported_as_bought_nothing(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take the store answered was never paid for by this run, so its charged cost is nothing, never the price."""
    a = in_the_store(tmp_path, "a")
    b = in_the_store(tmp_path, "b")
    voice_a, voice_b = FakeVoice(audio=b"AUDIO-A"), FakeVoice(audio=b"AUDIO-B")
    built: list[None] = []

    def factory(_context: SpeechContext) -> FakeVoice:
        built.append(None)
        if len(built) == 1:
            narrate(a, make_run(a, spend=True).run, only=[1])
            return voice_b
        return voice_a

    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, factory)
    watched = make_run(b, spend=True)
    result = narrate(b, watched.run, only=[1])
    assert watched.of(TakeCharged) == []
    assert result.cost.state is CostState.CHARGED
    assert result.cost.dollars == result.cost.ceiling_dollars == 0
    assert result.cost.characters == 0
    assert result.cost.sections == ()
    assert result.cost.sentence == "This run bought nothing."


def pair_of(place: Path, digest: str) -> tuple[bytes, bytes]:
    """The audio and the words of one voiced take in one place, read as they are."""
    return (place / take_file(digest, TAKE_SUFFIX)).read_bytes(), (place / words_file(digest)).read_bytes()


def agrees(place: Path, digest: str) -> bool:
    """True when one place's words of a voiced take record the size and BLAKE3 of the audio beside them."""
    words = ProviderWords.read(place / words_file(digest))
    audio_file = place / take_file(digest, TAKE_SUFFIX)
    return (
        words is not None
        and words.audio is not None
        and words.audio.bytes == audio_file.stat().st_size
        and words.audio.blake3 == file_digest(audio_file)
    )


def test_a_take_the_takes_directory_cannot_hold_is_kept_in_the_store_and_copied_in_by_the_next_run(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take is paid for the moment the voice answers, so the store is written first, and a takes directory that
    cannot hold it costs one more run and no second purchase."""
    voice = a_voice_saying(monkeypatch, b"PAID")
    a = in_the_store(tmp_path, "a")
    digest = take_states(a)[1].digest
    assert digest is not None
    squatter = a.workspace.takes / take_file(digest, TAKE_SUFFIX)
    squatter.mkdir(parents=True)
    watched = make_run(a, spend=True)
    with pytest.raises(InputError, match="A good copy of it is in the take store"):
        narrate(a, watched.run, only=[1])
    assert len(watched.of(TakeCharged)) == 1
    store = tmp_path / STORED
    assert agrees(store, digest)
    squatter.rmdir()
    result = narrate(a, make_run(a).run, only=[1])
    assert [row.outcome for row in result.sections] == [TakeOutcome.KEPT]
    assert len(voice.requests) == 1
    assert pair_of(a.workspace.takes, digest) == pair_of(store, digest)


def test_a_re_buy_over_a_damaged_project_copy_sets_it_aside_first(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy of the same length passes the size check, so only its BLAKE3 tells it is damaged, and it is set
    aside before the take voiced again replaces it rather than overwritten."""
    a_voice_saying(monkeypatch, b"FIRST")
    a = in_the_store(tmp_path, "a")
    digest = narrate(a, make_run(a, spend=True).run, only=[1]).sections[0].digest
    takes = a.workspace.takes
    audio_name, words_name = take_file(digest, TAKE_SUFFIX), words_file(digest)
    (takes / audio_name).write_bytes(b"XXXXX")
    shutil.rmtree(tmp_path / STORED)
    a_voice_saying(monkeypatch, b"SECOND")
    watched = make_run(a, spend=True)
    narrate(a, watched.run, only=[1], replace_voiced=True)
    assert (takes / audio_name).read_bytes() == b"SECOND"
    assert unreadable(takes) == {f"{audio_name}.unreadable", f"{words_name}.unreadable"}
    assert (takes / f"{audio_name}.unreadable").read_bytes() == b"XXXXX"
    assert said(watched).count("moved aside") == 1


def test_a_re_buy_replaces_a_store_copy_that_does_not_hold_the_bytes_its_words_recorded(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store keeps its first good pair, and a copy whose audio was swapped for bytes of the same length is not
    one, so a re-buy writes the store and moves the damaged copy aside."""
    a_voice_saying(monkeypatch, b"FIRST")
    a = in_the_store(tmp_path, "a")
    digest = narrate(a, make_run(a, spend=True).run, only=[1]).sections[0].digest
    store = tmp_path / STORED
    audio_name = take_file(digest, TAKE_SUFFIX)
    (store / audio_name).write_bytes(b"XXXXX")
    b = in_the_store(tmp_path, "b")
    a_voice_saying(monkeypatch, b"SECOND")
    narrate(b, make_run(b, spend=True).run, only=[1], replace_voiced=True)
    assert (store / audio_name).read_bytes() == b"SECOND"
    assert (store / f"{audio_name}.unreadable").read_bytes() == b"XXXXX"


def test_a_run_kept_waiting_past_store_wait_seconds_is_refused_naming_it_and_buys_nothing(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait on another run's voicing is its own machine setting, so the refusal names it and no request times."""
    voice = a_voice_saying(monkeypatch, b"PAID")
    store = tmp_path / STORED
    toml = TOML.replace("[narration]\n", "[narration]\ntimeout_seconds = 10\n")
    machine = {"narration": {"store_dir": str(store), "store_wait_seconds": 1}}
    a = load_project(tmp_path / "a", toml, script=SCRIPT, environ=ENVIRON, machine=machine)
    digest = narrate(a, make_run(a, spend=True).run, only=[1]).sections[0].digest
    for place in (a.workspace.takes, store):
        (place / take_file(digest, TAKE_SUFFIX)).unlink()
        (place / words_file(digest)).unlink()
    with FileLock(store / f"{digest}.lock"), pytest.raises(ProjectLocked, match=r"\[narration\] store_wait_seconds"):
        narrate(a, make_run(a, spend=True).run, only=[1])
    assert len(voice.requests) == 1


def test_a_takes_directory_that_cannot_be_written_is_refused_before_the_voice_is_asked(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take is paid for the moment the voice answers, so a place that cannot hold it is refused before that."""
    voice = a_voice_saying(monkeypatch, b"PAID")
    a = in_the_store(tmp_path, "a")
    a.workspace.takes.write_bytes(b"")
    watched = make_run(a, spend=True)
    with pytest.raises(InputError, match="nothing was paid"):
        narrate(a, watched.run, only=[1])
    assert voice.requests == []
    assert watched.of(TakeCharged) == []


def test_a_take_store_that_cannot_be_made_is_refused_naming_store_dir_before_the_voice_is_asked(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = a_voice_saying(monkeypatch, b"PAID")
    a = in_the_store(tmp_path, "a")
    (tmp_path / STORED).write_bytes(b"")
    with pytest.raises(InputError, match=r"\[narration\] store_dir"):
        narrate(a, make_run(a, spend=True).run, only=[1])
    assert voice.requests == []


def test_a_run_that_keeps_a_take_heals_a_damaged_store_copy_from_the_takes_directory(
    make_run: Callable[..., Watched], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that does not spend never buys, and still replaces a damaged store copy with its own whole one."""
    voice = a_voice_saying(monkeypatch, b"PAID")
    a = in_the_store(tmp_path, "a")
    digest = narrate(a, make_run(a, spend=True).run, only=[1]).sections[0].digest
    store = tmp_path / STORED
    (store / words_file(digest)).write_text("{", encoding="utf-8")
    narrate(a, make_run(a).run, only=[1])
    assert (store / words_file(digest)).read_bytes() == (a.workspace.takes / words_file(digest)).read_bytes()
    assert (store / f"{words_file(digest)}.unreadable").read_text(encoding="utf-8") == "{"
    assert len(voice.requests) == 1


@pytest.mark.usefixtures("free_voice")
def test_a_take_a_free_voice_makes_is_written_through_to_the_store_too(
    make_run: Callable[..., Watched], tmp_path: Path, counted: CountedVoice
) -> None:
    a = in_the_store(tmp_path, "a")
    narrate(a, make_run(a).run)
    assert len(counted.voice.requests) == 3
    assert take_files(tmp_path / STORED) == take_files(a.workspace.takes)


# ---- the voices a run carries -------------------------------------------------------------------


def the_shipped_voice_is_never_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make building the real ElevenLabs provider fail the test, by whichever path it is built."""

    def refuse(*_args: object) -> None:
        raise AssertionError("the shipped ElevenLabs voice was built")

    monkeypatch.setattr(ElevenLabs, "__init__", refuse)


def test_a_paid_run_buys_through_the_voices_its_machine_holds_and_never_the_shipped_one(
    make_inputs: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run opened outside `Machine._run` still answers with its machine's table, so no host is billed elsewhere."""
    the_shipped_voice_is_never_built(monkeypatch)
    house = CountedVoice()
    project = make_inputs()
    run = a_voiced_run(project.root, {"elevenlabs": house}, spend=True)
    result = narrate(project, run)
    assert result.ok
    assert house.built == 1
    assert len(house.voice.requests) == 3


PAUSED = """## 1. Open

A bowl. [pause 2] A ball.

## 2. Middle

It steps down [beat] the bowl.

## 3. Close

Every picture waited for its word.
"""
"""A script with one timed pause in section 1 and one beat in section 2."""


def on_elevenlabs(make_inputs: Callable[..., Inputs], model: str) -> Inputs:
    """The project read by the shipped provider's name on `model`, which the test then stands a fake voice in for."""
    toml = TOML.replace("[elevenlabs]", f'[elevenlabs]\nmodel = "{model}"')
    return make_inputs(toml=toml, script=PAUSED, name=model)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_request_carries_each_paragraph_and_its_pause_and_no_markup(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    project = make_inputs(script=PAUSED)
    narrate(project, make_run(project, spend=True).run)
    sent: dict[str, SpeechRequest] = {request.pieces[0].text: request for request in fake_voice.requests}
    assert [(piece.text, piece.pause) for piece in sent["A bowl."].pieces] == [("A bowl.", 2.0), ("A ball.", None)]
    assert [(piece.text, piece.pause) for piece in sent["It steps down"].pieces] == [
        ("It steps down", 0.0),
        ("the bowl.", None),
    ]
    assert not any("<" in piece.text for request in fake_voice.requests for piece in request.pieces)


@pytest.mark.usefixtures("fake_ffmpeg")
@pytest.mark.parametrize("model", ["eleven_v3", "eleven_v4"])
def test_a_model_that_drops_a_timed_pause_is_refused_before_anything_is_bought(
    make_inputs: Callable[..., Inputs],
    make_run: Callable[..., Watched],
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    voice = FakeVoice()
    monkeypatch.setitem(PROVIDERS, "elevenlabs", lambda _context: voice)
    project = on_elevenlabs(make_inputs, model)
    with pytest.raises(InputError) as caught:
        narrate(project, make_run(project, spend=True).run)
    assert voice.requests == []
    assert model in str(caught.value) and "[1]" in str(caught.value)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_model_that_renders_a_timed_pause_buys_every_section(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = FakeVoice()
    monkeypatch.setitem(PROVIDERS, "elevenlabs", lambda _context: voice)
    project = on_elevenlabs(make_inputs, "eleven_multilingual_v2")
    narrate(project, make_run(project, spend=True).run)
    assert len(voice.requests) == 3


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_model_that_drops_a_timed_pause_still_buys_a_section_without_one(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal reads only the sections the run would buy, so a timed pause elsewhere never stops it."""
    voice = FakeVoice()
    monkeypatch.setitem(PROVIDERS, "elevenlabs", lambda _context: voice)
    project = on_elevenlabs(make_inputs, "eleven_v3")
    narrate(project, make_run(project, spend=True).run, only=[2])
    assert [[piece.text for piece in request.pieces] for request in voice.requests] == [["It steps down", "the bowl."]]


# ---- the voice id ----------------------------------------------------------------------------

NAMED = TOML.replace("[voice]\n", f'[voice]\nid = "{VOICE_ID}"\n')
"""The test project with its voice named in `decktalk.toml`, which is where the published name lives."""

KEY_ONLY = {KEY: "key-under-test"}
"""A machine that holds the credential and names no voice, so the project's own file has to."""


def test_the_voice_id_in_decktalk_toml_names_the_voice(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    project = make_inputs(toml=NAMED)
    on_a_machine = Inputs.load(project.root, environ=KEY_ONLY)
    narrate(on_a_machine, make_run(on_a_machine, spend=True).run)
    assert {request.voice_id for request in fake_voice.requests} == {VOICE_ID}


def test_decktalk_voice_id_in_the_environment_overrides_the_file(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    """Somebody who keeps the id out of the file exports it, and the export wins over a committed one."""
    project = make_inputs(toml=NAMED)
    private = Inputs.load(project.root, environ={**KEY_ONLY, "DECKTALK_VOICE_ID": "kept-private"})
    narrate(private, make_run(private, spend=True).run)
    assert {request.voice_id for request in fake_voice.requests} == {"kept-private"}


@pytest.mark.usefixtures("fake_voice")
def test_elevenlabs_voice_id_alone_no_longer_names_the_voice(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched]
) -> None:
    """The old variable is read by nothing, so a voiced run with only it is refused naming both places to name it."""
    project = make_inputs()
    old = Inputs.load(project.root, environ={**KEY_ONLY, "ELEVENLABS_VOICE_ID": VOICE_ID})
    with pytest.raises(InputError) as refused:
        narrate(old, make_run(old, spend=True).run)
    assert "[voice] id" in str(refused.value) and "DECKTALK_VOICE_ID" in str(refused.value)


def test_a_clone_that_commits_its_voice_id_and_its_takes_plays_them_with_no_environment(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], counted: CountedVoice, tmp_path: Path
) -> None:
    """With `[voice] id` committed beside `takes_dir`, a fresh clone matches every voiced take and misses none."""
    bought = make_inputs(toml=NAMED.replace("[narration]\n", '[narration]\ntakes_dir = "voice"\n'))
    narrate(bought, make_run(bought, spend=True).run)
    clone = tracked_copy(bought.root, tmp_path / "clone")
    environ = Recorded({})
    fresh = Inputs.load(clone, environ=environ)
    result = narrate(fresh, make_run(fresh).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert missing_sections(result) == []
    assert result.findings == ()
    assert counted.built == 1
    assert KEY not in environ.read


UNREADABLE = {"corrupt": "{not json", "older-shape": '{"version": 1, "sections": []}'}
"""Two take indexes that do not read: one broken mid-write by hand, and one an older release wrote."""


@pytest.mark.parametrize("spend", [False, True], ids=["no-spend", "spend"])
@pytest.mark.parametrize("toml", [TOML, IN_THE_PROJECT], ids=["build", "takes-dir"])
@pytest.mark.parametrize("written", list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_a_take_index_that_does_not_read_after_a_voiced_run_is_rebuilt_and_buys_nothing(
    make_inputs: Callable[..., Inputs],
    make_run: Callable[..., Watched],
    counted: CountedVoice,
    spend: bool,
    toml: str,
    written: str,
) -> None:
    """The take index is a cache over the takes on disk, so losing it costs no request and changes no row."""
    project = make_inputs(toml=toml)
    narrate(project, make_run(project, spend=True).run)
    bought = Takes.read(project.workspace.takes_path)
    sent = len(counted.voice.requests)
    project.workspace.takes_path.write_text(written, encoding="utf-8")
    result = narrate(project, make_run(project, spend=spend).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.findings == ()
    assert result.cost.dollars == 0
    assert len(counted.voice.requests) == sent, "a take index that did not read bought a take again"
    assert Takes.read(project.workspace.takes_path) == bought, "the rebuilt index differs from the one it replaced"


@pytest.mark.parametrize("spend", [False, True], ids=["no-spend", "spend"])
def test_a_take_index_whose_rows_name_the_take_hash_is_rebuilt_with_its_digest(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], counted: CountedVoice, spend: bool
) -> None:
    """An older index names each take `hash`, and a cache in an older shape is rebuilt, never refused."""
    project = make_inputs(toml=IN_THE_PROJECT)
    narrate(project, make_run(project, spend=True).run)
    bought = Takes.read(project.workspace.takes_path)
    sent = len(counted.voice.requests)
    rows = json.loads(project.workspace.takes_path.read_text(encoding="utf-8"))
    rows["sections"] = [
        {("hash" if name == "digest" else name): value for name, value in row.items()} for row in rows["sections"]
    ]
    project.workspace.takes_path.write_text(json.dumps(rows), encoding="utf-8")
    result = narrate(project, make_run(project, spend=spend).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert result.findings == ()
    assert len(counted.voice.requests) == sent, "an index in an older shape bought a take again"
    written = json.loads(project.workspace.takes_path.read_text(encoding="utf-8"))
    assert all("digest" in row and "hash" not in row for row in written["sections"])
    assert Takes.read(project.workspace.takes_path) == bought


def test_a_take_index_that_does_not_read_with_no_voice_named_buys_nothing(
    inputs: Inputs, make_run: Callable[..., Watched], counted: CountedVoice
) -> None:
    """Without the voice id no take on disk can be matched, so a run that may spend is refused rather than buying."""
    narrate(inputs, make_run(inputs, spend=True).run)
    sent = len(counted.voice.requests)
    inputs.workspace.takes_path.write_text(UNREADABLE["corrupt"], encoding="utf-8")
    nameless = Inputs.load(inputs.root, environ=KEY_ONLY)
    with pytest.raises(InputError) as refused:
        narrate(nameless, make_run(nameless, spend=True).run)
    assert VOICE_ID_VARIABLE in str(refused.value)
    assert len(counted.voice.requests) == sent
    result = narrate(nameless, make_run(nameless).run)
    assert missing_sections(result) == [1, 2, 3]
    assert len(counted.voice.requests) == sent
    named = narrate(inputs, make_run(inputs).run)
    assert {row.outcome for row in named.sections} == {TakeOutcome.KEPT}, "the voiced takes stayed on disk"


@pytest.mark.parametrize("written", list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_a_placeholder_only_take_index_that_does_not_read_is_rebuilt_not_refused(
    inputs: Inputs, make_run: Callable[..., Watched], written: str
) -> None:
    """A placeholder costs nothing, and the index over them is a cache like the placeholders themselves."""
    narrate(inputs, make_run(inputs).run)
    before = Takes.read(inputs.workspace.takes_path)
    inputs.workspace.takes_path.write_text(written, encoding="utf-8")
    result = narrate(inputs, make_run(inputs).run)
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    assert missing_sections(result) == [1, 2, 3]
    assert Takes.read(inputs.workspace.takes_path) == before
