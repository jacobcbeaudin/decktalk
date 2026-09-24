"""Stage one: the script becomes one take per section, indexed by content hash."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.artifacts import Takes, take_file, words_file
from decktalk.errors import ApprovalRequired, InputError, ProviderError
from decktalk.events import Unit
from decktalk.inputs import Inputs
from decktalk.media import audio
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, SpendState, TakeStatus, Voicing, Word
from decktalk.speech import PROVIDERS, SpeechRequest
from decktalk.stages.narrate import narrate

from .conftest import SCRIPT, TOML, Watched


@pytest.fixture(autouse=True)
def quiet_sound_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake encoder writes an empty file, so where a take's sound ends is answered at the seam."""
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)


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


def test_the_run_reports_one_take_at_a_time(inputs: Inputs, watched: Watched) -> None:
    placeholder(inputs, watched)
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


def test_a_paid_request_carries_the_published_voice_and_its_neighbours(
    inputs: Inputs, make_run: Callable[..., Watched], fake_voice: object
) -> None:
    narrate(inputs, make_run(inputs, voice=Voicing.PAID).run)
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


def test_the_index_is_checkpointed_after_every_take(
    inputs: Inputs, make_run: Callable[..., Watched], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that is stopped keeps every take it has already paid for, so the next run reuses them."""

    class RefusesTheSecond:
        name = "test-voice"

        def __init__(self) -> None:
            self.sent = 0

        def speak(self, _request: SpeechRequest) -> tuple[bytes, list[Word]]:
            self.sent += 1
            if self.sent > 1:
                raise ProviderError("the voice stopped answering.", retryable=True)
            return b"take", [Word(word="A", start=0.0, end=0.4)]

        def cache_key(self, _request: SpeechRequest) -> str:
            return self.name

    monkeypatch.setitem(PROVIDERS, "test-voice", lambda _context: RefusesTheSecond())
    with pytest.raises(ProviderError):
        narrate(inputs, make_run(inputs, voice=Voicing.PAID).run)
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    assert [row.section for row in index.sections] == [1]


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
