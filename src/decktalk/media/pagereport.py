"""The one reader of what a page hands back, so nothing above this module trusts a page's own words.

`window.__decktalkProbe.report()` answers with the ten fields `page.REPORT` names, written by JavaScript
that a deck's own script shares a window with. Every row therefore arrives here and is read into a
model before anything above uses it, and a row this contract cannot read is dropped with the reason
rather than carried into an artifact or into a verdict.

Every warning carries the finding code the page named, because `record` dispatches on the code and
never matches a sentence against a substring. A code the finding vocabulary does not hold
is a page and a library that have drifted apart, so the row is refused rather than raised.

The page speaks camelCase and the artifacts are snake_case, so this is the one boundary where the
two names of a field sit beside each other, and `page.REPORT` is what holds the field list honest.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator

from .. import page
from ..findings import Code, Model, RaisedBy
from ..page import REPORT, Attr, stagger_span


class PageWarningRow(Model):
    """One thing the page could not honour, as the code it carries and the sentence it printed."""

    code: Code = Field(description="The page code this warning raises, which is what a check dispatches on.")
    message: str = Field(description="The sentence the page printed, which is written for a person to read.")
    slide: str | None = Field(None, description="The slide this is about, or null.")
    cue: str | None = Field(None, description="The cue id of the cue this is about, or null.")
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


class CueRow(Model):
    """One cue as the page ran it, in seconds on the section clock."""

    id: str = Field(description="The cue id of the cue that fired.")
    due: float = Field(description="The second the cue was due.")
    ran: float = Field(description="The second the cue actually ran.")
    frame: float | None = Field(None, description="The second of the frame that showed it, or null before t=0.")
    describe: str | None = Field(
        None,
        description="What this cue's reveals describe themselves as that an earlier cue of its slide did not, or null.",
    )


class WordRow(Model):
    """One line shown word by word, reported once its first word is on screen."""

    text: str = Field(description="The opening of the line, which is enough to find it in the script.")
    cue_at: float = Field(alias="cueAt", description="The second the cue that started the line ran.")
    spoken_at: float = Field(alias="spokenAt", description="The second the voice reaches the line's first word.")
    count: int = Field(ge=0, description="How many words the line holds.")
    first_shown: float = Field(alias="firstShown", description="The second the first word was drawn.")


class Landing(Model):
    """One cue a motion lands on, and whether it departs or arrives there."""

    cue: str = Field(description="The cue id the motion lands on.")
    kind: Literal["depart", "arrive"] = Field(description="Whether the motion departs or arrives at this cue.")
    at: float = Field(description="The second on the section clock the cue was due.")


class Size(Model):
    """How big one element is, in the picture's own pixels."""

    w: float
    h: float


class Box(Model):
    """Where one element sits in the frame, in the picture's own pixels."""

    x: float
    y: float
    w: float
    h: float


class MotionLane(Model):
    """One declared motion, loop, region or pane clip as the recorder saw it, which verify masks and measures on.

    A reveal is a lane of kind `reveal`: every element one cue revealed or changed, sampled from the
    cue for as long as a cue may move, departing on the cue. Its `departs` and `arrives` are the cue's
    second and the end of that span, never the seconds a page happened to step, so pieces join to the
    one pass. The report keeps reveals apart from the
    lanes, because verify measures a reveal as a step in the whole frame and never masks it. A film's
    landings file carries them, so a pane that plays the film measures each reveal on its box.

    The box is in the recording's own pixels, the union of where the element was on every frame it
    moved, so a mask over it hides the whole lane and nothing else. `rest` is where the element stood
    the first frame it was seen, exactly as the browser laid it out, and `moved` the first second it
    stood anywhere else, so whether an element held still over a span is read from the two rather than
    from rounded pixels. A pane clip's lane is the pane from its cue on, and names the project file it
    played.
    """

    id: str = Field(description="The lane's own name, which no cue shares.")
    kind: Literal["motion", "loop", "region", "clip", "reveal"] = Field(
        description="A motion, a loop, a region a frame hook draws in, a pane clip, or a reveal a film carries."
    )
    box: Box | None = Field(None, description="The box the lane swept, in the recording's pixels, or null when unseen.")
    size: Size | None = Field(None, description="How big the moving element is, in the recording's pixels.")
    departs: float | None = Field(None, description="The first second the lane moved, or null when it never did.")
    arrives: float | None = Field(None, description="The last second the lane moved, or null when it never did.")
    rest: Box | None = Field(
        None,
        description="Where the element stood the first frame the recorder saw it, to a fraction of a pixel, or null "
        "when unseen.",
    )
    moved: float | None = Field(
        None,
        description="The first second the element stood anywhere but `rest` or at another size, or null when it "
        "held still for as long as it was seen.",
    )
    landings: tuple[Landing, ...] = Field((), description="Every cue the lane lands on, which verify measures.")
    clip: str | None = Field(None, description="The project file a pane clip played, or null for any other lane.")


class ElementRow(Model):
    """One measured element of a slide, which is every element a slide draws whether or not it is cued."""

    attrs: dict[str, str] = Field(default_factory=dict, description="Every contract attribute the element carries.")
    moments: dict[str, str] = Field(default_factory=dict, description="Each moment attribute against its cue id.")
    text: str = Field("", description="The element's text, collapsed and cut to the contract's length.")
    children: int = Field(0, ge=0, description="How many children a staggered element reveals one after another.")
    box: Box

    @property
    def cue(self) -> str | None:
        """The cue id of this element's entrance, which is the moment every span is measured from."""
        return self.moments.get(Attr.IN.value)

    @property
    def described(self) -> bool:
        """True when the element says in words what it changes, which the transcript carries."""
        return bool(self.attrs.get(Attr.DESCRIBE.value) or self.attrs.get(Attr.DESCRIBE_CLASS.value))

    @property
    def entrance(self) -> float:
        """How long this element's entrance plays, from the seconds it declares or the style it names."""
        declared = self.attrs.get(Attr.IN_SECONDS.value)
        if declared:
            return float(declared)
        style = self.attrs.get(Attr.IN_STYLE.value) or page.ATTRS[Attr.IN_STYLE].default or ""
        effect = page.ENTRANCES.get(style)
        return effect.seconds if effect else 0.0

    def span(self, scale: float) -> float:
        """The seconds of motion this element puts between its own cue and the next one.

        The scale is applied without the clamp the runtime puts on it, because this is the
        judgement that tells an author the scale they chose has made their own cues unmeasurable.
        A staggered container's span is its step times the children after the first plus one
        entrance, which is exact arithmetic rather than an estimate, so its judgement is an error.
        The number of children is what the probe counted on the page, because `data-stagger` gives the
        step between children and says nothing about how many there are.
        """
        entrance = self.entrance * scale
        step = self.attrs.get(Attr.STAGGER.value)
        if not step:
            return entrance
        return stagger_span(float(step) * scale, self.children, entrance)


class MotionRow(Model):
    """One motion, loop or pane clip a slide declares, with the cue ids it departs and arrives on."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["motion", "loop", "clip"] = Field(
        description="A motion between two cues, a loop for the whole slide, or a pane clip from one cue."
    )
    name: str = Field(description="The @keyframes the element moves through, or the project file a pane clip plays.")
    departs: str | None = Field(None, alias="from", description="The cue id the motion departs at, or null.")
    arrives: str | None = Field(None, alias="to", description="The cue id the motion arrives at, or null.")
    ease: str | None = Field(None, description="How the motion moves between its cues, or null for a loop.")
    seconds: float | None = Field(None, description="How long one turn of a loop takes, or null for a motion.")


class MeasuredScene(Model):
    """One scene of the catalog the page published, with the boxes the probe measured onto it.

    The catalog is the page's own document and `pagescan.py` is its reader, so what this model does
    not name is kept rather than dropped, and what it does name is read. It is named for what it
    carries rather than for the field it arrives under, because the contract walker behind
    `decktalk schema` is the catalog and no published name may be read as that one.
    """

    # What a scene does not name is kept, because `pagescan` reads the rest of the catalog.
    model_config = ConfigDict(extra="allow")

    scene: str = Field(description="The scene this entry is about.")
    elements: dict[str, tuple[ElementRow, ...]] = Field(
        default_factory=dict, description="The measured rows of each slide, keyed by the slide id."
    )
    slides: tuple[str, ...] = Field((), description="The slides of this scene, in the order the page declares them.")
    cues: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, description="The cue ids of the cues each slide declares, keyed by the slide id."
    )
    motions: dict[str, tuple[MotionRow, ...]] = Field(
        default_factory=dict, description="Every motion, loop and pane clip each slide declares, keyed by the slide id."
    )
    spans: dict[str, float] = Field(
        default_factory=dict, description="How long each moment and class change plays, keyed by its cue id."
    )


class PageReport(Model):
    """Everything one page said about itself, read once, at the boundary where the page stops being trusted."""

    version: str | None = Field(None, description=REPORT["version"])
    mode: str | None = Field(None, description=REPORT["mode"])
    scene: str | None = Field(None, description=REPORT["scene"])
    slide: str | None = Field(None, description=REPORT["slide"])
    warnings: tuple[PageWarningRow, ...] = Field((), description=REPORT["warnings"])
    catalog: tuple[MeasuredScene, ...] = Field((), description=REPORT["catalog"])
    cues: tuple[CueRow, ...] = Field((), description=REPORT["cues"])
    words: tuple[WordRow, ...] = Field((), description=REPORT["words"])
    motions: tuple[MotionLane, ...] = Field((), description=REPORT["motions"])
    reveals: tuple[MotionLane, ...] = Field((), description=REPORT["reveals"])
    unreadable: tuple[str, ...] = Field(
        (), description="One sentence per row this contract could not read, which is a page that went its own way."
    )


ROWS: dict[str, type[Model]] = {
    "warnings": PageWarningRow,
    "catalog": MeasuredScene,
    "cues": CueRow,
    "words": WordRow,
    "motions": MotionLane,
    "reveals": MotionLane,
}
"""Each list field of the report against the model one of its rows has to be, which is what `read` walks."""


class UndeclaredChange(Model):
    """One element a script changed the picture in between two steps, inside nothing the page declared as moving.

    The page names the element and the recorder counts the frames, because only a captured frame that
    differs from the one before says the change was seen.
    """

    what: str = Field(description="The element the change was made in, as the page names it.")
    at: float = Field(ge=0, description="The first second on the section clock a frame showed the change.")
    frames: int = Field(gt=0, description="How many frames showed a change made in it.")


class Recording(Model):
    """One section recorded: what the page loaded, what it said, and the rate its clock was stepped at.

    This is what the recorder knows. Whether the recording still matches the project, and what the
    frames of it show, are the stage's to add when it writes the log. It is declared beside the
    report it carries rather than beside the recorder that fills it, so an artifact can hold one
    whole without importing the browser driver.
    """

    url: str = Field(description="The page URL that was recorded, with its query.")
    assets: tuple[str, ...] = Field(description="Every project file the page loaded, project-relative.")
    external: tuple[str, ...] = Field(description="Every other origin the page reached for while recording.")
    missing: tuple[str, ...] = Field((), description="Every project file the page asked for that is not there.")
    requested_seconds: float = Field(
        ge=0, description="How many seconds of section clock the page was stepped through."
    )
    fps: int = Field(
        gt=0, description="The rate the section clock was stepped at, which is one frame of the video a step."
    )
    load_seconds: float = Field(ge=0, description="How long the page took to load and paint before its first step.")
    page_errors: tuple[str, ...] = Field(description="Uncaught exceptions, or the one line for no runtime at all.")
    report: PageReport = Field(description="What the page said about itself, read once.")
    undeclared: tuple[UndeclaredChange, ...] = Field(
        (), description="Every element a script changed the picture in with nothing declared to explain it."
    )
    pieces: tuple[int, ...] = Field(
        (0,),
        description="The first frame of each piece the video was joined from, which is one piece when one page "
        "recorded it whole.",
    )
    steps: int = Field(
        0,
        ge=0,
        description="How many times the pages that recorded it were moved, which is what recording it cost, or 0 "
        "when a command drew it.",
    )

    def pane_clips(self) -> tuple[MotionLane, ...]:
        """Every pane clip the page played from its cue, which is a clip lane naming a file the origin served it.

        The lane is the page's word and the served file is the origin's, so a page that names a file it
        never played, or a file the origin would not serve it, plays nothing in the film.
        """
        return tuple(
            lane
            for lane in self.report.motions
            if lane.kind == "clip" and lane.clip is not None and lane.clip in self.assets and lane.landings
        )


def _rows(field: str, given: object) -> tuple[list[Model], list[str]]:
    """The rows of one list field that read, and a sentence for each that did not.

    A page that reports a row this contract cannot read has gone its own way, which is worth saying
    once rather than failing the recording, because the rest of what the page said is still true.
    """
    model = ROWS[field]
    if not isinstance(given, list):
        return [], [f"the page reported {field} as {type(given).__name__} rather than a list"]
    kept: list[Model] = []
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
    fields: dict[str, object] = {}
    unreadable: list[str] = []
    for name in ("version", "mode", "scene", "slide"):
        said = given.get(name)
        if said is None or isinstance(said, str):
            fields[name] = said
        else:
            unreadable.append(f"the page reported {name} as {type(said).__name__} rather than text")
    for field in ROWS:
        kept, refused = _rows(field, given.get(field, []))
        fields[field] = tuple(kept)
        unreadable += refused
    return PageReport.model_validate({**fields, "unreadable": tuple(unreadable)})


def _once[T: Model](groups: Sequence[Sequence[T]]) -> tuple[T, ...]:
    """Every row of `groups` once, in the order the groups first hold it, a row being equal to one with its values."""
    seen: dict[str, T] = {}
    for group in groups:
        for row in group:
            seen.setdefault(row.model_dump_json(), row)
    return tuple(seen.values())


def _first_shown(groups: Sequence[Sequence[WordRow]]) -> tuple[WordRow, ...]:
    """Each line shown word by word once, at the earliest second any page saw its first word drawn.

    A page draws a line's words at the seconds it is stepped to, and a page that started after the
    line began only stepped the seconds work was due before its first frame, so it may see the first
    word later than the page whose frames hold it. The page whose frames hold it saw it at its own
    second, which is the earliest any page saw. Lines are told apart by what they are and how many
    times a page reported one of them, and stay in the order they were first shown.
    """
    best: dict[tuple[str, int], tuple[WordRow, int, int]] = {}
    for piece, group in enumerate(groups):
        counted: dict[str, int] = {}
        for at, row in enumerate(group):
            line = row.model_dump_json(exclude={"first_shown"})
            nth = counted[line] = counted.get(line, 0) + 1
            kept = best.get((line, nth))
            if kept is None or row.first_shown < kept[0].first_shown:
                best[(line, nth)] = (row, piece, at)
    ordered = sorted(best.values(), key=lambda kept: (kept[0].first_shown, kept[1], kept[2]))
    return tuple(row for row, _piece, _at in ordered)


def _swept(groups: Sequence[Sequence[MotionLane]]) -> tuple[MotionLane, ...]:
    """Each lane once, with the box every page saw it sweep and the first and last second any page saw it move.

    A page notes a lane at every second it is stepped to, and each page stepped some of the seconds
    of one recording, so the lane the recording swept is the union of what the pages saw. Its size is
    the one seen at the first second it moved. A page declares lanes as it reaches them, so the pages
    that started later hold every lane the earlier ones declared, in the order the page declared them.
    """
    seen: dict[str, list[MotionLane]] = {}
    # The page that declared the most lanes goes first, so the lanes keep the order the page declared them in.
    for group in sorted(groups, key=len, reverse=True):
        for lane in group:
            seen.setdefault(lane.id, []).append(lane)
    return tuple(_lane(lanes) for lanes in seen.values())


def _lane(seen: Sequence[MotionLane]) -> MotionLane:
    """One lane as several pages saw it, joined into the lane one page stepping every second would have seen.

    It stands where the earliest page first saw it, and it moved at the first second any page saw it
    move, or at the first second a later page saw it standing anywhere else.
    """
    drawn = [lane for lane in seen if lane.box is not None and lane.departs is not None and lane.arrives is not None]
    if not drawn:
        return seen[0]
    boxes = [lane.box for lane in drawn if lane.box is not None]
    left, top = min(box.x for box in boxes), min(box.y for box in boxes)
    right, bottom = max(box.x + box.w for box in boxes), max(box.y + box.h for box in boxes)
    first = min(drawn, key=lambda lane: lane.departs if lane.departs is not None else 0.0)
    moved = [lane.moved for lane in drawn if lane.moved is not None]
    moved += [lane.departs for lane in drawn if lane.rest != first.rest and lane.departs is not None]
    return first.model_copy(
        update={
            "box": Box(x=left, y=top, w=right - left, h=bottom - top),
            "arrives": max(lane.arrives for lane in drawn if lane.arrives is not None),
            "moved": min(moved, default=None),
        }
    )


def joined(reports: Sequence[PageReport]) -> PageReport:
    """What several pages that each recorded a range of one section said, as one page recording all of it said.

    Each page loaded the section and stepped every second work was due before its own frames, so
    every cue that ran and every warning raised before a page's last frame is in its report, and the
    page that recorded the last frames saw the section end. What a page measures only on the frames
    it draws, the lanes a motion swept, the boxes a reveal was drawn in and the second a line's first
    word was shown, is joined from every page. `reports` is in frame order.
    """
    if len(reports) == 1:
        return reports[0]
    last = reports[-1]
    return last.model_copy(
        update={
            "warnings": _once([report.warnings for report in reports]),
            "cues": _once([report.cues for report in reports]),
            "words": _first_shown([report.words for report in reports]),
            "motions": _swept([report.motions for report in reports]),
            "reveals": _swept([report.reveals for report in reports]),
            "unreadable": tuple(dict.fromkeys(line for report in reports for line in report.unreadable)),
        }
    )
