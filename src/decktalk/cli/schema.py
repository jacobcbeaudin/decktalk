"""The `--json` envelope as types: one frozen dataclass per payload and per row, and the reader.

`Envelope.from_dict` parses what a command printed back into these types. Every verdict becomes a
`Verdict`, every skip reason a `SkipReason`, every stage a `Stage`, every take, soundscape and
section word its enum, and the error's code an `ErrorCode`, so a reader holds enum members and never
compares a string against a code. Every
object is read strictly by `jsonio.read_as`: a missing key, a key the type does not declare, a value
of the wrong kind or a code no enum holds raises `ShapeError` naming the place in the document.

The payload's type is chosen by the command, because one word names the command, the Python call,
the result type and the payload key. `PAYLOADS` is that table, and it is the whole of what a caller
may read from DeckTalk's JSON: a field a skill or a doc names is a field of one of these classes, and
`tests/test_results.py` drives every result through its stage and reads its payload back here, so a
field a result writes and this file does not declare, or the other way round, fails the suite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any

from ..artifacts import Luma
from ..errors import ErrorCode
from ..jsonio import ShapeError, read_as
from ..media.audio import Loudness
from ..pipeline import SectionKind, SoundscapeStatus, Stage, Substitute, TakeStatus
from ..scaffold import DoctorRow
from ..stages.status import RunStatus
from ..verdicts import Finding, SkipReason, Verdict

# ---- the machine commands ---------------------------------------------------------------


@dataclass(frozen=True)
class InitPayload:
    project: str
    name: str
    example: str
    skills: bool
    files: list[str]


@dataclass(frozen=True)
class InstallPayload:
    ffmpeg: str | None
    ffprobe: str | None


@dataclass(frozen=True)
class DoctorPayload:
    components: list[DoctorRow]


@dataclass(frozen=True)
class ServePayload:
    urls: list[str]


# ---- the take plan, which `narrate` and `preflight` share --------------------------------


@dataclass(frozen=True)
class PlanVoice:
    """The voice a take plan is priced for: the provider, the model and the settings it is sent."""

    provider: str | None
    model: str | None
    settings: dict[str, Any]


@dataclass(frozen=True)
class RequestBody:
    """The body a voiced run posts for one section, which carries no credential."""

    text: str
    model_id: str
    voice_settings: dict[str, Any]
    output_format: str
    previous_text: str | None
    next_text: str | None


@dataclass(frozen=True)
class PlannedTake:
    key: str
    chapter: str
    status: TakeStatus
    reason: str | None
    file: str | None
    hash: str | None
    characters_sent: int
    characters_spoken: int
    characters_with_context: int
    word_count: int
    estimated_seconds: float
    placeholders: list[str]
    request: RequestBody | None


@dataclass(frozen=True)
class PlanTotals:
    synthesize: int
    cached: int
    unknown: int
    characters_sent: int
    characters_with_context: int
    characters_spoken: int
    characters_unchecked: int
    price_per_1000_characters: float
    estimated_cost: float | None
    most_it_can_cost: float | None


# ---- the stages ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexedTake:
    """One row of the take index, with the section key it is filed under."""

    key: str
    index: int
    chapter: str
    file: str
    words_file: str
    hash: str
    word_count: int
    estimated_seconds: float
    duration_seconds: float
    voiced: bool
    target_seconds: float | None
    speech_end_seconds: float | None
    sound_end_seconds: float | None
    lead_seconds: float
    tail_seconds: float
    spoken: str


@dataclass(frozen=True)
class TakeIndex:
    model: str
    estimated: bool
    total_seconds: float
    sections: list[IndexedTake]


@dataclass(frozen=True)
class NarratePayload:
    voice: PlanVoice
    note: str | None
    sections: list[PlannedTake]
    totals: PlanTotals
    notes: list[Finding]
    synthesized: list[str]
    cached: list[str]
    takes: TakeIndex | None


@dataclass(frozen=True)
class AlignedSection:
    key: str
    speech_end_seconds: float
    min_seconds: float | None
    skipped: str | None
    cues: dict[str, float]
    notes: list[Finding]


@dataclass(frozen=True)
class AlignPayload:
    estimated: bool
    cue_times_file: str | None
    unresolved: int
    unknown: int
    uncued: int
    sections: list[AlignedSection]


@dataclass(frozen=True)
class RecordedSection:
    key: str
    file: str
    kept: bool
    seconds: float
    t0_seconds: float | None
    t0_method: str | None
    t0_guessed: bool
    duration: float | None
    wanted: float | None
    luma: Luma | None
    verdicts: list[Verdict]
    detail: str | None
    stall_ms: int | None
    page_errors: list[str]
    assets: list[str]
    external: list[str]


@dataclass(frozen=True)
class RecordPayload:
    recordings: list[RecordedSection]
    stale: list[Finding]


@dataclass(frozen=True)
class AssembledSection:
    key: str
    source: str
    substitute: Substitute | None
    duration: float
    path: str


@dataclass(frozen=True)
class Captions:
    srt: str | None
    vtt: str | None
    chapters: str | None
    transcript: str | None


@dataclass(frozen=True)
class LoudnessReport:
    before: Loudness
    after: Loudness
    problems: list[Finding]


@dataclass(frozen=True)
class AssemblePayload:
    final: str
    duration: float
    stamped: str | None
    sections: list[AssembledSection]
    captions: Captions
    cuts: str | None
    poster: str | None
    loudness: LoudnessReport | None
    warnings: list[str]
    uncaptioned: list[Finding]
    substituted: list[Finding]


@dataclass(frozen=True)
class LoggedRecording:
    key: str
    where: str
    t0_method: str | None
    verdicts: list[Verdict]
    detail: str | None
    page_errors: list[str]


@dataclass(frozen=True)
class SectionStart:
    key: str
    where: str | None
    start: float
    probe_at: float
    yavg: float
    ymax: float
    verdict: Verdict
    detail: str | None


@dataclass(frozen=True)
class CutCheck:
    """The sound just before one cut into a section, where speech still sounding means the cut is early."""

    key: str
    where: str | None
    cut_at: float
    rms_db: float
    verdict: Verdict
    detail: str | None


@dataclass(frozen=True)
class Seam:
    key: str
    where: str | None
    cut_at: float
    last_at: float
    first_at: float
    changed_percent: float
    verdict: Verdict
    detail: str | None


@dataclass(frozen=True)
class CueLanding:
    section: int
    cue: str
    where: str | None
    cue_seconds: float | None
    final_seconds: float | None
    changed_percent: float | None
    control_percent: float | None
    offset_ms: int | None
    av_ms: int | None
    verdict: Verdict
    reason: SkipReason | None
    detail: str | None


@dataclass(frozen=True)
class VerifyPayload:
    final: str | None
    total_seconds: float
    silent: bool
    recordings: list[LoggedRecording]
    starts: list[SectionStart]
    cuts: list[CutCheck]
    seams: list[Seam]
    cues: list[CueLanding]


@dataclass(frozen=True)
class BuildPayload:
    """A whole run: each stage's own payload under the name a build gives it, or null when it did not run."""

    narrate: NarratePayload | None
    align: AlignPayload | None
    record: RecordPayload | None
    assemble: AssemblePayload | None
    verify: VerifyPayload | None
    stages: list[Stage]
    progress: str | None


@dataclass(frozen=True)
class BuildPlanPayload:
    """`build --dry-run`: the stages the run would execute, and the artifacts it needs and lacks."""

    stages: list[Stage]
    missing: list[str]


# ---- the commands around a build ----------------------------------------------------------


@dataclass(frozen=True)
class StatusProject:
    root: str
    name: str
    script: str
    script_exists: bool
    cues: str
    cues_exists: bool


@dataclass(frozen=True)
class SectionStatus:
    key: str
    kind: SectionKind
    source: str
    recorded: bool
    cut: bool
    stale: str | None


@dataclass(frozen=True)
class ClockRow:
    """Where one section lands on the narration clock once the takes are joined."""

    key: str
    title: str
    start: float | None
    end: float | None
    duration: float | None


@dataclass(frozen=True)
class NarrationStatus:
    exists: bool
    estimated: bool
    total_seconds: float | None
    sections: list[ClockRow]


@dataclass(frozen=True)
class SectionCueTimes:
    key: str
    cues: dict[str, float]


@dataclass(frozen=True)
class CueTimesStatus:
    exists: bool
    sections: list[SectionCueTimes]


@dataclass(frozen=True)
class FinalStatus:
    path: str
    exists: bool
    duration: float | None


@dataclass(frozen=True)
class OutputStatus:
    path: str
    exists: bool


@dataclass(frozen=True)
class StatusPayload:
    project: StatusProject
    sections: list[SectionStatus]
    narration: NarrationStatus
    cue_times: CueTimesStatus
    final: FinalStatus
    outputs: dict[str, OutputStatus]
    problems: list[Finding]
    run: RunStatus | None


@dataclass(frozen=True)
class PreflightCueTimes:
    """The align payload of a rehearsal, which writes no cue times file and names the estimated sections."""

    estimated: bool
    unresolved: int
    unknown: int
    uncued: int
    sections: list[AlignedSection]
    estimated_sections: list[str]


@dataclass(frozen=True)
class FrozenCue:
    section: int
    cue: str
    cue_seconds: float
    slide: str | None
    changed_percent: float | None
    verdict: Verdict
    reason: SkipReason | None
    detail: str | None
    note: str | None
    before: str | None
    after: str | None


@dataclass(frozen=True)
class FrozenSeam:
    key: str
    changed_percent: float | None
    verdict: Verdict
    reason: SkipReason | None
    detail: str | None
    last: str | None
    first: str | None


@dataclass(frozen=True)
class PreflightPayload:
    voice: PlanVoice
    note: str | None
    placeholders: list[Finding]
    takes: list[PlannedTake]
    totals: PlanTotals
    cue_times: PreflightCueTimes
    cues: list[FrozenCue]
    seams: list[FrozenSeam]
    warnings: list[str]
    page_errors: list[Finding]
    page_scan: list[Finding]
    frames: str | None


@dataclass(frozen=True)
class SpokenWord:
    word: str
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SectionWords:
    key: str
    section: int
    title: str
    lead_seconds: float
    duration: float
    estimated: bool
    words: list[SpokenWord]


@dataclass(frozen=True)
class WordsPayload:
    sections: list[SectionWords]


@dataclass(frozen=True)
class Screenshot:
    file: str
    page: str
    slide: str | None
    cue: str | None
    section: int | None
    at: float | None
    page_errors: list[str]


@dataclass(frozen=True)
class ScreenshotsPayload:
    files: list[Screenshot]


@dataclass(frozen=True)
class SoundscapeItem:
    name: str
    out: str
    endpoint: str
    status: SoundscapeStatus
    duration_seconds: float | None
    requests: list[dict[str, Any]]


@dataclass(frozen=True)
class SoundscapePayload:
    items: list[SoundscapeItem]


@dataclass(frozen=True)
class ClipPayload:
    section: int
    video: str
    words_file: str
    start: float
    end: float
    first_frame: int
    last_frame: int
    hold_seconds: float
    duration: float
    gain_db: float
    estimated: bool
    word_count: int
    cut_words: list[str]
    cuts: list[Finding]


PAYLOADS: dict[str, Any] = {
    "init": InitPayload,
    "install": InstallPayload,
    "doctor": DoctorPayload,
    "status": StatusPayload,
    "preflight": PreflightPayload,
    "words": WordsPayload,
    "screenshots": ScreenshotsPayload,
    "soundscape": SoundscapePayload,
    "clip": ClipPayload,
    "serve": ServePayload,
    Stage.NARRATE.value: NarratePayload,
    Stage.ALIGN.value: AlignPayload,
    Stage.RECORD.value: RecordPayload,
    Stage.ASSEMBLE.value: AssemblePayload,
    Stage.VERIFY.value: VerifyPayload,
    "build": BuildPayload | BuildPlanPayload,
}
"""Each command's payload type, keyed by the command, which is also the key the payload sits under."""


# ---- the envelope ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorSlot:
    code: ErrorCode
    message: str
    hint: str | None
    path: str | None
    line: int | None


@dataclass(frozen=True)
class FindingsSlot:
    certain: int
    uncertain: int
    items: list[Finding]


@dataclass(frozen=True)
class _Head:
    """Everything an envelope carries before its payload, which is read the same way for every command."""

    schema: int
    version: str
    command: str
    ok: bool
    exit_code: int
    summary: dict[str, Any]
    findings: FindingsSlot
    written: list[str]
    error: ErrorSlot | None


ENVELOPE_KEYS = tuple(f.name for f in fields(_Head))
"""The keys every envelope carries, in order, before the command's own payload."""


@dataclass(frozen=True)
class Envelope:
    """One envelope as a reader receives it, with the payload read as its command's type."""

    schema: int
    version: str
    command: str
    ok: bool
    exit_code: int
    summary: dict[str, Any]
    findings: FindingsSlot
    written: list[str]
    error: ErrorSlot | None
    payload: Any
    """The command's own payload, typed by `PAYLOADS`, or None exactly when `error` is set."""

    @classmethod
    def from_dict(cls, doc: Any) -> Envelope:
        """The envelope a command printed, refusing one that is not exactly the shape section 6 fixes."""
        if not isinstance(doc, dict) or not isinstance(doc.get("command"), str):
            raise ShapeError(f"$ is not an envelope: {doc!r}")
        word = doc["command"].split(" ", 1)[0]
        keys = [*ENVELOPE_KEYS, word]
        if list(doc) != keys:
            raise ShapeError(f"$ carries {list(doc)}, and an envelope carries {keys} in that order")
        head = vars(read_as(_Head, {key: doc[key] for key in ENVELOPE_KEYS}))
        if head["error"] is not None:
            # A command that raised printed no payload, and a refused command line may name no command.
            if doc[word] is not None:
                raise ShapeError(f"$.{word} is a payload beside an error, and an error carries none")
            return cls(**head, payload=None)
        if word not in PAYLOADS:
            raise ShapeError(f"$.command is {doc['command']!r}, which is no command")
        return cls(**head, payload=read_as(PAYLOADS[word], doc[word], f"$.{word}"))


def read_envelope(text: str) -> Envelope:
    """The one envelope a command printed on stdout under `--json`."""
    return Envelope.from_dict(json.loads(text))
