"""The URL a page section is opened at, and what its recording is keyed on.

The page is opened on the local origin with the section's scene, the cues `cue` resolved and the
words the voice spoke, and it is recorded for its span in the narration plus its record margin. A
section whose frames stall is recorded again while the machine is quieter, up to `[record] retries`
times.

A section is keyed on that URL, which already carries the scene, the resolved cues and the spoken
words with their times, on the frame geometry, the colour scheme and the motion the render asks for,
and on what the page puts on screen for this one section: the markup of the scene it plays, the rest
of the page file, which every scene shares, and the content of every project file the page loaded
when it was last recorded. A run that would open the same page with the same everything would record
the same pixels, so `record` keeps the recording it has.

The key is cut that way because a page holds every scene of a film. A digest of the whole file would
call all nine sections of a nine-scene page stale for one slide's edit, which on the one-page project
`decktalk init` writes is every section there is. The scene slice is taken from the page source and
never from the browser, because `status` and `record --only` ask whether a recording still stands and
neither may open Chromium to find out. The asset list is what keeps the key honest in the other
direction: a page that swaps one picture for another changes no line of HTML, so the markup alone
would say nothing had moved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from decktalk.artifacts import CueTimes, RecordingLog, input_hash, text_digest
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection
from decktalk.media.origin import page_url
from decktalk.page import Q
from decktalk.results import Word

SIGNAL = "signal"
"""What `t0` is set to so the page starts its clock on the recorder's signal rather than on a second."""

SECOND_DIGITS = 3
"""Truth: three decimal places of a second is one millisecond, which is finer than any frame."""

WORD_DIGITS = 2
"""How precisely a word's start is written into the page URL, which is a hundredth of a second."""


def as_query(params: dict[str, str]) -> dict[Q, str]:
    """A section's own params as contract keys, refusing a key no page reads.

    The query vocabulary has one home, so a param that names a key outside it is a project file
    asking the page for something it cannot answer, which is worth refusing at load rather than
    sending and watching nothing happen.
    """
    known = {key.value: key for key in Q}
    unknown = sorted(name for name in params if name not in known)
    if unknown:
        raise InputError(
            f"a section's params name {', '.join(unknown)}, which no DeckTalk page reads.",
            hint=f"The query keys a page reads are {', '.join(sorted(known))}.",
        )
    return {known[name]: value for name, value in params.items()}


def scene_params(section: PageSection, cue_times: CueTimes | None) -> dict[Q, str]:
    """The section's own query parameters, plus its resolved cues unless the section sets `cues` itself."""
    params = as_query(dict(section.params))
    if cue_times is not None and Q.CUES not in params:
        query = cue_times.query(section.number)
        if query:
            params[Q.CUES] = query
    return params


def words_param(words: tuple[Word, ...]) -> str | None:
    """A section's words as word@seconds pairs, in seconds after that section starts.

    A comma separates two pairs and an at sign separates a word from its second, so neither may
    appear inside a word that is written into the value.
    """
    if not words:
        return None
    return ",".join(
        f"{word.word.replace(',', '').replace('@', '')}@{max(0.0, word.start):.{WORD_DIGITS}f}" for word in words
    )


def spoken_words(inputs: Inputs, section: int) -> tuple[Word, ...]:
    """One section's words in seconds after it starts, or nothing when it has no take yet."""
    takes = inputs.takes()
    take = takes.of(section) if takes is not None else None
    return () if take is None else inputs.words(section, take.hash)


def words_query(inputs: Inputs, section: PageSection) -> str | None:
    """The section's spoken words with their seconds, which is what a word-synced line is drawn on."""
    return words_param(spoken_words(inputs, section.number))


def scene_url(inputs: Inputs, section: PageSection, params: dict[Q, str]) -> str:
    """The local origin URL the recorder and the frozen frames open for one page section."""
    page = inputs.path(section.page)
    if not page.exists():
        raise InputError(
            f"section {section.number} plays {section.page}, which is not there.",
            hint="Write the page, or point the section at the file you meant.",
        )
    query: dict[Q, str] = {Q.SCENE: section.scene, **params}
    words = words_query(inputs, section)
    if words and Q.WORDS not in query:
        query[Q.WORDS] = words
    query[Q.T0] = SIGNAL
    return page_url(section.page, query)


# ---- the page, cut into the scene one section plays and the part every scene shares -----------

VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
"""Truth: the elements HTML gives no end tag, so an open one never holds anything."""


class SceneSpans(HTMLParser):
    """Where each `[data-scene]` element begins and ends in a page's source text.

    The runtime finds its scenes with `document.querySelectorAll("[data-scene]")`, so a span is one
    element carrying that attribute and everything inside it, taken from the file exactly as the
    author wrote it. Depth is counted on the wrapper's own tag name, which a `<script>`'s contents
    and an HTML comment cannot disturb because the parser hands neither back as a tag.

    A page whose scene element never closes leaves `balanced` false. Nothing is sliced then, and the
    caller keys the section on the whole file, which is what a recording was keyed on before this
    key could tell one scene from another.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        # The parser reports a position as a line and a column, so the offset each line starts at is
        # what turns that pair into an index into the file. The parser counts a line per newline and
        # nothing else, so this split has to agree with it.
        self.line_starts = [0]
        for line in source.split("\n"):
            self.line_starts.append(self.line_starts[-1] + len(line) + 1)
        self.spans: list[tuple[str, int, int]] = []
        self.open: tuple[str, str, int] | None = None
        self.depth = 0
        self.balanced = True

    @classmethod
    def of(cls, source: str) -> list[tuple[str, int, int]] | None:
        """Every scene's span in this page, or None when the page cannot be cut up with confidence."""
        parser = cls(source)
        parser.feed(source)
        parser.close()
        return parser.spans if parser.balanced and parser.open is None else None

    def at(self) -> int:
        """Where the tag the parser is on begins, as an index into the source."""
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            return
        if self.open is not None:
            if tag == self.open[0]:
                self.depth += 1
            return
        found = dict(attrs)
        if "data-scene" in found:
            self.open, self.depth = (tag, found["data-scene"] or "", self.at()), 1

    def handle_endtag(self, tag: str) -> None:
        if self.open is None or tag != self.open[0]:
            return
        self.depth -= 1
        if self.depth:
            return
        _tag, scene, start = self.open
        closed = self.source.find(">", self.at())
        self.spans.append((scene, start, len(self.source) if closed < 0 else closed + 1))
        self.open = None

    def close(self) -> None:
        super().close()
        # A wrapper still open at the end of the file was never closed, so no span can be trusted.
        if self.open is not None:
            self.balanced = False


@dataclass(frozen=True)
class PageParts:
    """One page's source in two pieces, as one section is recorded from it."""

    scene: str
    """The markup of the scene this section plays, or nothing when the page declares none."""

    shared: str
    """Everything else in the file: the head, the styles, the scripts and the other scenes."""


def page_parts(source: str, scene: str) -> PageParts:
    """Cut a page into the scene a section plays and the part every scene of that page shares.

    A page that declares its scenes in script has no `[data-scene]` element to cut out, and neither
    has a page the parser could not follow, so the whole file is shared and every section of it is
    recorded again whenever it is edited. That is the conservative answer, and it is the right one,
    because a render function is reached by any line of the script around it.
    """
    spans = SceneSpans.of(source)
    if spans is None or not any(found == scene for found, _start, _end in spans):
        return PageParts(scene="", shared=source)
    kept: list[str] = []
    cut = 0
    for _scene, start, end in spans:
        kept.append(source[cut:start])
        cut = end
    kept.append(source[cut:])
    own = "".join(source[start:end] for found, start, end in spans if found == scene)
    return PageParts(scene=own, shared="".join(kept))


def page_source(path: Path) -> str:
    """A page as text, or nothing when the project no longer has it, which `scene_url` reports."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def section_hash(inputs: Inputs, section: PageSection, url: str, seconds: float, assets: Sequence[str | Path]) -> str:
    """The digest of everything that decides what this section's recording looks like.

    The page joins the key in two pieces rather than as one file, so an edit to one scene moves the
    key of the sections that play it and of no others, while an edit to the head, a style, a script
    or another shared part of the file moves every section of that page. The motion the render asks
    for joins the key too, or a reduced build would reuse the full-motion recordings it made before.
    `assets` is every project file the page loaded, which the last run's log names, and the page file
    itself is left out of them because its two pieces are already here.
    """
    settings = inputs.settings
    video, record, motion = settings.video, settings.record, settings.motion
    page = inputs.path(section.page)
    parts = page_parts(page_source(page), section.scene)
    lines = [
        url,
        f"{seconds:.{SECOND_DIGITS}f}",
        f"{video.width}x{video.height}@{video.output_fps}",
        record.color_scheme,
        f"motion:{motion.reduce}:{motion.scale:g}",
        f"scene:{text_digest(parts.scene)}",
        f"page:{text_digest(parts.shared)}",
    ]
    named = [Path(rel).as_posix() for rel in assets]
    files = {rel: found for rel in named if (found := inputs.path(rel)) != page}
    return input_hash(lines, files)


@dataclass(frozen=True)
class Job:
    """One section the recorder is about to open, what it would be recorded from, and what is there now."""

    section: PageSection
    url: str
    seconds: float
    out: Path
    log_path: Path
    input_hash: str
    previous: RecordingLog | None
    """The log of the recording already on disk, when there is one."""

    @property
    def unchanged(self) -> bool:
        """Whether the recording on disk was made from these exact inputs, and is finished.

        A log with no narration t=0 in it belongs to a run that was stopped between placing the webm
        and measuring it, so the section is recorded again rather than assembled from a picture whose
        first frame nobody found.
        """
        return (
            self.previous is not None
            and bool(self.previous.input_hash)
            and self.previous.input_hash == self.input_hash
            and self.previous.t0_seconds is not None
            and self.out.exists()
        )


def plan_job(inputs: Inputs, section: PageSection, cue_times: CueTimes | None, seconds: float) -> Job:
    """What recording one section would open and write, and whether the recording on disk still stands."""
    url = scene_url(inputs, section, scene_params(section, cue_times))
    workspace = inputs.workspace
    previous = RecordingLog.read(workspace.recording_log(section.key))
    return Job(
        section=section,
        url=url,
        seconds=seconds,
        out=workspace.recording(section.key),
        log_path=workspace.recording_log(section.key),
        input_hash=section_hash(inputs, section, url, seconds, previous.assets if previous else ()),
        previous=previous,
    )


__all__ = [
    "Job",
    "PageParts",
    "SceneSpans",
    "as_query",
    "page_parts",
    "page_source",
    "plan_job",
    "scene_params",
    "scene_url",
    "section_hash",
    "spoken_words",
    "words_param",
    "words_query",
]
