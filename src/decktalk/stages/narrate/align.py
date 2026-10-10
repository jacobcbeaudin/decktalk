"""Timing the words of a take whose voice sent none, through the aligner, after every take is made.

A voice that sends a take with no word times leaves a whole, fingerprinted pair whose words file says
so. `first_words` is what such a take plays when its row is first written: words DeckTalk estimated
over its sound, where a take whose voice sent its words plays those.

`time_takes` is the alignment pass. It runs once per narrate, on the caller's thread, after every take
is made, so no worker waits on the aligner and a Ctrl-C stops the alignment in flight at once. It
first settles every such take from the aligned words kept in the takes directory, with no server, so
a run whose every take has kept aligned words opens no socket. Only when some take has none kept, or
the run is forced, does it build the aligner, which asks its server who it is once, and then checks
every such take against that aligner: a take whose aligned words are kept under that aligner's key
plays them, and any other is aligned and its words written beside the takes. The run is checked for a
cancel before every alignment.

An aligner that is down, because no aligner answers, because nothing answered as one, or because it
stopped answering or stayed busy past its retries, is not asked again in the run. A take then plays
the aligned words it already settled on, and a take with none plays estimated words with a
`TAKE_UNALIGNED` finding that says why. An aligner that answered and answered wrong fails the run.

Kept aligned words play by one rule, which `kept_alignment` and the hit share: the file's name is its
own key, and its audio and words are the take's. Among those, the one the film last played wins,
then the highest generation, then the lowest name, which a note names when it settles a tie.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from decktalk.artifacts import (
    AlignedWords,
    AlignmentInputs,
    EstimatedWords,
    ProviderWords,
    Take,
    Takes,
    TimedBy,
    TurnKey,
    Words,
    alignment_match,
    is_placeholder,
)
from decktalk.artifacts.words import WORDS_SUFFIX
from decktalk.errors import NotBuiltError, ProviderError
from decktalk.events import Unit
from decktalk.findings import Code, Finding, Location, judge
from decktalk.inputs import Inputs
from decktalk.inputs.script import estimated_words, spoken_words
from decktalk.machine.run import Run
from decktalk.media import audio
from decktalk.pipeline import Stage
from decktalk.speech import ALIGNER, Aligner, AlignerContext, AlignerIdentity, AlignmentRequest
from decktalk.stages.narrate.takes import retimed, sound_end_of

START = "Start decktalk-voice at the address [dtsp] base_url names"
"""What starts the aligner DeckTalk ships, which is the advice beside a down aligner that named no other."""


@dataclass(frozen=True)
class _Down:
    """Why a take was not aligned, in the clause its finding says after "and", and what to do about it."""

    why: str
    action: Callable[[int], str]
    """The sentence that tells the author what to do about section `n`."""


def _then_run(hint: str) -> Callable[[int], str]:
    return lambda n: f"{hint.rstrip('.')}, then run decktalk narrate --section {n}."


NO_ALIGNER = _Down("this machine answers for no aligner", _then_run(START))
"""Why a take was not aligned on a machine whose host gave it no aligner."""

NO_PRINT = _Down(
    "its words file carries no fingerprint to key aligned words by",
    lambda n: f"Run decktalk narrate --section {n} --replace-voiced to make the take again.",
)
"""Why a take whose words file records no audio print is never aligned."""


def _down_reason(failure: ProviderError) -> _Down:
    """Why the aligner is down, in its own words, and what to do: run again when waiting is enough, else its hint."""
    said = str(failure).rstrip(".")
    why = said[:1].lower() + said[1:]
    if failure.retryable:
        hint = failure.hint
        return _Down(why, lambda n: hint or f"Run decktalk narrate --section {n} again once the aligner answers.")
    return _Down(why, _then_run(failure.hint or START))


def first_words(inputs: Inputs, spoken: str, digest: str) -> tuple[Words, ProviderWords | None]:
    """The words a take plays before any aligner is asked, and the take's own words file.

    A take's own words when its voice sent them, or estimated words over its sound when it sent none.
    A placeholder's own words file is its estimate, so it answers None for the second.
    """
    said = inputs.take_words(digest)
    own = said if isinstance(said, ProviderWords) else None
    if own is not None and not own.timed:
        end = sound_end_of(inputs, inputs.take_places.find(digest).audio)
        return EstimatedWords(words=tuple(estimated_words(spoken, end))), own
    if said is None:
        return (EstimatedWords() if is_placeholder(digest) else ProviderWords()), None
    return said, own


def _plays(path: Path, audio_print: str, words: tuple[str, ...]) -> AlignedWords | None:
    """The aligned words at `path` when they may play for this take, which is when they are its own and nothing else."""
    try:
        aligned = AlignedWords.read(path)
    except NotBuiltError:
        # silent: a file that is not this take's alignment is not read as one, and the take is aligned again.
        return None
    if aligned is None or path.name != f"{aligned.inputs.key}{WORDS_SUFFIX}":
        return None
    return aligned if (aligned.inputs.audio, aligned.inputs.words) == (audio_print, words) else None


def _readable(inputs: Inputs, audio_print: str, words: tuple[str, ...]) -> list[AlignedWords]:
    """Every alignment of this take kept in the takes directory that may play for it, by name."""
    match = alignment_match(audio_print, words)
    paths = sorted(inputs.workspace.aligned_dir.glob(f"{match}-*{WORDS_SUFFIX}"))
    return [aligned for path in paths if (aligned := _plays(path, audio_print, words)) is not None]


def _kept(
    inputs: Inputs, audio_print: str, words: tuple[str, ...], *, prefer: str | None
) -> tuple[AlignedWords | None, list[AlignedWords]]:
    """The kept alignment this take plays with no server, and every alignment it tied with at its generation."""
    found = _readable(inputs, audio_print, words)
    preferred = next((aligned for aligned in found if aligned.inputs.key == prefer), None)
    if preferred is not None or not found:
        return preferred, []
    top = max(aligned.generation for aligned in found)
    tied = sorted((aligned for aligned in found if aligned.generation == top), key=lambda aligned: aligned.inputs.key)
    return tied[0], tied if len(tied) > 1 else []


def kept_alignment(
    inputs: Inputs, audio_print: str, words: tuple[str, ...], *, prefer: str | None
) -> AlignedWords | None:
    """Aligned words of this audio and these words kept in the takes directory, read with no server.

    The alignment `prefer` names wins, which is the one the film last played, then the highest
    generation, then the lowest name.
    """
    return _kept(inputs, audio_print, words, prefer=prefer)[0]


@dataclass(frozen=True)
class _Untimed:
    """One take of this run whose voice sent no word times, and the turn that plays it."""

    key: TurnKey
    digest: str
    audio_print: str
    words: tuple[str, ...]


@dataclass
class _Pass:
    """What one alignment pass has settled so far: the rows, the findings, and the notes it owes the run."""

    rows: dict[TurnKey, Take]
    found: list[Finding] = field(default_factory=list)
    ties: dict[TurnKey, list[AlignedWords]] = field(default_factory=dict)
    live: dict[TurnKey, str] = field(default_factory=dict)
    """The key each take is kept under by the aligner that answered the handshake, when one did."""

    def several(self, section: int) -> bool:
        """Whether this section has more than one turn, which a finding then names."""
        return sum(key.section == section for key in self.rows) > 1


def time_takes(
    inputs: Inputs,
    run: Run,
    rows: Mapping[TurnKey, Take],
    previous: Takes | None,
    *,
    made: Collection[TurnKey],
    force: bool,
) -> tuple[dict[TurnKey, Take], list[Finding]]:
    """Every take this run made whose voice sent no word times: settled from its kept aligned words, else aligned
    one at a time on this thread after the pool, asking the aligner only when a take has none kept or `force` is set.

    It gives back the rows, retimed where the take now plays aligned words, and a `TAKE_UNALIGNED`
    finding for every turn whose take plays estimated words because no aligner timed it.
    """
    state = _Pass(rows=dict(rows))
    untimed = _untimed(inputs, state, made)
    missed = _settle(inputs, state, untimed, previous)
    if untimed and (missed or force):
        aligner, down = _aligner(inputs, run)
        if aligner is not None:
            down = _align_every(inputs, run, state, untimed, aligner)
            _reuse_note(inputs, run, state, aligner.identity)
        for take in untimed:
            if state.rows[take.key].timed_by is TimedBy.ESTIMATED:
                state.found.append(_unaligned(inputs, state, take.key, down or NO_ALIGNER))
    _tie_note(run, state)
    return state.rows, state.found


def _untimed(inputs: Inputs, state: _Pass, made: Collection[TurnKey]) -> list[_Untimed]:
    """The takes this run made whose voice sent no word times, in section and turn order.

    A take whose words file carries no audio print cannot be keyed, so it gets its finding here instead.
    """
    untimed: list[_Untimed] = []
    for key in sorted(made):
        row = state.rows.get(key)
        if row is None or not row.voiced or row.timed_by is not TimedBy.ESTIMATED:
            continue
        said = inputs.take_words(row.digest)
        if not isinstance(said, ProviderWords) or said.timed:
            continue
        if said.audio is None:
            state.found.append(_unaligned(inputs, state, key, NO_PRINT))
            continue
        untimed.append(_Untimed(key, row.digest, said.audio.blake3, spoken_words(row.spoken)))
    return untimed


def _settle(inputs: Inputs, state: _Pass, untimed: list[_Untimed], previous: Takes | None) -> bool:
    """Play every take's kept aligned words, with no server, and say whether any take has none kept."""
    missed = False
    before = {row.turn_key: row for row in previous.sections} if previous is not None else {}
    for take in untimed:
        last = before.get(take.key)
        prefer = last.alignment if last is not None and last.digest == state.rows[take.key].digest else None
        aligned, tied = _kept(inputs, take.audio_print, take.words, prefer=prefer)
        if aligned is None:
            missed = True
            continue
        state.rows[take.key] = retimed(state.rows[take.key], aligned)
        if tied:
            state.ties[take.key] = tied
    return missed


def _aligner(inputs: Inputs, run: Run) -> tuple[Aligner | None, _Down | None]:
    """The aligner this machine answers with, built by asking its server who it is, or why there is none."""
    factory = run.machine.aligners.get(ALIGNER)
    if factory is None:
        return None, NO_ALIGNER
    context = AlignerContext(
        base_url=inputs.settings.dtsp.base_url.rstrip("/"),
        handshake_seconds=inputs.settings.dtsp.handshake_seconds,
        timeout_seconds=inputs.settings.narration.timeout_seconds,
        retries=run.machine.speech_providers.retries,
    )
    try:
        return factory(context), None
    except ProviderError as failure:
        return None, _down_reason(failure)


def _align_every(inputs: Inputs, run: Run, state: _Pass, untimed: list[_Untimed], aligner: Aligner) -> _Down | None:
    """Check every take against the aligner that answered, aligning each one it has no kept words of, until it is down.

    A take two sections share is aligned once. The pass stops at the first failure that says the
    aligner is down, and gives back why, and raises any failure of an aligner that answered wrong.
    """
    identity = aligner.identity
    asking: list[tuple[_Untimed, AlignmentInputs]] = []
    for take in untimed:
        keyed = AlignmentInputs(
            audio=take.audio_print,
            words=take.words,
            aligner=identity.id,
            revision=identity.revision,
            model_digest=identity.model_digest,
        )
        state.live[take.key] = keyed.key
        hit = _plays(inputs.workspace.aligned_words(keyed.key), take.audio_print, take.words)
        if hit is not None:
            state.rows[take.key] = retimed(state.rows[take.key], hit)
        else:
            asking.append((take, keyed))
    total = len({keyed.key for _take, keyed in asking})
    aligned: dict[str, AlignedWords] = {}
    for take, keyed in asking:
        if keyed.key not in aligned:
            try:
                aligned[keyed.key] = _align_one(inputs, run, take, keyed, aligner)
            except ProviderError as failure:
                if failure.reached and not failure.retryable:
                    raise
                return _down_reason(failure)
            row = state.rows[take.key]
            run.progress(
                Stage.NARRATE,
                done=len(aligned),
                total=total,
                unit=Unit.TAKE,
                label=f"aligning {row.chapter}",
                section=take.key.section,
            )
        state.rows[take.key] = retimed(state.rows[take.key], aligned[keyed.key])
    return None


def _align_one(inputs: Inputs, run: Run, take: _Untimed, keyed: AlignmentInputs, aligner: Aligner) -> AlignedWords:
    """Align one take, and keep its aligned words in the takes directory under their key.

    The words are written one generation above every alignment of this take kept so far, so with the
    aligner down later the newest plays.
    """
    run.check()
    wav = audio.wav(inputs.take_places.find(take.digest).audio, sample_rate=inputs.settings.audio.sample_rate)
    words = aligner.align(AlignmentRequest(wav=wav, words=take.words))
    generation = 1 + max((kept.generation for kept in _readable(inputs, take.audio_print, take.words)), default=0)
    try:
        aligned = AlignedWords(words=tuple(words), inputs=keyed, generation=generation)
    except ValidationError as exc:
        raise ProviderError(f"the aligner answered words that are not the ones it was sent: {exc}") from exc
    inputs.workspace.aligned_dir.mkdir(parents=True, exist_ok=True)
    run.wrote(aligned.write(inputs.workspace.aligned_words(keyed.key)))
    return aligned


def _unaligned(inputs: Inputs, state: _Pass, key: TurnKey, down: _Down) -> Finding:
    """The finding for one turn whose take plays estimated words, saying why no aligner timed it.

    A section of one turn is named alone, and a section of several names the turn too.
    """
    number = key.section
    take = f"Section {number}'s take of turn {key.turn}" if state.several(number) else f"Section {number}'s take"
    return judge(
        Code.TAKE_UNALIGNED,
        f"{take} came with no word times, and {down.why}, so it plays estimated words. {down.action(number)}",
        Location(where=f"section {number}", file=inputs.relative(inputs.script_path), section=number),
        stage=Stage.NARRATE,
    )


def _reuse_note(inputs: Inputs, run: Run, state: _Pass, identity: AlignerIdentity) -> None:
    """Say which sections still play words an earlier aligner measured, once an aligner that answered went down."""
    numbers = list(
        dict.fromkeys(
            turn.section
            for turn, key in state.live.items()
            if state.rows[turn].timed_by is TimedBy.ALIGNED and state.rows[turn].alignment != key
        )
    )
    if numbers:
        run.note(
            f"The aligner at {inputs.settings.dtsp.base_url.rstrip('/')} is {identity.id} {identity.revision}, and "
            f"section(s) {numbers} play words an earlier aligner measured. Run decktalk narrate --force to align "
            "them again."
        )


def _tie_note(run: Run, state: _Pass) -> None:
    """Name the aligners a take's kept words tied between, once, when the take still plays the one the tie chose."""
    for turn, tied in state.ties.items():
        if state.rows[turn].alignment == tied[0].inputs.key:
            run.note(
                f"Section {turn.section}'s take has aligned words from {len(tied)} aligners kept at one generation, so "
                f"{tied[0].inputs.key}{WORDS_SUFFIX} plays. Remove the others from takes/aligned with git rm."
            )
            return


__all__ = ["first_words", "kept_alignment", "time_takes"]
