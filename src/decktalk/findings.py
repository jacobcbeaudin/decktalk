"""A finding: the code a caller dispatches on, the sentence a reader meets, where it is, and the fix.

A finding is a judgement about the film or about the files it is made from. It is never an error,
because an error means DeckTalk could not run at all. Every judgement in the product is one of
these, so a reader dispatches on a code and never on the absence of one.

`Code` is the closed list of every judgement DeckTalk can make. A member carries its own sentence,
its certainty and the side that raises it, so those sentences live once and the docs page,
the JSON Schema and the printed line are three renderings of one row. A code never spells its own
certainty, because an agent dispatching on a code would then meet two codes for one condition and
have to know that one is the other's hedge.

`MODEL` is the configuration every frozen model in the package shares, and `ProjectPath` is how
every path is serialised. Both are declared here because the finding is the lowest model in the
package and everything else that models anything sits above it.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from decktalk.pipeline import Stage

MODEL = ConfigDict(frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True)
"""The configuration every model in the package uses, so a result is hashable, closed and one shape."""


class Model(BaseModel):
    """The base of every model in the package, which is what carries `MODEL` to each of them."""

    model_config = MODEL


ProjectPath = Annotated[Path, PlainSerializer(Path.as_posix, return_type=str)]
"""A path as JSON receives it, which is project-relative with forward slashes on every platform."""

DOCS = "https://docs.decktalk.ai/reference"
"""Where every code's page lives, which is the one prefix a printed line and a schema both carry."""


class Certainty(Enum):
    """Whether a finding is wrong for sure or only probably wrong.

    The threshold a run fails on is `--fail-on`, which names a threshold and never a field value, so
    these two words are read and never compared against a flag.
    """

    CERTAIN = "certain"
    UNCERTAIN = "uncertain"


class RaisedBy(Enum):
    """Which side of the product reports a condition, which decides who owns its test.

    A runtime code is reported by the page itself and owes a browser test. A python code is measured
    by a stage from a file, a frame or the catalog the page published.
    """

    sentence: str

    def __new__(cls, side: str, sentence: str) -> RaisedBy:
        member = object.__new__(cls)
        member._value_ = side
        member.sentence = sentence
        return member

    RUNTIME = "runtime", "the page reports it from the browser"
    PYTHON = "python", "DeckTalk measures it from what the run produced"


class Code(Enum):
    """Every judgement DeckTalk can make, with its sentence, its certainty and who raises it.

    The member name is the code an agent dispatches on and passes to `--allow`. The prefix is the
    subject the finding judges, which groups the codes for sorting, for `--allow` and for the docs
    URL.

    A code does not list the settings that move it. Each settings key names the codes it decides,
    beside its range, and every rendering that shows a code's keys reads that one declaration in
    reverse, because two lists of one relation had drifted apart.
    """

    sentence: str
    certainty: Certainty
    raised_by: RaisedBy

    def __new__(
        cls,
        code: str,
        sentence: str,
        certainty: Certainty = Certainty.CERTAIN,
        raised_by: RaisedBy = RaisedBy.RUNTIME,
    ) -> Code:
        # A row that names no certainty is certain and one that names no side is the page's, because
        # most rows are both, and a row that differs says so where it is written.
        member = object.__new__(cls)
        member._value_ = code
        member.sentence = sentence
        member.certainty = certainty
        member.raised_by = raised_by
        return member

    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"

    @property
    def url(self) -> str:
        """The docs page for this code, which every printed finding and every schema row carries."""
        return f"{DOCS}/findings/{self.name}"

    # Reported by the page in the browser.
    PAGE_UNKNOWN_ATTR = (
        "PAGE_UNKNOWN_ATTR",
        "An element carries a data attribute the contract does not declare, so nothing reads it.",
    )
    PAGE_BAD_VALUE = (
        "PAGE_BAD_VALUE",
        "An attribute's value is outside the range the contract publishes for it.",
    )
    PAGE_MOMENT_UNKNOWN = (
        "PAGE_MOMENT_UNKNOWN",
        "An element outside every slide template names a moment, so no slide owns its cue and it never plays.",
    )
    PAGE_MOMENT_ORDER = (
        "PAGE_MOMENT_ORDER",
        "An element's exit is declared at or before its own entrance, so the exit never plays.",
    )
    PAGE_CUE_UNKNOWN = (
        "PAGE_CUE_UNKNOWN",
        "A cue fired that no reveal, no handler and no slide in the deck declares, so nothing answered it.",
    )
    PAGE_NO_OWNER = (
        "PAGE_NO_OWNER",
        "A cue the scene plays is owned by none of its slides, so nothing is mounted for it.",
    )
    PAGE_SCENE_EMPTY = (
        "PAGE_SCENE_EMPTY",
        "A scene holds no slide template, so its section has no slide to show.",
    )
    PAGE_SLIDE_NO_ID = (
        "PAGE_SLIDE_NO_ID",
        "A slide template carries no id, so no cue and no section can name it.",
    )
    PAGE_SLIDE_DOUBLED = (
        "PAGE_SLIDE_DOUBLED",
        "Two slide templates carry the same id, so a cue that names it is ambiguous.",
    )
    PAGE_SLIDE_UNUSED = (
        "PAGE_SLIDE_UNUSED",
        "No section plays this slide, so nothing on it reaches the film.",
    )
    PAGE_TEMPLATE_IGNORED = (
        "PAGE_TEMPLATE_IGNORED",
        "A template nested inside a slide declares no slide of its own, so nothing ever mounts it.",
    )
    PAGE_WORDS_NOT_FOUND = (
        "PAGE_WORDS_NOT_FOUND",
        "An element syncs to words the transcript does not hold, so word timing cannot start.",
    )
    PAGE_KATEX_MISSING = (
        "PAGE_KATEX_MISSING",
        "The page asks for KaTeX and the library is not loaded, so every formula shows its source.",
    )
    PAGE_KATEX_ERROR = (
        "PAGE_KATEX_ERROR",
        "KaTeX refused a formula, so the element shows the readable text the author wrote in its place.",
    )
    PAGE_FREEZE_CUE_UNKNOWN = (
        "PAGE_FREEZE_CUE_UNKNOWN",
        "A freeze was asked for a cue the slide does not declare, so no frame could be made.",
    )
    PAGE_RENDER_THREW = (
        "PAGE_RENDER_THREW",
        "A slide's render threw, so the slide is drawn without what the render would have written.",
    )
    PAGE_ENTER_THREW = (
        "PAGE_ENTER_THREW",
        "A slide's enter threw, so its opening moment never ran.",
    )
    PAGE_SLIDE_HANDLER_THREW = (
        "PAGE_SLIDE_HANDLER_THREW",
        "A slide's own moment handler threw, so that moment did not play.",
    )
    PAGE_HANDLER_THREW = (
        "PAGE_HANDLER_THREW",
        "A moment handler threw, so the change it was to make never happened.",
    )
    PAGE_WAIT_REJECTED = (
        "PAGE_WAIT_REJECTED",
        "A wait the page declared was rejected, so the runtime carried on without what it waited for.",
    )
    PAGE_WAIT_UNSETTLED = (
        "PAGE_WAIT_UNSETTLED",
        "A wait the page declared never settled inside its budget, so the runtime carried on without it.",
    )
    PAGE_CLASS_UNDESCRIBED = (
        "PAGE_CLASS_UNDESCRIBED",
        "An element changes class with no description, so the transcript cannot say what changed.",
    )
    PAGE_CLASS_NOT_REDUCED = (
        "PAGE_CLASS_NOT_REDUCED",
        "A class change declares no reduced form, so a reduced render would animate anyway.",
    )
    PAGE_SWAP_AMBIGUOUS = (
        "PAGE_SWAP_AMBIGUOUS",
        "A swap finds no element or several elements leaving at its cue, so it cannot tell which one it replaces.",
    )
    PAGE_PREVIEW_AMBIGUOUS = (
        "PAGE_PREVIEW_AMBIGUOUS",
        "Two sections name one scene, so a preview cannot choose whose cue times to play.",
    )
    PAGE_APPEAR_TOO_LONG = (
        "PAGE_APPEAR_TOO_LONG",
        "A line shown with data-words=appear holds more words than one cue can carry one at a time.",
    )
    PAGE_STAGGER_EMPTY = (
        "PAGE_STAGGER_EMPTY",
        "An element staggers its children and has none, so the stagger plays nothing.",
    )

    # Measured in Python, about the page.
    PAGE_MOTION_OVERRUN = (
        "PAGE_MOTION_OVERRUN",
        "A motion span runs past the measurable ceiling, so the cue it carries cannot be verified.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_STAGGER_OVERRUN = (
        "PAGE_STAGGER_OVERRUN",
        "A staggered entrance totals past the measurable ceiling, and the arithmetic that says so is exact.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_THIN_DRAW = (
        "PAGE_THIN_DRAW",
        "A frozen slide draws less of the picture than a change must cross to be seen.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_NO_DESCRIPTION = (
        "PAGE_NO_DESCRIPTION",
        "An element changes the picture and describes nothing, so the transcript loses the change.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_SWAP_APART = (
        "PAGE_SWAP_APART",
        "A swap's two halves land far enough apart that a viewer sees the gap between them.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_STALLED = (
        "PAGE_STALLED",
        "The picture held still for longer than a recorded section ever should.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_BLACK = (
        "PAGE_BLACK",
        "A recorded frame is black, so the film shows nothing at that moment.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_TRUNCATED = (
        "PAGE_TRUNCATED",
        "A recording stopped before its section's clock ran out, so the film is short of picture.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_CDN_ASSET = (
        "PAGE_CDN_ASSET",
        "The page loads an asset from a network origin, so the film depends on somebody else's server.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    PAGE_RUNTIME_STALE = (
        "PAGE_RUNTIME_STALE",
        "The project's copy of the runtime is not the one this engine ships, so its pages play a contract "
        "this engine does not measure.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )

    # Measured in Python, about the script, the cues, the cut and the files.
    CUE_MISSING = (
        "CUE_MISSING",
        "The page declares a moment that cues.json does not list, so nothing gives it a second.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_UNKNOWN = (
        "CUE_UNKNOWN",
        "cues.json lists a cue no page declares, so nothing plays it.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_UNRESOLVED = (
        "CUE_UNRESOLVED",
        "The cue's phrase is not spoken in its section, so there is no second to place it at.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_STALE = (
        "CUE_STALE",
        "The cue times on disk were placed from a different cues.json than the project's, so the film "
        "is judged against moments nobody asked for until the project is built again.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_OFF = (
        "CUE_OFF",
        "The change lands further from its word than the offset limit allows.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_NO_ONSET = (
        "CUE_NO_ONSET",
        "The cue resolved with no measured onset, so its second is the section's start and not its word's.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_NO_CHANGE = (
        "CUE_NO_CHANGE",
        "Nothing in the picture changed at the cue's second, so the reveal never happened.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_THIN_CHANGE = (
        "CUE_THIN_CHANGE",
        "Less of the picture changed at the cue than a visible reveal must cross.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    CUE_OVERLAP = (
        "CUE_OVERLAP",
        "Two cues resolve close enough together that a viewer cannot tell them apart.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    TAKE_PLACEHOLDER = (
        "TAKE_PLACEHOLDER",
        "The script still holds an open placeholder, so a voiced run would read it out.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    TAKE_SPOKEN_SYMBOL = (
        "TAKE_SPOKEN_SYMBOL",
        "The script holds a symbol the voice reads as its name rather than as the thing it means.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    CUT_SPEECH = (
        "CUT_SPEECH",
        "Speech is still sounding at a section cut, so the film slices a word in two.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    CUT_POP = (
        "CUT_POP",
        "The picture steps at a section cut, so the film pops on the seam.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )
    MIX_LOUDNESS = (
        "MIX_LOUDNESS",
        "The mixed film misses the loudness it was mastered to.",
        Certainty.UNCERTAIN,
        RaisedBy.PYTHON,
    )
    FILE_MISSING = (
        "FILE_MISSING",
        "A file the project names is not on disk.",
        Certainty.CERTAIN,
        RaisedBy.PYTHON,
    )


class Applicability(Enum):
    """How much a fix may be trusted, which is what decides whether `--fix` applies it.

    A safe fix cannot lose the author's work, an unsafe fix can, and a display fix is a change only
    a person can make, so it is printed and never applied.
    """

    SAFE = "safe"
    UNSAFE = "unsafe"
    DISPLAY = "display"


class Location(Model):
    """Where a finding is, as the five facts that travel together into a renderer and into a fix.

    `where` is never null, because the object a finding judges is what tells a reader which side of
    the spend it came from, and the other four are filled by whoever knows them.
    """

    where: str = Field(description="The object this finding judges, such as a cue id, a slide or a file.")
    file: ProjectPath | None = Field(None, description="The file to open, project-relative, or null.")
    line: int | None = Field(None, ge=1, description="The line in that file, counting from one, or null.")
    section: int | None = Field(None, ge=0, description="The section number this is about, or null.")
    cue: str | None = Field(None, description="The wire id of the cue this is about, or null.")


class Edit(Model):
    """One change to one file, named by exactly one locator so an agent can apply it without guessing.

    A pointer addresses a place in a JSON file, a key addresses a place in a TOML file, and a line
    addresses a place in any other text file.
    """

    file: ProjectPath = Field(description="The file this edit changes, project-relative.")
    pointer: str | None = Field(None, description="A JSON pointer into that file, or null.")
    key: str | None = Field(None, description="A dotted TOML key in that file, or null.")
    line: int | None = Field(None, ge=1, description="A line number in that file, or null.")
    old: str | None = Field(None, description="What is there now, or null when the edit only adds.")
    new: str = Field(description="What goes there instead, which is the empty string when the edit removes.")

    @model_validator(mode="after")
    def _one_locator(self) -> Self:
        """An edit that named two places would leave the applier to choose between them."""
        named = [name for name, value in (("pointer", self.pointer), ("key", self.key), ("line", self.line)) if value]
        if len(named) != 1:
            found = ", ".join(named) or "nothing"
            raise ValueError(f"an edit names exactly one of pointer, key or line, and this one names {found}")
        return self

    def applied(self, lines: list[str]) -> list[str]:
        """The lines of a file once this line edit is made, which a fix plans its next edit against too."""
        index = (self.line or 1) - 1
        edited = list(lines)
        edited[index : index + (1 if self.old is not None else 0)] = [self.new + "\n"] if self.new else []
        return edited


class EditFix(Model):
    """A fix that changes files, which is what scaffolds a missing cue row or repairs a phrase."""

    kind: Literal["edit"] = Field("edit", description="The kind of fix, which is how a reader dispatches on it.")
    title: str = Field(description="One sentence saying what applying this fix does.")
    applicability: Applicability = Field(description="Whether this fix may be applied without asking.")
    edits: tuple[Edit, ...] = Field(description="Every change this fix makes, applied together or not at all.")


class SettingFix(Model):
    """A fix that turns a knob, which is the same key space `config set` and `--set` take."""

    kind: Literal["setting"] = Field("setting", description="The kind of fix, which is how a reader dispatches on it.")
    title: str = Field(description="One sentence saying what applying this fix does.")
    applicability: Applicability = Field(description="Whether this fix may be applied without asking.")
    key: str = Field(description="The settings key to set, such as verify.cue_offset_max_ms.")
    value: str = Field(description="The value to set it to, spelled as a command line would spell it.")


FIX_COMMANDS: frozenset[tuple[str, ...]] = frozenset({("decktalk", "install")})
"""Every command a fix may run, which is a closed set of DeckTalk's own calls.

A fix travels as JSON, and `apply` runs what it names. An open argv would let a fix built from JSON
run any program on the machine that applies it, so the set is closed and a new command fix is added
here by name.
"""


class CommandFix(Model):
    """A fix that runs one of DeckTalk's own commands, spelled as the argv a person would type."""

    kind: Literal["command"] = Field("command", description="The kind of fix, which is how a reader dispatches on it.")
    title: str = Field(description="One sentence saying what applying this fix does.")
    applicability: Applicability = Field(description="Whether this fix may be applied without asking.")
    command: tuple[str, ...] = Field(
        description="The command to run, one argument per element, and always one of DeckTalk's own calls."
    )

    @field_validator("command")
    @classmethod
    def _one_of_ours(cls, command: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse any command outside the closed set, so a fix read from JSON cannot name a program."""
        if command not in FIX_COMMANDS:
            allowed = ", ".join(" ".join(argv) for argv in sorted(FIX_COMMANDS))
            raise ValueError(
                f"a command fix runs only DeckTalk's own calls, which are {allowed}, "
                f"and {' '.join(command)!r} is not one of them"
            )
        return command


class RuntimeFix(Model):
    """A fix that replaces a project's copy of the runtime with the one this engine ships.

    A copy some release shipped holds none of the author's work, so its fix is safe. A copy that
    matches no shipped runtime holds edits that replacing it would lose, so its fix is unsafe. The
    fix names only where the copy is, because what goes there is always the engine's runtime.
    """

    kind: Literal["runtime"] = Field("runtime", description="The kind of fix, which is how a reader dispatches on it.")
    title: str = Field(description="One sentence saying what applying this fix does.")
    applicability: Applicability = Field(description="Whether this fix may be applied without asking.")
    file: ProjectPath = Field(description="The project's copy of the runtime to replace, project-relative.")


Fix = Annotated[EditFix | SettingFix | CommandFix | RuntimeFix, Field(discriminator="kind")]
"""The four moves an agent can make: editing a file, turning a knob, running a command and replacing the runtime."""


class Finding(Model):
    """One judgement, in the shape every renderer, every schema and every agent receives.

    `certainty` and `url` are the code's own and are written into the object rather than left for a
    reader to look up, so one line of JSON carries everything a decision needs. A raiser leaves them
    out and the code fills them, and a value that disagrees with the code is refused. Both therefore
    carry a declared default, which says in the signature and in the schema that a raiser names the
    code and nothing else. Neither default is ever the value in force, because the code fills both
    before this model is built and refuses any finding whose code it does not know.
    """

    code: Code = Field(description="The stable code a caller dispatches on, such as CUE_OFF.")
    message: str = Field(description="One sentence, with every measured number and its limit written into it.")
    certainty: Certainty = Field(
        Certainty.CERTAIN, description="Whether this is wrong for sure or only probably wrong."
    )
    location: Location = Field(description="The object this judges, with its file, line, section and cue.")
    stage: Stage | None = Field(None, description="The stage that raised it, or null when no stage did.")
    fix: Fix | None = Field(None, description="A change that resolves it, or null when none is known.")
    url: str = Field("", description="The docs page for this code.")

    @model_validator(mode="before")
    @classmethod
    def _fill_from_code(cls, data: object) -> object:
        """The code owns the certainty and the page, so a raiser names the code and nothing else."""
        if not isinstance(data, dict):
            return data
        code = data.get("code")
        member = code if isinstance(code, Code) else Code.__members__.get(code) if isinstance(code, str) else None
        if member is None:
            return data
        return {"certainty": member.certainty, "url": member.url, **data}

    @model_validator(mode="after")
    def _agrees_with_code(self) -> Self:
        """A finding whose certainty or page differed from its code's would publish two answers."""
        if self.certainty is not self.code.certainty:
            raise ValueError(f"{self.code.name} is {self.code.certainty.value} and this finding says {self.certainty}")
        if self.url != self.code.url:
            raise ValueError(f"{self.code.name} is documented at {self.code.url} and this finding says {self.url}")
        return self


def judge(
    code: Code, message: str, location: Location, *, stage: Stage | None = None, fix: Fix | None = None
) -> Finding:
    """One judgement, built through validation so the code fills its own certainty and its own page.

    A raiser names the code, the sentence, the place and sometimes the fix. Writing the certainty
    out beside the code would be the second spelling of one fact, which is what the code owning it
    exists to prevent.
    """
    return Finding.model_validate({"code": code, "message": message, "location": location, "stage": stage, "fix": fix})


__all__ = [
    "Applicability",
    "Certainty",
    "Code",
    "CommandFix",
    "Edit",
    "EditFix",
    "Finding",
    "Fix",
    "Location",
    "RaisedBy",
    "RuntimeFix",
    "SettingFix",
]
