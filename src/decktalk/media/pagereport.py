"""The one reader of what a page hands back, so nothing above this module trusts a page's own words.

`window.__dtprobe.report()` answers with the ten fields `page.REPORT` names, written by JavaScript
that a deck's own script shares a window with. Every row therefore arrives here and is read into a
model before anything above uses it, and a row this contract cannot read is dropped with the reason
rather than carried into an artifact or into a verdict.

Every warning carries the finding code the page named, because `record` dispatches on the code. The
channel this replaces was a sentence classified by matching substrings, with the sentence itself
spelled in the runtime, in the recorder and in a test. A code the finding vocabulary does not hold
is a page and a library that have drifted apart, so the row is refused rather than raised.

The page speaks camelCase and the artifacts are snake_case, so this is the one boundary where the
two names of a field sit beside each other, and `page.REPORT` is what holds the field list honest.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..findings import MODEL, Code, RaisedBy
from ..page import REPORT
from . import MILLISECONDS

PAGE = ConfigDict(frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True)
"""The configuration every row here uses, which refuses a field the page contract does not name."""

CATALOG = ConfigDict(frozen=True, extra="allow", populate_by_name=True, serialize_by_alias=True)
"""The catalog's own configuration, which keeps what it does not name, because `pagescan` reads the rest."""


class PageWarningRow(BaseModel):
    """One thing the page could not honour, as the code it carries and the sentence it printed."""

    model_config = PAGE

    code: Code = Field(description="The page code this warning raises, which is what a check dispatches on.")
    message: str = Field(description="The sentence the page printed, which is written for a person to read.")
    slide: str | None = Field(None, description="The slide this is about, or null.")
    cue: str | None = Field(None, description="The wire id of the cue this is about, or null.")
    attr: str | None = Field(None, description="The attribute this is about, or null.")

    @field_validator("code")
    @classmethod
    def raised_in_the_browser(cls, code: Code) -> Code:
        """Refuse a code the page has no business raising, which is every code DeckTalk measures itself.

        Half the page codes are measured from the frames or from the catalog after the run, and a
        page that reported one of those would be a deck deciding its own verdict.
        """
        if code.raised_by is not RaisedBy.RUNTIME:
            raise ValueError(f"{code.name} is measured by DeckTalk and is never reported by the page")
        return code


class CueRow(BaseModel):
    """One cue as the page ran it, in seconds on the narration clock."""

    model_config = PAGE

    id: str = Field(description="The wire id of the cue that fired.")
    due: float = Field(description="The second the cue was due.")
    ran: float = Field(description="The second the cue actually ran.")
    frame: float | None = Field(None, description="The second the frame that ran it began, or null before t=0.")
    describe: str | None = Field(None, description="What this cue's reveals describe themselves as, or null.")
    next: float | None = Field(None, description="The second the frame after that one began, or null.")
    after: float | None = Field(None, description="The second the frame after that one began, or null.")


class WordRow(BaseModel):
    """One line shown word by word, reported once its first word is on screen."""

    model_config = PAGE

    text: str = Field(description="The opening of the line, which is enough to find it in the script.")
    cue_at: float = Field(alias="cueAt", description="The second the cue that started the line ran.")
    run_at: float = Field(alias="runAt", description="The second the voice reaches the line's first word.")
    count: int = Field(ge=0, description="How many words the line holds.")
    first_shown: float = Field(alias="firstOn", description="The second the first word was drawn.")


class FrameGap(BaseModel):
    """One gap between two animation frames longer than the recorder can absorb."""

    model_config = PAGE

    at: float | None = Field(description="The second the gap ended, or null when it ended before t=0.")
    ms: int = Field(ge=0, description="How long the gap lasted.")


class LongFrame(BaseModel):
    """One animation frame that took longer than a captured frame, with when it was presented."""

    model_config = PAGE

    start: float | None = Field(description="The second the frame began, or null before t=0.")
    ms: int = Field(ge=0, description="How long the frame took.")
    render: float | None = Field(None, description="The second rendering began, or null when unreported.")
    presented: float | None = Field(None, description="The second the frame was presented, or null.")


class Box(BaseModel):
    """Where one element sits in the frame, in the picture's own pixels."""

    model_config = PAGE

    x: float
    y: float
    w: float
    h: float


class ElementRow(BaseModel):
    """One measured element of a slide, which is every element a slide draws whether or not it is cued."""

    model_config = PAGE

    attrs: dict[str, str] = Field(default_factory=dict, description="Every contract attribute the element carries.")
    moments: dict[str, str] = Field(default_factory=dict, description="Each moment attribute against its wire id.")
    text: str = Field("", description="The element's text, collapsed and cut to the contract's length.")
    box: Box


class SceneCatalog(BaseModel):
    """One scene of the catalog the page published, with the boxes the probe measured onto it.

    The catalog is the page's own document and `pagescan.py` is its reader, so what this model does
    not name is kept rather than dropped, and what it does name is read.
    """

    model_config = CATALOG

    scene: str = Field(description="The scene this entry is about.")
    elements: dict[str, tuple[ElementRow, ...]] = Field(
        default_factory=dict, description="The measured rows of each slide, keyed by the slide id."
    )


class PageReport(BaseModel):
    """Everything one page said about itself, read once, at the boundary where the page stops being trusted."""

    model_config = PAGE

    version: str | None = Field(None, description=REPORT["version"])
    mode: str | None = Field(None, description=REPORT["mode"])
    scene: str | None = Field(None, description=REPORT["scene"])
    slide: str | None = Field(None, description=REPORT["slide"])
    warnings: tuple[PageWarningRow, ...] = Field((), description=REPORT["warnings"])
    catalog: tuple[SceneCatalog, ...] = Field((), description=REPORT["catalog"])
    cues: tuple[CueRow, ...] = Field((), description=REPORT["cues"])
    words: tuple[WordRow, ...] = Field((), description=REPORT["words"])
    frame_gaps: tuple[FrameGap, ...] = Field((), alias="frameGaps", description=REPORT["frameGaps"])
    long_frames: tuple[LongFrame, ...] = Field((), alias="longFrames", description=REPORT["longFrames"])
    unreadable: tuple[str, ...] = Field(
        (), description="One sentence per row this contract could not read, which is a page that went its own way."
    )

    @property
    def worst_gap_ms(self) -> int:
        """The longest stall a viewer can see, which is the part of each gap that falls after t=0.

        Frames before t=0 sit under the cover and are trimmed from the cut, so a scene may warm up
        there. A gap is recorded when it ends, so a gap that began before t=0 counts only what fell
        after it, and a gap with no time on the narration clock counts nothing.
        """
        visible = (0.0 if gap.at is None else min(gap.ms, gap.at * MILLISECONDS) for gap in self.frame_gaps)
        return int(max((seen for seen in visible if seen > 0), default=0))


ROWS: dict[str, type[BaseModel]] = {
    "warnings": PageWarningRow,
    "catalog": SceneCatalog,
    "cues": CueRow,
    "words": WordRow,
    "frameGaps": FrameGap,
    "longFrames": LongFrame,
}
"""Each list field of the report against the model one of its rows has to be, which is what `read` walks."""


class Recording(BaseModel):
    """One section recorded: what the page loaded, what it said, and where narration t=0 sits in the webm.

    This is what the recorder knows. Whether the recording still matches the project, and what the
    frames of it show, are the stage's to add when it writes the log. It is declared beside the
    report it carries rather than beside the recorder that fills it, so an artifact can hold one
    whole without importing the browser driver.
    """

    model_config = MODEL

    url: str = Field(description="The page URL that was recorded, with its query.")
    assets: tuple[str, ...] = Field(description="Every project file the page loaded, project-relative.")
    external: tuple[str, ...] = Field(description="Every other origin the page reached for while recording.")
    requested_seconds: float = Field(ge=0, description="How long the page was recorded for after the clock started.")
    load_seconds: float = Field(ge=0, description="How long the page took to load.")
    settle_seconds: float = Field(ge=0, description="How long the page was left to settle after it loaded.")
    clock_start_seconds: float = Field(ge=0, description="Seconds from the recorder's start to narration t=0.")
    page_errors: tuple[str, ...] = Field(description="Uncaught exceptions, or the one line for no runtime at all.")
    report: PageReport = Field(description="What the page said about itself, read once.")


def _rows(field: str, given: object) -> tuple[list[BaseModel], list[str]]:
    """The rows of one list field that read, and a sentence for each that did not.

    A page that reports a row this contract cannot read has gone its own way, which is worth saying
    once rather than failing the recording, because the rest of what the page said is still true.
    """
    model = ROWS[field]
    if not isinstance(given, list):
        return [], [f"the page reported {field} as {type(given).__name__} rather than a list"]
    kept: list[BaseModel] = []
    refused: list[str] = []
    for index, row in enumerate(given):
        try:
            kept.append(model.model_validate(row))
        except ValidationError as invalid:
            refused.append(f"{field}[{index}] is not a row this contract can read: {invalid.error_count()} problems")
    return kept, refused


def read(given: object) -> PageReport:
    """The report one page answered with, read into models, with every row this contract cannot read named.

    The whole answer is refused only when it is not an object at all, because that is a page with no
    probe in it rather than a page with one bad row.
    """
    if not isinstance(given, dict):
        said = f"the page answered with {type(given).__name__} rather than a report"
        return PageReport(unreadable=(said,))
    fields: dict[str, object] = {name: given.get(name) for name in ("version", "mode", "scene", "slide")}
    unreadable: list[str] = []
    for field in ROWS:
        kept, refused = _rows(field, given.get(field, []))
        fields[field] = tuple(kept)
        unreadable += refused
    return PageReport.model_validate({**fields, "unreadable": tuple(unreadable)})
