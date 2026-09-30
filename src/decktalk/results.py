"""What every call returns: one frozen result per command, each a flat object a reader can dispatch on.

`Result` reserves four keys and every result carries them. `schema` is the shape version, `ok` is
true when the command ran and judged nothing certain, `findings` holds every judgement and `error`
is filled only when the command could not run at all. A result whose command opens a run also
declares `run`, and one whose command writes files also declares `written`, so a reader learns from
the schema which commands do those things rather than meeting a null on the ones that do not.

A result also declares two facts about its own command rather than about its own JSON.
`reports_findings` says the command can report a judgement and `spends` says it can buy something,
and the command line derives `--fail-on`, `--allow` and the three spending flags from them. They are
class facts rather than fields, so the shape a caller reads is unchanged and the command line needs
no list of its own beside the models.

Every result lives here rather than in the stage that fills it, because importing the command line
must load no stage, and because the row types a result carries would otherwise sit above it. Nothing
in this module imports a stage, a project or a machine.

Paths are stored project-relative the moment a stage fills them, so `model_dump_json()` with no
arguments is correct and a caller needs no root to read one. A field whose value changes between two
identical runs carries `volatile` in its schema, so a golden comparison drops it by declaration
rather than by a hand-written strip in every test.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, Field, JsonValue

from decktalk.errors import ErrorInfo
from decktalk.findings import Code, Finding, Model, ProjectPath
from decktalk.page import SECOND_DIGITS
from decktalk.pipeline import Outcome, Stage

SCHEMA = 2
"""The shape version every result carries, which a reader checks before it reads anything else."""

VOLATILE: dict[str, JsonValue] = {"volatile": True}
"""What marks a field whose value differs between two otherwise identical runs."""

Run = Annotated[
    str,
    Field(description="The id of the run this call opened, which names its events file.", json_schema_extra=VOLATILE),
]
"""The run id, declared once and carried by every result whose command opens a run."""

Written = Annotated[
    tuple[ProjectPath, ...],
    Field(default=(), description="Every file this run wrote, project-relative, in the order it wrote them."),
]
"""The files a run wrote, declared once and carried by every result whose command writes any."""

Elapsed = Annotated[
    float,
    Field(ge=0, description="How long this call took, in seconds.", json_schema_extra=VOLATILE),
    AfterValidator(lambda seconds: round(seconds, SECOND_DIGITS)),
]
"""A wall-clock duration, which is measured rather than computed and so is never compared.

It is rounded where it is declared, so a run, a stage, a section and a result all read their
clocks the same way and no emitter rounds for itself.
"""

NextCommand = Annotated[
    str | None,
    Field(default=None, description="The whole command to run next, or null when nothing is next."),
]
"""A reading of project state that goes stale, so only the two objects a caller takes fresh carry it."""

SectionNumber = Annotated[int, Field(ge=0, description="The section this row is about, as the author numbered it.")]
"""A section's number, which is how the author numbers sections in decktalk.toml, counting from zero or one."""

SectionKey = Annotated[str, Field(description="The section's key, which names its files under build/.")]
"""A section's key, which is the stable name its recording, its take and its cut are filed under."""


class Voicing(Enum):
    """What a run does about the voice, which replaces a pair of flags that could contradict each other."""

    PLACEHOLDER = "placeholder"
    PAID = "paid"


class SpendState(Enum):
    """Whether a price is what a run would cost or what it did cost."""

    ESTIMATE = "estimate"
    CHARGED = "charged"


class Layer(Enum):
    """Which of the five layers set a settings value, lowest first.

    A key with no override is set by `default`, which is also what refuses `--max-cost` on a price
    nobody has stated: DeckTalk would be capping a spend against a number it made up.
    """

    DEFAULT = "default"
    MACHINE = "machine"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    OVERRIDE = "override"


class Scope(Enum):
    """Which file a settings write lands in."""

    PROJECT = "project"
    MACHINE = "machine"


class TakeStatus(Enum):
    """What one run did about one section's take."""

    VOICED = "voiced"
    KEPT = "kept"
    PLACEHOLDER = "placeholder"


class SoundKind(Enum):
    """What one soundscape item is, which decides where it sits in the mix."""

    MUSIC = "music"
    AMBIENCE = "ambience"
    EFFECT = "effect"


class SoundStatus(Enum):
    """What one run did about one soundscape item."""

    PLANNED = "planned"
    KEPT = "kept"
    GENERATED = "generated"


class SectionKind(Enum):
    """What a section plays, which is a recorded page or a video file the author supplied."""

    PAGE = "page"
    CLIP = "clip"


class Substitute(Enum):
    """What played in place of a section whose file was missing."""

    SLATE = "slate"
    BLACK = "black"


class SkipReason(Enum):
    """Why one measurement was not taken, so a skipped row is never read as a passing one."""

    AT_SECTION_START = "at_section_start"
    NO_CUES = "no_cues"
    NO_ONSET = "no_onset"
    NO_SLIDE = "no_slide"
    NOT_ASSEMBLED = "not_assembled"
    OPTED_OUT = "opted_out"
    TOO_CLOSE_TO_END = "too_close_to_end"


def counted(count: int, noun: str, plural: str | None = None) -> str:
    """A count and its noun, singular for one and plural otherwise, which every sentence that counts uses.

    The plural adds an s unless the caller names it, which a noun such as fix needs.
    """
    return f"{count:,} {noun if count == 1 else plural or noun + 's'}"


class Spend(Model):
    """What a run costs, priced once so a caller never works it out from a character count.

    `--max-cost` is compared against `ceiling_dollars` and never against `dollars`, because credits
    are consumed one request at a time and a cap that claimed to stop a run halfway would be a lie.
    """

    state: SpendState = Field(description="Whether this is what the run would cost or what it did cost.")
    sections: tuple[SectionNumber, ...] = Field(description="The sections this price covers, in script order.")
    characters: int = Field(ge=0, description="How many characters of script this price is for.")
    dollars: float = Field(ge=0, description="The price at the stated rate, in US dollars.")
    ceiling_dollars: float = Field(ge=0, description="The most this run can cost, in US dollars.")
    price_per_1000_characters: float = Field(ge=0, description="The rate this price was worked out at.")
    price_layer: Layer = Field(description="Which layer set that rate, where default means nobody stated it.")

    @property
    def sentence(self) -> str:
        """This price in one sentence, which tells the figure a run certainly spends from its ceiling.

        A take that could not be matched to a voice may already be on disk, so it counts toward the
        ceiling and never toward the price. When the two differ the sentence says which is which,
        because "about $0.00, up to $0.14" reads as a contradiction to anyone not holding the rule.
        Every surface that states a price states this sentence, so the rule is written once.
        """
        rate = f"{money(self.price_per_1000_characters)} per 1,000 characters"
        if self.state is SpendState.CHARGED:
            if self.dollars == self.ceiling_dollars == 0:
                return "This run bought nothing."
            return f"This run spent {money(self.dollars)} on {counted(self.characters, 'character')} at {rate}."
        if self.ceiling_dollars == 0:
            return "This run buys nothing."
        if self.dollars == self.ceiling_dollars:
            return f"This run costs {money(self.dollars)} for {counted(self.characters, 'character')} at {rate}."
        if self.dollars == 0:
            return (
                "The takes on disk could not be matched to a voice, so this run costs up to "
                f"{money(self.ceiling_dollars)} at {rate}."
            )
        return (
            f"This run costs {money(self.dollars)} for the sections that certainly need a take, and up to "
            f"{money(self.ceiling_dollars)} if the takes that could not be matched to a voice need one too, "
            f"at {rate}."
        )


def money(dollars: float) -> str:
    """An amount in US dollars as a price is written, to the cent."""
    return f"${dollars:.2f}"


class Word(Model):
    """One spoken word with its span, in seconds after its section starts."""

    word: str = Field(description="The word as the script spells it.")
    start: float = Field(ge=0, description="When the word starts, in seconds after its section starts.")
    end: float = Field(ge=0, description="When the word ends, in seconds after its section starts.")


class Result(Model):
    """What every call returns, with the four keys every result reserves.

    A subclass adds its own fields at the top level, because the JSON is one flat object and a
    reader derives every count it wants from the arrays it already has.
    """

    reports_findings: ClassVar[bool] = False
    """True when the command answering with this can report a judgement, so it takes --fail-on and --allow."""

    spends: ClassVar[bool] = False
    """True when the command answering with this can buy something, so it takes the three spending flags."""

    schema_: Literal[2] = Field(SCHEMA, alias="schema", description="The shape version of this object.")
    ok: bool = Field(description="True when the command ran and judged nothing certain.")
    findings: tuple[Finding, ...] = Field((), description="Every judgement this call made, certain first.")
    error: ErrorInfo | None = Field(None, description="Filled only when the command could not run.")


class ErrorResult(Result):
    """What a refused command line fills, which carries the refusal and nothing else.

    A command that never ran has no fields of its own to report, so this is the base exactly, and it
    is the one result a caller meets without having reached a command's implementation.
    """


class InstalledTool(Model):
    """One tool this machine holds or has just fetched."""

    tool: str = Field(description="What the tool is called, such as ffmpeg or chromium.")
    version: str | None = Field(None, description="The version this machine holds, or null when it cannot be read.")
    path: ProjectPath | None = Field(None, description="Where the tool is, or null when it is not there.")
    fetched: bool = Field(False, description="True when this run downloaded it rather than finding it.")


class SectionStatus(Model):
    """What one section has and what it still needs, as `status` reads it off disk."""

    section: SectionNumber
    key: SectionKey
    kind: SectionKind = Field(description="Whether the section plays a recorded page or a supplied clip.")
    source: str = Field(description="The page or the file this section plays.")
    voiced: bool = Field(description="True when a voice spoke a take of this section's current text.")
    recorded: bool = Field(description="True when a recording of this section is on disk.")
    cut: bool = Field(description="True when this section has been cut into the film.")
    stale: bool = Field(description="True when what is on disk no longer matches what the project says.")


class LiveRun(Model):
    """One run whose events file is still open, which is how a caller finds a background build."""

    run: Run
    events: ProjectPath = Field(description="The events file that run appends to, project-relative.")
    started: datetime = Field(description="When the run opened.", json_schema_extra=VOLATILE)
    stage: Stage | None = Field(None, description="The stage that run was last in, or null before the first.")


class SectionTake(Model):
    """One section's take: what it cost, how long it runs and what the run did about it."""

    section: SectionNumber
    key: SectionKey
    status: TakeStatus = Field(description="What this run did about this section's take.")
    characters: int = Field(ge=0, description="How many characters of script this take speaks.")
    seconds: float | None = Field(None, ge=0, description="How long the take runs, or null before it exists.")
    file: ProjectPath | None = Field(None, description="The take's audio file, or null before it exists.")
    hash: str = Field(description="The content hash that decides whether a take may be reused.")


class CueTime(Model):
    """One cue resolved against the words its section speaks."""

    cue: str = Field(description="The cue's wire id, which is its slide and its local name.")
    phrase: str = Field(description="The phrase in the script this cue lands on.")
    seconds: float | None = Field(None, ge=0, description="When it lands, in seconds after its section starts.")
    offset: float = Field(0.0, description="The author's own nudge in seconds, added to the resolved second.")


class SectionCues(Model):
    """One section's cues, each resolved to a second on that section's own clock."""

    section: SectionNumber
    key: SectionKey
    estimated: bool = Field(description="True when the seconds come from estimated words rather than a voiced take.")
    cues: tuple[CueTime, ...] = Field(description="Every cue this section declares, in the order they play.")


class SectionRecording(Model):
    """One section's recording, as the recorder left it."""

    section: SectionNumber
    key: SectionKey
    file: ProjectPath | None = Field(None, description="The recorded video, or null when nothing was recorded.")
    seconds: float = Field(ge=0, description="How long the recording runs.")
    frames: int = Field(ge=0, description="How many frames the recording holds.")
    kept: bool = Field(description="True when the run left an existing recording alone.")


class SoundItem(Model):
    """One piece of the soundscape, which is a music bed, an ambience bed or an effect."""

    name: str = Field(description="What the author calls this item in decktalk.toml.")
    kind: SoundKind = Field(description="Whether this item is music, ambience or an effect.")
    status: SoundStatus = Field(description="What this run did about this item.")
    prompt: str = Field(description="The words the author wrote to describe this item.")
    seconds: float | None = Field(None, ge=0, description="How long the item runs, or null before it exists.")
    file: ProjectPath | None = Field(None, description="The item's audio file, or null before it exists.")


class RenderedSection(Model):
    """One section as it sits in the finished film."""

    section: SectionNumber
    key: SectionKey
    file: ProjectPath = Field(description="The cut this section contributed, project-relative.")
    start: float = Field(ge=0, description="When this section starts in the film, in seconds.")
    seconds: float = Field(ge=0, description="How long this section runs in the film.")
    substitute: Substitute | None = Field(None, description="What stood in for a missing file, or null.")


class Loudness(Model):
    """What the mixed film measures against the loudness it was mastered to."""

    integrated_lufs: float = Field(description="The film's integrated loudness.")
    true_peak_dbtp: float = Field(description="The film's highest true peak.")
    range_lu: float = Field(ge=0, description="The film's loudness range.")
    target_lufs: float = Field(description="The integrated loudness the mix was aiming at.")


class StartCheck(Model):
    """What the first frame of one section looks like, which is how a black opening is caught."""

    section: SectionNumber
    at: float = Field(ge=0, description="When this frame sits in the film, in seconds.")
    luma: float = Field(ge=0, description="The frame's brightest pixel, on the luma scale the settings bound.")


class CutCheck(Model):
    """What one seam between two sections sounds like."""

    section: SectionNumber
    at: float = Field(ge=0, description="When the cut sits in the film, in seconds.")
    speech_dbfs: float = Field(description="How loud speech is across the cut.")
    step_dbfs: float = Field(description="How far the waveform steps across the cut.")


class SeamCheck(Model):
    """How far one section's picture has drifted from its own clock by the time it ends."""

    section: SectionNumber
    at: float = Field(ge=0, description="When the seam sits in the film, in seconds.")
    drift: float = Field(description="How far the picture is from where the clock says it should be, in seconds.")


class CueCheck(Model):
    """One cue measured on the finished film against the word it was promised to."""

    section: SectionNumber
    cue: str = Field(description="The cue's wire id.")
    spoken: float = Field(ge=0, description="When the cue's word is spoken in the film, in seconds.")
    shown: float | None = Field(None, ge=0, description="When the picture changed, or null when it did not.")
    offset: float | None = Field(None, description="How far the change is from its word, in seconds.")
    change_percent: float | None = Field(None, ge=0, description="How much of the picture changed, as a percent.")
    skipped: SkipReason | None = Field(None, description="Why this cue was not measured, or null when it was.")


class StageRun(Model):
    """One stage of one build, and how it ended."""

    stage: Stage = Field(description="The stage this row is about.")
    outcome: Outcome = Field(description="Whether the stage ran, was kept from the last run, was skipped, or failed.")
    seconds: Elapsed


class SettingValue(Model):
    """One settings key with the value in force and the layer that set it."""

    key: str = Field(description="The key's dotted name, such as verify.cue_offset_max_ms.")
    value: JsonValue = Field(description="The value in force for this project on this machine.")
    default: JsonValue = Field(description="The value that would be in force with no override at all.")
    layer: Layer = Field(description="Which layer set the value in force.")
    file: ProjectPath | None = Field(None, description="The file that set it, or null when no file did.")


class LayerValue(Model):
    """One layer's answer for one key, whether or not that layer is the one in force."""

    layer: Layer = Field(description="Which of the five layers this row is.")
    value: JsonValue = Field(description="The value this layer states, or the default when it is the default.")
    file: ProjectPath | None = Field(None, description="The file this layer read, or null when it is not a file.")
    line: int | None = Field(None, ge=1, description="The line in that file, or null.")


class NumberView(Model):
    """One published number a key feeds, with its inputs at the values in force."""

    id: str = Field(description="The number's name, which is the key it replaced or the constant it is.")
    formula: str = Field(description="The expression this number is, which is what it is published as.")
    reads: dict[str, JsonValue] = Field(description="Every key and constant the formula reads, at its value here.")
    value: JsonValue = Field(description="What the formula works out to at the values in force.")
    candidate: JsonValue | None = Field(None, description="What it would work out to at the candidate, or null.")
    unit: str | None = Field(None, description="The number's true unit, or null when it has none.")
    sentence: str = Field(description="Why this number is not a knob, which opens with its nature.")


class FixOutcome(Model):
    """What happened to one fix a caller asked to apply."""

    code: Code = Field(description="The code of the finding this fix resolves.")
    title: str = Field(description="The fix's own sentence, as the finding published it.")
    applied: bool = Field(description="True when the fix was applied, false when it was left alone.")
    why: str | None = Field(None, description="Why an unapplied fix was left alone, or null when it was applied.")
    files: tuple[ProjectPath, ...] = Field((), description="Every file the fix changed, project-relative.")


class SectionWords(Model):
    """One section's spoken words, in seconds after that section starts."""

    section: SectionNumber
    key: SectionKey
    estimated: bool = Field(description="True when the words come from a build with placeholder narration.")
    words: tuple[Word, ...] = Field(description="Every word this section speaks, in the order it speaks them.")


class Panel(Model):
    """One frozen moment on the storyboard."""

    section: SectionNumber
    slide: str = Field(description="The slide this panel shows.")
    cue: str | None = Field(None, description="The cue this panel is frozen at, or null for the slide's opening.")
    at: float = Field(ge=0, description="When this moment sits in its section, in seconds.")
    image: ProjectPath = Field(description="The frozen image, project-relative.")


class InitResult(Result):
    """What `decktalk init` wrote, which is a project that already builds."""

    run: Run
    written: Written
    root: ProjectPath = Field(description="The project directory this call created.")
    name: str = Field(description="The project's name, which its film is named after.")
    example: str = Field(description="The packaged example this project was written from.")
    skills: bool = Field(description="True when the packaged skills were written into the project.")


class InstallResult(Result):
    """What `decktalk install` fetched, and what it found already there."""

    run: Run
    tools: tuple[InstalledTool, ...] = Field(description="Every tool this machine needs, in the order it checks them.")
    cache: ProjectPath = Field(description="The directory the fetched tools live in.")


class DoctorResult(Result):
    """What this machine holds, and what a run on it would use."""

    reports_findings: ClassVar[bool] = True

    run: Run
    tools: tuple[InstalledTool, ...] = Field(description="Every tool this machine needs, in the order it checks them.")
    cache: ProjectPath = Field(description="The directory the fetched tools live in.")
    python: str = Field(description="The Python this DeckTalk runs on.")
    platform: str = Field(description="The operating system and processor this machine reports.")
    voice_key: bool = Field(description="True when the credential a voiced run would use is set.")
    bias_ms: float | None = Field(None, description="This host's measured presentation bias, or null when unmeasured.")


class StatusResult(Result):
    """What is written, what is built, what is stale, and what to do next."""

    run: Run
    name: str = Field(description="The project's name, which its film is named after.")
    script: ProjectPath = Field(description="The script this project speaks, project-relative.")
    cues: ProjectPath = Field(description="The cue file this project resolves, project-relative.")
    sections: tuple[SectionStatus, ...] = Field(description="Every section, in script order.")
    film: ProjectPath | None = Field(None, description="The built film, or null when none is built.")
    film_seconds: float | None = Field(None, ge=0, description="How long the built film runs, or null.")
    runs: tuple[LiveRun, ...] = Field((), description="Every run whose events file is still open.")
    next: NextCommand


class CheckResult(Result):
    """What a judgement before a build found, and what the build would cost."""

    reports_findings: ClassVar[bool] = True

    run: Run
    written: Written
    judged: tuple[ProjectPath, ...] = Field(description="Every file and page this call judged, project-relative.")
    pages: bool = Field(description="True when the pages were opened in a browser rather than read as text.")
    frames: bool = Field(description="True when slides were frozen and compared as pictures.")
    spend: Spend = Field(description="What a voiced build of this project would cost.")
    storyboard: ProjectPath | None = Field(None, description="The storyboard this call wrote, or null.")


class WordsResult(Result):
    """Every spoken word with its span, which is how a cue phrase is written."""

    run: Run
    sections: tuple[SectionWords, ...] = Field(description="Every spoken section, in script order.")


class StoryboardResult(Result):
    """The contact sheet of every slide at every cue, which is the checkpoint before credits are spent."""

    reports_findings: ClassVar[bool] = True

    run: Run
    written: Written
    storyboard: ProjectPath | None = Field(None, description="The storyboard page this call wrote, or null.")
    panels: tuple[Panel, ...] = Field(description="Every frozen moment on that page, in film order.")


class ServeResult(Result):
    """The local origin serving this project's deck."""

    run: Run
    url: str = Field(description="The origin's base URL, which is where the deck is served.")
    port: int = Field(ge=1, description="The port the origin listens on.")


class ConfigListResult(Result):
    """Every settings key with the value in force and the layer that set it."""

    keys: tuple[SettingValue, ...] = Field(description="Every key, in the order the tables declare them.")


class ConfigGetResult(Result):
    """One settings key with the value in force."""

    key: SettingValue = Field(description="The key asked for, with the value in force.")


class ConfigSetResult(Result):
    """What a settings write changed, or what it would change on a dry run."""

    written: Written
    key: str = Field(description="The key's dotted name.")
    value: JsonValue = Field(description="The value this call wrote.")
    previous: JsonValue = Field(description="The value that file held before, or null when it held none.")
    scope: Scope = Field(description="Which file the write landed in.")
    file: ProjectPath = Field(description="The file that was written, project-relative.")
    effective: JsonValue = Field(description="The value in force once this call is done, which a higher layer may set.")
    layer: Layer = Field(description="Which layer the value in force comes from, so a shadowed write says it is one.")
    dry_run: bool = Field(description="True when the call reported the change and wrote nothing.")


class ConfigUnsetResult(Result):
    """What a settings removal took out, so the layer below it wins again."""

    written: Written
    keys: tuple[str, ...] = Field(description="Every key that file no longer sets, in the order this call named them.")
    previous: JsonValue = Field(description="The value that file held before, or null when it held none.")
    scope: Scope = Field(description="Which file the removal landed in.")
    file: ProjectPath = Field(description="The file that was written, project-relative.")
    effective: JsonValue = Field(description="The value in force once this call is done, which the layer below sets.")
    layer: Layer = Field(description="Which layer decides the key now, so a variable that still sets it says so.")


class ConfigExplainResult(Result):
    """One knob read whole: what it is, what it does, what may be set and what set it."""

    key: str = Field(description="The key's dotted name.")
    type: str = Field(description="The key's type, as the schema names it.")
    sentence: str = Field(description="What this key changes, in one sentence.")
    value: JsonValue = Field(description="The value in force for this project on this machine.")
    default: JsonValue = Field(description="The value that would be in force with no override at all.")
    unit: str | None = Field(None, description="The true unit of the value, or null when it has none.")
    range: str = Field(description="The values this key accepts, as the schema states them.")
    layer: Layer = Field(description="Which layer set the value in force.")
    file: ProjectPath | None = Field(None, description="The file that set it, or null when no file did.")
    line: int | None = Field(None, ge=1, description="The line in that file, or null.")
    layers: tuple[LayerValue, ...] = Field(description="Every layer that stated this key, lowest first.")
    environment: str = Field(description="The environment variable that sets this key.")
    decides: tuple[Code, ...] = Field((), description="The findings whose verdict this key moves.")
    numbers: tuple[NumberView, ...] = Field((), description="The published numbers this key feeds, worked out here.")
    clamped: tuple[str, ...] = Field((), description="Every cue in this project the value in force clamps.")
    hazard: str | None = Field(None, description="What a value at the edge of the range risks, or null.")
    docs: str = Field(description="The docs page for this key.")


class NarrateResult(Result):
    """What the voice was asked for, what it returned and what the run kept."""

    reports_findings: ClassVar[bool] = True
    spends: ClassVar[bool] = True

    run: Run
    written: Written
    voice: Voicing = Field(description="Whether this run spent on speech or wrote placeholders.")
    sections: tuple[SectionTake, ...] = Field(description="Every section this run considered, in script order.")
    spend: Spend = Field(description="What this run cost, or would have cost.")
    takes: ProjectPath | None = Field(None, description="The take index this run wrote, or null on a dry run.")
    seconds: Elapsed


class CueResult(Result):
    """Every cue phrase resolved to a second on its section's clock."""

    reports_findings: ClassVar[bool] = True

    run: Run
    written: Written
    sections: tuple[SectionCues, ...] = Field(description="Every section that declares a cue, in script order.")
    file: ProjectPath | None = Field(None, description="The cue times this run wrote, or null when it wrote none.")
    seconds: Elapsed


class RecordResult(Result):
    """Every section the recorder touched, in the order it touched them."""

    reports_findings: ClassVar[bool] = True

    run: Run
    written: Written
    sections: tuple[SectionRecording, ...] = Field(description="Every section this run considered, in script order.")
    seconds: Elapsed


class SoundscapeResult(Result):
    """Every piece of the soundscape this run planned or generated."""

    reports_findings: ClassVar[bool] = True
    spends: ClassVar[bool] = True

    run: Run
    written: Written
    items: tuple[SoundItem, ...] = Field(description="Every item, in the order decktalk.toml declares them.")
    spend: Spend = Field(description="What this run cost, or would have cost.")
    seconds: Elapsed


class AssembleResult(Result):
    """The finished film and everything written beside it."""

    reports_findings: ClassVar[bool] = True

    run: Run
    written: Written
    film: ProjectPath = Field(description="The finished film, project-relative.")
    film_seconds: float = Field(ge=0, description="How long the finished film runs.")
    sections: tuple[RenderedSection, ...] = Field(description="Every section as it sits in the film, in film order.")
    loudness: Loudness | None = Field(None, description="What the mix measured, or null when it was not measured.")
    seconds: Elapsed


class VerifyResult(Result):
    """Every start, cut, seam and landing measured on the finished film."""

    reports_findings: ClassVar[bool] = True

    run: Run
    film: ProjectPath = Field(description="The film this call measured, project-relative.")
    film_seconds: float = Field(ge=0, description="How long that film runs.")
    starts: tuple[StartCheck, ...] = Field((), description="The first frame of every section, in film order.")
    cuts: tuple[CutCheck, ...] = Field((), description="Every seam between two sections, in film order.")
    seams: tuple[SeamCheck, ...] = Field((), description="Every section's drift from its own clock, in film order.")
    cues: tuple[CueCheck, ...] = Field((), description="Every cue measured against its word, in film order.")
    seconds: Elapsed


class BuildResult(Result):
    """A whole run: which stages ran, how each ended, what it cost and what it left behind."""

    reports_findings: ClassVar[bool] = True
    spends: ClassVar[bool] = True

    run: Run
    written: Written
    stages: tuple[StageRun, ...] = Field(description="Every stage this run planned, in run order.")
    voice: Voicing = Field(description="Whether this run spent on speech or wrote placeholders.")
    spend: Spend = Field(description="What this run cost, or would have cost.")
    film: ProjectPath | None = Field(None, description="The finished film, or null when the run made none.")
    storyboard: ProjectPath | None = Field(None, description="The storyboard this run wrote, or null.")
    stopped_at: Stage | None = Field(
        None,
        description="The stage whose certain findings stopped the run before the film, or null when it ran through.",
    )
    seconds: Elapsed


class ClipResult(Result):
    """A span of one built section, cut into its own file."""

    run: Run
    written: Written
    section: SectionNumber
    film: ProjectPath = Field(description="The clip this call wrote, project-relative.")
    words: ProjectPath = Field(description="The clip's own words file, project-relative.")
    start: float = Field(ge=0, description="The first frame's time in the section, in seconds.")
    end: float = Field(ge=0, description="The time just after the last frame, in seconds.")
    seconds: float = Field(ge=0, description="How long the clip runs, including its hold.")
    hold_seconds: float = Field(ge=0, description="How long the last frame is held after the span.")
    gain_db: float = Field(description="How much the clip's audio was lifted or cut.")
    estimated: bool = Field(description="True when the words come from a build with placeholder narration.")


class ApplyResult(Result):
    """What applying a set of fixes changed, and what it left alone."""

    run: Run
    written: Written
    fixes: tuple[FixOutcome, ...] = Field(description="Every fix this call considered, in the order it met them.")


RESULTS: dict[str, type[Result]] = {
    re.sub(r"(?<=[a-z])(?=[A-Z])", "-", kind.__name__.removesuffix("Result")).lower(): kind
    for kind in sorted(Result.__subclasses__(), key=lambda kind: kind.__name__)
}
"""Every result by the name `decktalk schema NAME` prints it under, which is its command's own name.

The name is spelled from the class, so `ConfigExplainResult` is `config-explain`. It is read when this
module loads, so a result declared anywhere else never joins it.
"""


__all__ = [
    "ApplyResult",
    "AssembleResult",
    "BuildResult",
    "CheckResult",
    "ClipResult",
    "ConfigExplainResult",
    "ConfigGetResult",
    "ConfigListResult",
    "ConfigSetResult",
    "ConfigUnsetResult",
    "CueCheck",
    "CueResult",
    "CueTime",
    "CutCheck",
    "DoctorResult",
    "ErrorResult",
    "FixOutcome",
    "InitResult",
    "InstallResult",
    "InstalledTool",
    "Layer",
    "LayerValue",
    "LiveRun",
    "Loudness",
    "NarrateResult",
    "NumberView",
    "Panel",
    "RecordResult",
    "RenderedSection",
    "Result",
    "Scope",
    "SeamCheck",
    "SectionCues",
    "SectionKind",
    "SectionRecording",
    "SectionStatus",
    "SectionTake",
    "SectionWords",
    "ServeResult",
    "SettingValue",
    "SkipReason",
    "SoundItem",
    "SoundKind",
    "SoundStatus",
    "SoundscapeResult",
    "Spend",
    "SpendState",
    "StageRun",
    "StartCheck",
    "StatusResult",
    "StoryboardResult",
    "Substitute",
    "TakeStatus",
    "VerifyResult",
    "Voicing",
    "Word",
    "WordsResult",
]
