"""Timing the words of a take whose voice sent none, through the aligner, after every take is made.

Every test runs the real `narrate` against a voice that sends no word times, registered in the
table the test's runs are opened with, and an aligner in this process registered the same way. No
test reaches a socket but the last, which drives the real client against the fake decktalk-voice.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import (
    AlignedWords,
    AudioPrint,
    ProviderWords,
    Take,
    Takes,
    TimedBy,
    TurnKey,
    take_file,
    words_file,
)
from decktalk.errors import Cancelled, ProviderError
from decktalk.events import RunLog, StageProgress, TakeCharged
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.inputs.script import spoken_words
from decktalk.media.audio import PCM_BYTES_PER_SAMPLE
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, Word
from decktalk.settings import BY_ID
from decktalk.speech import (
    ALIGNER,
    ALIGNERS,
    PROVIDERS,
    Aligner,
    AlignerContext,
    AlignerIdentity,
    AlignmentRequest,
    SpeechFactory,
)
from decktalk.speech import http as speech_http
from decktalk.stages.build import build
from decktalk.stages.cue import cue
from decktalk.stages.narrate import narrate
from decktalk.stages.narrate.align import first_words
from support.fakes import FAKE_VOICE_NAME, FREE_VOICE_NAME, FakeAligner, FakeFfmpeg, FakeVoice, refusing_aligners
from support.projects import load_project
from support.runs import Watched
from support.takes import TAKE_SUFFIX
from support.voice_server import VoiceServer

from .conftest import DIALOGUE, ENVIRON, FREE_TOML, SCRIPT, TOML, WITH_A_GUEST

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every test here writes audio, and none of them may run ffmpeg to do it."""

VOICED = "0000000000000abc"
"""A voiced take's digest, held in the takes directory by the tests that read its words."""

SERVED = "sha256:5eed"
"""The model the word-less voice says it served every take with."""

Aligners = dict[str, Callable[[AlignerContext], Aligner]]


@pytest.fixture
def inputs(make_inputs: Callable[..., Inputs]) -> Inputs:
    """The project read by the free voice, which makes every take with or without spending."""
    return make_inputs(toml=FREE_TOML)


@pytest.fixture
def wordless(voices: dict[str, SpeechFactory]) -> FakeVoice:
    """A free voice that sends no word times with any take, and names the model it served."""
    voice = FakeVoice(name=FREE_VOICE_NAME, words=None, model_digest=SERVED)
    voices[FREE_VOICE_NAME] = lambda _context: voice
    return voice


@pytest.fixture
def aligner(aligners: Aligners) -> FakeAligner:
    """An aligner in this process, registered under the shipped aligner's name."""
    fake = FakeAligner()
    aligners[ALIGNER] = fake.build
    return fake


def rows(inputs: Inputs) -> dict[int, Take]:
    index = Takes.read(inputs.workspace.takes_path)
    assert index is not None
    return {row.section: row for row in index.sections}


def kept(inputs: Inputs) -> list[Path]:
    return sorted(inputs.workspace.aligned_dir.glob("*.words.json"))


def unaligned(result: NarrateResult) -> list[int | None]:
    return [found.location.section for found in result.findings if found.code is Code.TAKE_UNALIGNED]


def said(watched: Watched) -> str:
    return "\n".join(line.message for line in watched.of(RunLog))


def no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test the moment anything opens a connection."""

    def refused(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a run whose every take has kept aligned words opened a socket")

    monkeypatch.setattr(speech_http, "urlopen", refused)


def down(message: str = "could not reach http://127.0.0.1:9361/v1/models: Connection refused") -> ProviderError:
    return ProviderError(message, reached=False)


# ---- the words a take plays first ----------------------------------------------------------------


def test_a_take_with_its_own_words_plays_them_first(inputs: Inputs) -> None:
    own = ProviderWords(words=(Word(word="A", start=0.0, end=0.4),), audio=AudioPrint.of(b"take", suffix=TAKE_SUFFIX))
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file(VOICED, TAKE_SUFFIX)).write_bytes(b"take")
    own.write(inputs.workspace.takes / words_file(VOICED))
    assert first_words(inputs, "A.", VOICED) == (own, own)


def test_a_take_with_its_own_words_never_builds_an_aligner(
    inputs: Inputs, watched: Watched, voices: dict[str, SpeechFactory]
) -> None:
    """The aligners refuse to be built, so a run that asked for one would fail here."""
    voice = FakeVoice(name=FREE_VOICE_NAME)
    voices[FREE_VOICE_NAME] = lambda _context: voice
    result = narrate(inputs, watched.run)
    assert result.ok and unaligned(result) == []
    assert {row.timed_by for row in rows(inputs).values()} == {TimedBy.PROVIDER}


# ---- an aligner that answers ----------------------------------------------------------------------


@pytest.mark.usefixtures("wordless")
def test_a_miss_aligns_once_and_keeps_the_words_in_the_takes_directory(
    inputs: Inputs, watched: Watched, aligner: FakeAligner
) -> None:
    result = narrate(inputs, watched.run)
    assert result.ok and unaligned(result) == []
    assert len(aligner.contexts) == 1 and len(aligner.requests) == 3
    index = rows(inputs)
    assert [request.words for request in aligner.requests] == [spoken_words(index[n].spoken) for n in (1, 2, 3)]
    assert {path.name.removesuffix(".words.json") for path in kept(inputs)} == {row.alignment for row in index.values()}
    for row in index.values():
        assert (row.timed_by, row.model_digest) == (TimedBy.ALIGNED, SERVED)
    assert type(inputs.words(1)) is AlignedWords
    assert kept(inputs)[0].is_relative_to(inputs.workspace.takes / "aligned")


@pytest.mark.usefixtures("wordless")
def test_a_run_whose_every_take_has_kept_aligned_words_opens_no_socket(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner, aligners: Aligners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    narrate(inputs, make_run(inputs).run)
    aligners.update(refusing_aligners())
    no_socket(monkeypatch)
    again = narrate(inputs, make_run(inputs).run)
    assert again.ok and unaligned(again) == []
    assert {row.timed_by for row in rows(inputs).values()} == {TimedBy.ALIGNED}
    assert len(aligner.requests) == 3


@pytest.mark.usefixtures("wordless")
def test_a_build_with_every_take_kept_opens_no_socket(
    inputs: Inputs, make_run: Callable[..., Watched], aligners: Aligners, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAligner()
    aligners[ALIGNER] = fake.build
    narrate(inputs, make_run(inputs).run)
    aligners.update(refusing_aligners())
    no_socket(monkeypatch)
    built = build(inputs, make_run(inputs).run, stages=[Stage.NARRATE, Stage.CUE])
    assert built.ok
    assert {row.timed_by for row in rows(inputs).values()} == {TimedBy.ALIGNED}


@pytest.mark.usefixtures("wordless", "aligner")
def test_a_run_with_the_server_down_and_every_take_kept_prints_no_note(
    inputs: Inputs, make_run: Callable[..., Watched], aligners: Aligners
) -> None:
    narrate(inputs, make_run(inputs).run)
    aligners.update(refusing_aligners())
    watched = make_run(inputs)
    again = narrate(inputs, watched.run)
    assert unaligned(again) == []
    assert "aligner" not in said(watched)


@pytest.mark.usefixtures("wordless")
def test_a_run_that_aligns_one_take_checks_every_take_against_the_aligner_it_met(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], aligners: Aligners
) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    first = FakeAligner()
    aligners[ALIGNER] = first.build
    narrate(inputs, make_run(inputs).run)
    newer = FakeAligner(identity=AlignerIdentity(id="fake-aligner", revision="2", model_digest="m2"))
    aligners[ALIGNER] = newer.build
    edited = make_inputs(toml=FREE_TOML, script=SCRIPT.replace("Every picture", "Each picture"))
    result = narrate(edited, make_run(edited).run)
    assert result.ok and unaligned(result) == []
    assert len(newer.requests) == 3
    assert len(kept(edited)) == 6
    identities = {row.alignment.split("-")[1] for row in rows(edited).values() if row.alignment}
    assert len(identities) == 1


@pytest.mark.usefixtures("wordless")
def test_force_asks_the_aligner_again_and_aligns_only_what_its_identity_moved(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner, aligners: Aligners
) -> None:
    narrate(inputs, make_run(inputs).run)
    narrate(inputs, make_run(inputs).run, force=True)
    assert len(aligner.contexts) == 2 and len(aligner.requests) == 3
    moved = FakeAligner(identity=AlignerIdentity(id="fake-aligner", revision="2", model_digest="m"))
    aligners[ALIGNER] = moved.build
    narrate(inputs, make_run(inputs).run, force=True)
    assert len(moved.requests) == 3


@pytest.mark.usefixtures("wordless")
def test_the_alignment_pass_runs_after_every_take_is_made(
    inputs: Inputs, watched: Watched, aligner: FakeAligner
) -> None:
    on_disk: list[int] = []

    def count(_request: AlignmentRequest) -> None:
        on_disk.append(len(list(inputs.workspace.takes.glob(f"*{TAKE_SUFFIX}"))))

    aligner.on_align = count
    narrate(inputs, watched.run)
    assert on_disk == [3, 3, 3]


@pytest.mark.usefixtures("wordless")
def test_a_cancel_between_alignments_keeps_what_was_aligned(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner
) -> None:
    watched = make_run(inputs)
    aligner.on_align = lambda _request: watched.run.cancel.cancel()
    with pytest.raises(Cancelled):
        narrate(inputs, watched.run)
    assert len(kept(inputs)) == 1
    assert [row.timed_by for row in rows(inputs).values()] == [TimedBy.ESTIMATED] * 3
    aligner.on_align = None
    narrate(inputs, make_run(inputs).run)
    assert len(aligner.requests) == 1 + 2


@pytest.mark.usefixtures("wordless")
def test_an_interrupt_during_an_alignment_stops_it_and_keeps_every_alignment_written_before(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner
) -> None:
    def second_stops(_request: AlignmentRequest) -> None:
        if len(aligner.requests) == 2:
            raise KeyboardInterrupt

    aligner.on_align = second_stops
    with pytest.raises(Cancelled):
        narrate(inputs, make_run(inputs).run)
    assert len(kept(inputs)) == 1
    aligner.on_align = None
    again = narrate(inputs, make_run(inputs).run)
    assert again.ok and len(aligner.requests) == 2 + 2
    assert {row.timed_by for row in rows(inputs).values()} == {TimedBy.ALIGNED}


@pytest.mark.usefixtures("wordless")
@pytest.mark.parametrize("moved", [{"revision": "2"}, {"model_digest": "sha256:feed"}])
def test_a_new_revision_or_model_aligns_every_section_again_and_changes_no_take(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner, aligners: Aligners,
    wordless: FakeVoice, moved: dict[str, str],
) -> None:  # fmt: skip
    narrate(inputs, make_run(inputs).run)
    digests = {n: row.digest for n, row in rows(inputs).items()}
    voiced = len(wordless.requests)
    newer = FakeAligner(identity=AlignerIdentity(**{**aligner.identity.__dict__, **moved}))
    aligners[ALIGNER] = newer.build
    narrate(inputs, make_run(inputs).run, force=True)
    assert len(newer.requests) == 3
    assert {n: row.digest for n, row in rows(inputs).items()} == digests
    assert len(wordless.requests) == voiced
    assert len(kept(inputs)) == 6


@pytest.mark.usefixtures("wordless", "aligner")
@pytest.mark.parametrize("fault", ["renamed", "other-words", "respelled"])
def test_aligned_words_filed_under_another_key_are_not_played(
    inputs: Inputs, make_run: Callable[..., Watched], aligners: Aligners, fault: str
) -> None:
    narrate(inputs, make_run(inputs).run)
    path = inputs.workspace.aligned_words(rows(inputs)[1].alignment or "")
    held = AlignedWords.read(path)
    assert held is not None
    if fault == "renamed":
        path.rename(path.with_name(f"{held.inputs.match}-{'0' * 16}.words.json"))
    elif fault == "other-words":
        other = held.inputs.model_copy(update={"words": ("Another", "bowl")})
        words = tuple(Word(word=w, start=0.0, end=0.1) for w in other.words)
        AlignedWords(words=words, inputs=other, generation=1).write(path)
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        record["words"][0]["word"] = "Respelled"
        path.write_text(json.dumps(record), encoding="utf-8")
    aligners[ALIGNER] = FakeAligner(handshake=down()).build
    result = narrate(inputs, make_run(inputs).run)
    assert unaligned(result) == [1]
    assert rows(inputs)[1].timed_by is TimedBy.ESTIMATED
    assert rows(inputs)[2].timed_by is TimedBy.ALIGNED


@pytest.mark.usefixtures("wordless")
def test_an_aligner_still_loading_after_its_retries_plays_estimated_words_with_a_warning(
    inputs: Inputs, watched: Watched, aligners: Aligners
) -> None:
    loading = ProviderError(
        'HTTP 503 from http://127.0.0.1:9361/v1/align: {"error": {"code": "MODEL_LOADING", '
        '"message": "still loading"}}',
        retryable=True,
    )
    aligners[ALIGNER] = FakeAligner(fails=loading).build
    result = narrate(inputs, watched.run)
    assert result.ok and unaligned(result) == [1, 2, 3]
    (first, *_rest) = [found for found in result.findings if found.code is Code.TAKE_UNALIGNED]
    assert "still loading" in first.message
    assert "Run decktalk narrate --section 1 again once the aligner answers." in first.message
    assert {row.timed_by for row in rows(inputs).values()} == {TimedBy.ESTIMATED}


@pytest.mark.usefixtures("wordless", "aligner")
def test_aligning_reports_progress_for_every_take_it_aligns(inputs: Inputs, watched: Watched) -> None:
    narrate(inputs, watched.run)
    aligning = [line for line in watched.of(StageProgress) if line.label.startswith("aligning ")]
    assert [(line.done, line.total, line.section) for line in aligning] == [(1, 3, 1), (2, 3, 2), (3, 3, 3)]


@pytest.mark.usefixtures("wordless")
def test_a_take_two_sections_share_is_aligned_once(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], aligner: FakeAligner
) -> None:
    shared = make_inputs(
        toml=FREE_TOML, script=SCRIPT.replace("Every picture waited for its word.", "It steps down the bowl.")
    )
    narrate(shared, make_run(shared).run)
    index = rows(shared)
    assert len(aligner.requests) == 2
    assert index[2].alignment == index[3].alignment is not None


def realign(inputs: Inputs, make_run: Callable[..., Watched], aligners: Aligners, revision: str) -> FakeAligner:
    """Align every take again with an aligner of `revision`, under `--force`."""
    fake = FakeAligner(
        identity=FakeAligner().identity.__class__(id="fake-aligner", revision=revision, model_digest="m")
    )
    aligners[ALIGNER] = fake.build
    narrate(inputs, make_run(inputs).run, force=True)
    return fake


@pytest.mark.usefixtures("wordless")
def test_with_the_aligner_down_the_alignment_the_film_last_played_plays_and_a_miss_warns(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], aligners: Aligners
) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    realign(inputs, make_run, aligners, "a")
    realign(inputs, make_run, aligners, "b")
    realign(inputs, make_run, aligners, "a")
    played = rows(inputs)[1].alignment
    aligners[ALIGNER] = FakeAligner(handshake=down()).build
    edited = make_inputs(toml=FREE_TOML, script=SCRIPT.replace("Every picture", "Each picture"))
    result = narrate(edited, make_run(edited).run)
    assert rows(edited)[1].alignment == played
    assert unaligned(result) == [3]
    assert result.ok


@pytest.mark.usefixtures("wordless")
def test_with_the_aligner_down_and_no_row_the_highest_generation_plays_and_a_tie_is_named(
    inputs: Inputs, make_run: Callable[..., Watched], aligners: Aligners
) -> None:
    realign(inputs, make_run, aligners, "a")
    realign(inputs, make_run, aligners, "b")
    (newest,) = [
        held for path in kept(inputs) if (held := AlignedWords.read(path)) is not None
        and held.generation == 2 and held.inputs.words == spoken_words("A bowl. A ball.")
    ]  # fmt: skip
    tied = newest.inputs.model_copy(update={"aligner": "another-aligner"})
    AlignedWords(words=newest.words, inputs=tied, generation=2).write(inputs.workspace.aligned_words(tied.key))
    shutil.rmtree(inputs.workspace.build)
    aligners.update(refusing_aligners())
    watched = make_run(inputs)
    result = narrate(inputs, watched.run)
    assert result.ok and unaligned(result) == []
    assert rows(inputs)[1].alignment == min(newest.inputs.key, tied.key)
    assert "Section 1's take has aligned words from 2 aligners kept at one generation" in said(watched)
    assert "Remove the others from takes/aligned with git rm." in said(watched)


@pytest.mark.usefixtures("wordless")
def test_with_the_aligner_down_and_nothing_kept_the_take_plays_estimated_words_with_a_warning(
    inputs: Inputs, watched: Watched, aligners: Aligners
) -> None:
    aligners[ALIGNER] = FakeAligner(handshake=down()).build
    result = narrate(inputs, watched.run)
    assert result.ok and unaligned(result) == [1, 2, 3]
    first = next(found for found in result.findings if found.code is Code.TAKE_UNALIGNED)
    assert first.message == (
        "Section 1's take came with no word times, and could not reach http://127.0.0.1:9361/v1/models: "
        "Connection refused, so it plays estimated words. Start decktalk-voice at the address [dtsp] base_url "
        "names, then run decktalk narrate --section 1."
    )
    index = rows(inputs)
    assert all(row.voiced and row.timed_by is TimedBy.ESTIMATED for row in index.values())
    assert inputs.words(1).estimated is True


@pytest.mark.usefixtures("wordless")
def test_a_run_whose_aligner_is_down_asks_it_once(inputs: Inputs, watched: Watched, aligners: Aligners) -> None:
    fake = FakeAligner(down=True)
    aligners[ALIGNER] = fake.build
    result = narrate(inputs, watched.run)
    assert len(fake.contexts) == 1 and len(fake.requests) == 1
    assert unaligned(result) == [1, 2, 3]


@pytest.mark.usefixtures("wordless")
def test_an_aligner_that_dies_partway_is_down_for_every_take_after(
    inputs: Inputs, watched: Watched, aligner: FakeAligner
) -> None:
    def dies(_request: AlignmentRequest) -> None:
        if len(aligner.requests) == 2:
            aligner.down = True

    aligner.on_align = dies
    result = narrate(inputs, watched.run)
    assert len(aligner.requests) == 2
    assert unaligned(result) == [2, 3]
    assert [row.timed_by for row in rows(inputs).values()] == [TimedBy.ALIGNED, TimedBy.ESTIMATED, TimedBy.ESTIMATED]


@pytest.mark.usefixtures("wordless")
def test_an_aligner_that_answers_wrong_fails_the_run(inputs: Inputs, watched: Watched, aligners: Aligners) -> None:
    wrong = ProviderError("the aligner answered 1 words for the 3 it was sent.")
    aligners[ALIGNER] = FakeAligner(fails=wrong).build
    with pytest.raises(ProviderError, match="1 words for the 3"):
        narrate(inputs, watched.run)


@pytest.mark.usefixtures("wordless")
def test_aligned_words_that_do_not_read_are_aligned_again(
    inputs: Inputs, make_run: Callable[..., Watched], aligner: FakeAligner
) -> None:
    narrate(inputs, make_run(inputs).run)
    path = inputs.workspace.aligned_words(rows(inputs)[1].alignment or "")
    path.write_text("{not json", encoding="utf-8")
    narrate(inputs, make_run(inputs).run)
    assert len(aligner.requests) == 3 + 1
    assert AlignedWords.read(path) is not None


def test_a_paid_take_with_no_word_times_is_aligned_and_charged_once(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], voices: dict[str, SpeechFactory],
    aligners: Aligners,
) -> None:  # fmt: skip
    paid = make_inputs(toml=TOML)
    voice = FakeVoice(words=None)
    voices[FAKE_VOICE_NAME] = lambda _context: voice
    aligners[ALIGNER] = FakeAligner(handshake=down()).build
    watched = make_run(paid, spend=True)
    result = narrate(paid, watched.run)
    assert sorted(line.section for line in watched.of(TakeCharged)) == [1, 2, 3]
    assert unaligned(result) == [1, 2, 3]
    assert all("came with no word times" in found.message for found in result.findings)
    fake = FakeAligner()
    aligners[ALIGNER] = fake.build
    again = make_run(paid, spend=True)
    narrate(paid, again.run)
    assert again.of(TakeCharged) == [] and len(fake.requests) == 3


def test_the_whole_path_through_the_local_server(
    tmp_path: Path, voice_server: VoiceServer, voices: dict[str, SpeechFactory], aligners: Aligners,
    make_run: Callable[..., Watched], fake_ffmpeg: FakeFfmpeg,
) -> None:  # fmt: skip
    """The real voice and the real aligner, against the fake decktalk-voice, then `cue` on the words it timed."""
    voice_server.speech_words = False
    voice_server.model_digest = SERVED
    voices[FREE_VOICE_NAME] = PROVIDERS[FREE_VOICE_NAME]
    aligners[ALIGNER] = ALIGNERS[ALIGNER]
    environ = {**ENVIRON, BY_ID["dtsp.base_url"].environment: voice_server.base_url}
    cues = {"2": {"cues": [{"id": "2.1:a", "phrase": "down"}]}}
    inputs = load_project(tmp_path / "whole", FREE_TOML, script=SCRIPT, cues=cues, environ=environ)
    seconds = 2
    rate = inputs.settings.audio.sample_rate
    fake_ffmpeg.raw_bytes = bytes(PCM_BYTES_PER_SAMPLE * rate * seconds)
    result = narrate(inputs, make_run(inputs).run)
    assert result.ok and unaligned(result) == []
    assert voice_server.models_calls == 1 and voice_server.align_calls == 3
    index = rows(inputs)
    assert {(row.timed_by, row.model_digest) for row in index.values()} == {(TimedBy.ALIGNED, SERVED)}
    said = spoken_words(index[2].spoken)
    step = seconds / len(said)
    cued = cue(inputs, make_run(inputs).run)
    ((landed,),) = [section.cues for section in cued.sections if section.section == 2]
    assert [section.estimated for section in cued.sections] == [False]
    assert landed.seconds == pytest.approx(inputs.lead_seconds(2) + said.index("down") * step, abs=0.002)


@pytest.mark.usefixtures("wordless")
def test_an_aligner_that_answers_and_goes_down_names_the_sections_still_on_an_earlier_aligner(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], aligners: Aligners
) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    realign(inputs, make_run, aligners, "a")
    newer = FakeAligner(identity=AlignerIdentity(id="fake-aligner", revision="b", model_digest="m"), down=True)
    aligners[ALIGNER] = newer.build
    edited = make_inputs(toml=FREE_TOML, script=SCRIPT.replace("Every picture", "Each picture"))
    watched = make_run(edited)
    result = narrate(edited, watched.run)
    assert unaligned(result) == [3]
    assert "is fake-aligner b, and section(s) [1, 2] play words an earlier aligner measured" in said(watched)
    assert "Run decktalk narrate --force to align them again." in said(watched)


@pytest.mark.usefixtures("wordless")
def test_a_machine_with_no_aligner_plays_estimated_words_and_says_so(
    inputs: Inputs, watched: Watched, aligners: Aligners
) -> None:
    aligners.clear()
    result = narrate(inputs, watched.run)
    assert unaligned(result) == [1, 2, 3]
    assert "this machine answers for no aligner" in result.findings[0].message


@pytest.mark.usefixtures("wordless", "aligner")
def test_a_take_whose_words_file_carries_no_fingerprint_is_never_aligned(
    inputs: Inputs, make_run: Callable[..., Watched]
) -> None:
    narrate(inputs, make_run(inputs).run)
    digest = rows(inputs)[1].digest
    ProviderWords(timed=False, model_digest=SERVED).write(inputs.workspace.takes / words_file(digest))
    result = narrate(inputs, make_run(inputs).run)
    (found,) = [found for found in result.findings if found.code is Code.TAKE_UNALIGNED]
    assert found.location.section == 1
    assert "carries no fingerprint" in found.message and "--replace-voiced" in found.message


# ---- one turn of a dialogue ----------------------------------------------------------------------


def test_a_guest_turn_that_came_without_words_is_aligned_alone_and_placed_on_the_section_clock(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], voices: dict[str, SpeechFactory],
    aligner: FakeAligner,
) -> None:  # fmt: skip
    """The narrator's turns came with their words, so only the guest's is aligned, and it plays after the narrator's."""
    narrator = FakeVoice()
    voices[FAKE_VOICE_NAME] = lambda _context: narrator
    voices[FREE_VOICE_NAME] = lambda _context: FakeVoice(name=FREE_VOICE_NAME, words=None, model_digest=SERVED)
    aligner.words = [Word(word="And", start=0.0, end=0.3), Word(word="it", start=0.3, end=0.5),
                     Word(word="rolls", start=0.5, end=0.9)]  # fmt: skip
    project = make_inputs(toml=WITH_A_GUEST, script=DIALOGUE)
    result = narrate(project, make_run(project, spend=True).run)
    assert result.ok and unaligned(result) == []
    assert [request.words for request in aligner.requests] == [("And", "it", "rolls")]
    index = Takes.read(project.workspace.takes_path)
    assert index is not None
    guest = index.placed_turns[TurnKey(2, 2)]
    start = index.placed[2].start
    heard = project.words(2).words
    assert [word.word for word in heard][2:5] == ["And", "it", "rolls"]
    assert heard[2].start == round(guest.start - start, 3)
    assert [row.timed_by for row in index.turns_of(2)] == [TimedBy.PROVIDER, TimedBy.ALIGNED, TimedBy.PROVIDER]
