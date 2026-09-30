"""Stage one: the script becomes one take per section, indexed by content hash."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from decktalk.artifacts import Takes, take_file, words_file
from decktalk.errors import ApprovalRequired, InputError, ProviderError
from decktalk.events import Unit
from decktalk.inputs import Inputs
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, SpendState, TakeStatus, Voicing, Word
from decktalk.speech import PROVIDERS, SpeechRequest
from decktalk.stages.narrate import narrate
from support.runs import Watched

from .conftest import ENVIRON, SCRIPT, TOML


@pytest.fixture(autouse=True)
def encoder(fake_ffmpeg: object) -> object:
    """Every narrate test writes audio, and none of them may run ffmpeg to do it."""
    return fake_ffmpeg


def placeholder(inputs: Inputs, watched: Watched, **options: object) -> NarrateResult:
    return narrate(inputs, watched.run, **options)  # type: ignore[arg-type]


def test_a_run_without_voice_writes_a_take_for_every_spoken_section(inputs: Inputs, watched: Watched) -> None:
    result = placeholder(inputs, watched)
    assert isinstance(result, NarrateResult)
    assert result.voice is Voicing.PLACEHOLDER
    assert [row.section for row in result.sections] == [1, 2, 3]
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}
    assert result.ok is True
    assert result.findings == ()


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
    inputs: Inputs, watched: Watched, caplog: pytest.LogCaptureFixture
) -> None:
    def said() -> list[tuple[int, bool, str]]:
        rows = [record.data for record in caplog.records if getattr(record, "data", {}).get("cache") == "take"]
        caplog.clear()
        return [(row["section"], row["hit"], row["why"]) for row in rows]

    with caplog.at_level("DEBUG", logger="decktalk"):
        placeholder(inputs, watched)
        workers = [record.data for record in caplog.records if "workers" in getattr(record, "data", {})]
        assert workers and workers[0]["jobs"] == 3
        assert sorted(said()) == [(1, False, "to-make"), (2, False, "to-make"), (3, False, "to-make")]
        placeholder(inputs, watched)
        assert sorted(said()) == [(1, True, "unchanged"), (2, True, "unchanged"), (3, True, "unchanged")]


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
    lines = watched.of("progress")
    assert [line.done for line in lines] == [1, 2, 3]  # type: ignore[attr-defined]
    assert {line.total for line in lines} == {3}  # type: ignore[attr-defined]
    assert {line.unit for line in lines} == {Unit.TAKE}  # type: ignore[attr-defined]
    assert {line.stage for line in lines} == {Stage.NARRATE}  # type: ignore[attr-defined]
    assert [line.section for line in watched.of("section.start")] == [1, 2, 3]  # type: ignore[attr-defined]


def test_a_paid_run_sends_one_request_per_section_and_reports_what_it_charged(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    watched = make_run(inputs, voice=Voicing.PAID)
    result = narrate(inputs, watched.run)
    assert len(fake_voice.requests) == 3  # type: ignore[attr-defined]
    assert {row.status for row in result.sections} == {TakeStatus.VOICED}
    assert result.spend.state is SpendState.CHARGED
    assert result.spend.sections == (1, 2, 3)
    assert result.spend.dollars > 0
    charged = watched.of("take.charged")
    assert sorted(line.section for line in charged) == [1, 2, 3]  # type: ignore[attr-defined]
    assert sum(line.characters for line in charged) == result.spend.characters  # type: ignore[attr-defined]


def test_a_take_the_run_found_on_disk_is_never_charged(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    """A ledger counts what was bought, so a take the cache answered puts no charge on the stream."""
    assert fake_voice is not None
    narrate(inputs, make_run(inputs, voice=Voicing.PAID).run)
    again = make_run(inputs, voice=Voicing.PAID)
    narrate(inputs, again.run)
    assert again.of("take.charged") == []


def test_a_paid_request_carries_the_published_voice_and_its_neighbours(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: object
) -> None:
    project = one_at_a_time(make_inputs)
    narrate(project, make_run(project, voice=Voicing.PAID).run)
    sent: list[SpeechRequest] = fake_voice.requests  # type: ignore[attr-defined]
    assert {request.voice_id for request in sent} == {"voice-under-test"}
    assert sent[0].previous_text is None
    assert sent[0].next_text == "It steps down the bowl."
    assert sent[-1].next_text is None


def test_the_price_is_approved_before_anything_is_sent(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    watched = make_run(inputs, voice=Voicing.PAID)
    narrate(inputs, watched.run)
    priced = watched.of("spend")
    assert priced
    assert priced[0].spend.state is SpendState.ESTIMATE  # type: ignore[attr-defined]
    assert fake_voice.requests  # type: ignore[attr-defined]


def test_a_run_over_its_ceiling_buys_nothing(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    watched = make_run(inputs, voice=Voicing.PAID, max_cost=0.001)
    with pytest.raises(ApprovalRequired):
        narrate(inputs, watched.run)
    assert fake_voice.requests == []  # type: ignore[attr-defined]
    assert not inputs.workspace.takes_path.exists()


def test_a_run_without_voice_refuses_to_replace_a_paid_take(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    assert fake_voice is not None
    narrate(inputs, make_run(inputs, voice=Voicing.PAID).run)
    with pytest.raises(InputError) as refused:
        narrate(inputs, make_run(inputs).run)
    assert "buy all of them again" in str(refused.value)
    assert refused.value.hint is not None
    assert "--replace-voiced" in refused.value.hint


def test_a_run_told_to_replace_a_paid_take_does(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    assert fake_voice is not None
    narrate(inputs, make_run(inputs, voice=Voicing.PAID).run)
    result = narrate(inputs, make_run(inputs).run, replace_voiced=True)
    assert {row.status for row in result.sections} == {TakeStatus.PLACEHOLDER}


def test_a_run_over_the_sections_nobody_paid_for_is_the_cheap_rehearsal(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    assert fake_voice is not None
    narrate(inputs, make_run(inputs, voice=Voicing.PAID).run, only=[1])
    result = narrate(inputs, make_run(inputs).run, only=[2, 3])
    assert [row.section for row in result.sections] == [2, 3]


class RefusesOneSection:
    """A voice that answers every section but one, which it refuses as a busy provider would."""

    name = "test-voice"

    def __init__(self, refused: str) -> None:
        self.refused = refused

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        if request.text == self.refused:
            raise ProviderError("the voice stopped answering.", retryable=True)
        return b"take", [Word(word="A", start=0.0, end=0.4)]

    def cache_key(self, _request: SpeechRequest) -> str:
        return self.name


def test_the_index_is_checkpointed_after_every_take(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that fails keeps every take it has already paid for, so the next run reuses them."""
    (second,) = [segment for segment in inputs.spoken() if segment.index == 2]
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: RefusesOneSection(second.tts_text))
    watched = make_run(inputs, voice=Voicing.PAID)
    with pytest.raises(ProviderError):
        narrate(inputs, watched.run)
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    indexed = [row.section for row in index.sections]
    assert 1 in indexed
    assert 2 not in indexed
    # Every take that was bought is on the stream, even though the run failed after it.
    assert sorted(line.section for line in watched.of("take.charged")) == indexed  # type: ignore[attr-defined]


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
    watched = make_run(inputs, voice=Voicing.PAID)
    result = narrate(inputs, watched.run)
    assert voice.most == 2
    assert [row.section for row in result.sections] == [1, 2, 3]
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1, 2, 3]
    assert [line.done for line in watched.of("progress")] == [1, 2, 3]  # type: ignore[attr-defined]


def test_a_concurrency_of_one_voices_one_section_at_a_time(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = Overlapping(hold=0)
    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: voice)
    project = one_at_a_time(make_inputs)
    narrate(project, make_run(project, voice=Voicing.PAID).run)
    assert voice.most == 1


def test_two_sections_with_the_same_words_buy_one_take_under_a_pool(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: object
) -> None:
    """The section that shares its words is indexed after the pool, so it reads the take and buys none."""
    doubled = make_inputs(script="## 1. Open\n\nA bowl.\n\n## 2. Middle\n\nA bowl.\n\n## 3. Close\n\nA ball.\n")
    watched = make_run(doubled, voice=Voicing.PAID)
    result = narrate(doubled, watched.run)
    assert len(fake_voice.requests) == 2  # type: ignore[attr-defined]
    assert [row.status for row in result.sections] == [TakeStatus.VOICED, TakeStatus.KEPT, TakeStatus.VOICED]
    assert sorted(line.section for line in watched.of("take.charged")) == [1, 3]  # type: ignore[attr-defined]


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


def test_every_section_that_failed_is_recorded_and_the_first_is_raised(caplog: pytest.LogCaptureFixture) -> None:
    """A paid request that failed after the first failure was lost, although it may have been charged."""
    from types import SimpleNamespace  # noqa: PLC0415

    from decktalk.stages.narrate import _in_pool  # noqa: PLC0415

    both_sent = threading.Barrier(2)

    def work(plan: SimpleNamespace) -> None:
        both_sent.wait(timeout=5)
        if plan.segment.index == 2:
            threading.Event().wait(0.1)
        raise ProviderError(f"section {plan.segment.index} failed")

    plans = [SimpleNamespace(segment=SimpleNamespace(index=number)) for number in (1, 2)]
    with caplog.at_level("DEBUG", logger="decktalk"), pytest.raises(ProviderError, match="section 1"):
        _in_pool(work, plans, workers=2)  # type: ignore[arg-type]
    later = [record for record in caplog.records if record.levelname == "WARNING"]
    assert [record.data["section"] for record in later] == [2]  # type: ignore[attr-defined]
