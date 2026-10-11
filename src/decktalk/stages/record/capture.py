"""The URL a page section is opened at, and what its recording is made from, part by part.

The page is opened on the local origin with the section's scene, the cues `cue` resolved and the
section's words, and its clock is stepped through its span in the narration plus its record margin,
one frame at a time.

A recording is kept as a list of parts, each with the digest of what it held: every turn's text and
voice as the script has them and the word times its take plays, the markup of the scene the section
plays, the rest of the page file, which every scene shares, each project file the page loaded when
it was last recorded, the scene and the section's own query, its resolved cues, its length, the frame
geometry, the colour scheme, the page policy, the motion the render asks for and the engine. A run
that would open the same page with the same parts would record the same pixels, so `record` keeps
the recording it has, and when a part moved the parts that moved are what `status` names.

The parts are cut that way because a page holds every scene of a film. A digest of the whole file
would call all nine sections of a nine-scene page stale for one slide's edit, which on the one-page
project `decktalk init` writes is every section there is, and one digest over everything could only
say that something moved. The scene slice is taken from the page source and never from the browser,
because `status` and `record --section` ask whether a recording still stands and neither may open
Chromium to find out. The asset list is what keeps the parts honest in the other direction: a page
that swaps one picture for another changes no line of HTML, so the markup alone would say nothing had
moved.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path

from decktalk.artifacts import CueTimes, RecordingLog, content_digest, file_digest, stored
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection, TurnWords
from decktalk.inputs.programs import project_files
from decktalk.media.origin import page_url
from decktalk.page import LIST_SEPARATOR, SECOND_DIGITS, T0_SIGNAL, TIME_MARK, Q
from decktalk.results import Word
from decktalk.stages.record import render

log = logging.getLogger(__name__)


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

    The contract's list separator divides two pairs and its time mark divides a word from its
    second, so neither may appear inside a word that is written into the value. Each second is
    written to `SECOND_DIGITS`, as every word time is kept, so rounding never moves a word onto the
    second the next turn begins.
    """
    if not words:
        return None
    pairs = ((word.word.replace(LIST_SEPARATOR, "").replace(TIME_MARK, ""), max(0.0, word.start)) for word in words)
    return LIST_SEPARATOR.join(f"{said}{TIME_MARK}{start:.{SECOND_DIGITS}f}" for said, start in pairs)


def turns_param(turns: Sequence[TurnWords]) -> str | None:
    """A section's turns as voice@seconds pairs in turn order, each the second on the section clock the turn begins.

    The page numbers the turns by their place in the list, so a turn that says no word is still
    written, and a word belongs to the last turn that began at or before its own second. The second
    is written as a word's is, so the first word of a turn is never written before its turn begins, and
    a last word that starts before the next turn begins is never written at or after it.
    """
    if not turns:
        return None
    return LIST_SEPARATOR.join(f"{turn.voice}{TIME_MARK}{max(0.0, turn.start):.{SECOND_DIGITS}f}" for turn in turns)


def spoken_query(inputs: Inputs, section: PageSection) -> dict[Q, str]:
    """The section's spoken words with their seconds and the turns that say them, as the page reads them.

    The words are what a word-synced line is drawn on, and the turns are what `DeckTalk.words` reads
    the voice and the turn of each word from. A key with nothing to carry is left out.
    """
    found = {
        Q.WORDS: words_param(inputs.words(section.number).words),
        Q.TURNS: turns_param(inputs.turn_words(section.number)),
    }
    return {key: value for key, value in found.items() if value}


def scene_url(inputs: Inputs, section: PageSection, params: dict[Q, str]) -> str:
    """The local origin URL the recorder and the frozen frames open for one page section."""
    page = inputs.path(section.page)
    if not page.exists():
        raise InputError(
            f"section {section.number} plays {section.page}, which is not there.",
            hint="Write the page, or point the section at the file you meant.",
        )
    query: dict[Q, str] = {Q.SCENE: section.scene, **spoken_query(inputs, section), **params}
    query[Q.T0] = T0_SIGNAL
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
    caller keys the section on the whole file, which is the one key that cannot miss a change.
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
    except OSError as unread:
        log.debug("%s could not be read, so it is hashed as empty.", path.name, exc_info=unread)
        return ""


# ---- what a recording is made from, part by part -----------------------------------------------


class Part(StrEnum):
    """One kind of thing a recording is made from, which its log keeps a digest of and a stale line names.

    A part that belongs to one turn or one file is kept under its kind and that turn or file, as
    `text:2.1` or `file:deck/chart.png`, so the line can say which one moved.
    """

    TEXT = "text"
    VOICE = "voice"
    SCENE = "scene"
    PAGE = "page"
    FILE = "file"
    COMMAND = "command"
    ADDRESS = "address"
    WORDS = "words"
    CUES = "cues"
    DOCUMENT = "document"
    LENGTH = "length"
    FRAME = "frame"
    COLOUR = "colour"
    POLICY = "policy"
    MOTION = "motion"
    ENGINE = "engine"


PHRASES: dict[Part, str] = {
    Part.TEXT: "the words of turn {}",
    Part.VOICE: "the voice of turn {}",
    Part.SCENE: "the markup of its scene",
    Part.PAGE: "the part of {} every scene shares",
    Part.FILE: "the file {}",
    Part.COMMAND: "its render command",
    Part.ADDRESS: "its scene or its own query parameters",
    Part.WORDS: "the word times of turn {}",
    Part.CUES: "its cue times",
    Part.DOCUMENT: "the motion document its command reads",
    Part.LENGTH: "its length",
    Part.FRAME: "the frame size",
    Part.COLOUR: "the colour scheme",
    Part.POLICY: "the page policy",
    Part.MOTION: "the motion it renders with",
    Part.ENGINE: "the DeckTalk version",
}
"""How a stale line names each part, with the turn or the file it belongs to in place of `{}`."""

FOLLOWS = frozenset({Part.WORDS, Part.CUES, Part.LENGTH, Part.DOCUMENT})
"""The parts that move because a take moved, which a stale line names only when nothing they follow from moved.

A turn's new words are timed again, so its word times, its section's cue times and its length move
with them, and a line naming all four would bury the one edit the author made.
"""


def part_key(part: Part, of: str = "") -> str:
    """The name a part is kept under: its kind, and the turn or the file it belongs to when it has one."""
    return f"{part}:{of}" if of else str(part)


def phrase(key: str) -> str:
    """How a stale line names the part kept under `key`."""
    kind, _, of = key.partition(":")
    return PHRASES[Part(kind)].format(of)


def _digest(text: str) -> str:
    return content_digest(text.encode("utf-8"))


@dataclass(frozen=True)
class TurnText:
    """What one turn says and which voice says it, as a take or the script has it."""

    turn: int
    spoken: str
    voice: str


def turn_parts(inputs: Inputs, section: PageSection, script: Sequence[TurnText] | None = None) -> dict[str, str]:
    """Each turn a section speaks: its text, its voice's name and the word times its take plays.

    The text and the voice are the take's, which is what the page is handed, unless `script` gives
    them as the script has them now. That is how `status` asks the question a build answers: the take
    follows the script on the next narrate, so a turn whose words were edited is named before narrate
    has run rather than after.
    """
    played = inputs.turn_words(section.number)
    says = script if script is not None else [TurnText(one.turn, one.spoken, one.voice) for one in played]
    parts: dict[str, str] = {}
    for turn in says:
        label = f"{section.number}.{turn.turn}"
        parts[part_key(Part.TEXT, label)] = _digest(turn.spoken)
        parts[part_key(Part.VOICE, label)] = _digest(turn.voice)
    for one in played:
        parts[part_key(Part.WORDS, f"{section.number}.{one.turn}")] = _digest(words_param(one.words.words) or "")
    return parts


def page_made_from(
    inputs: Inputs,
    section: PageSection,
    cue_times: CueTimes | None,
    seconds: float,
    assets: Sequence[str | Path],
    script: Sequence[TurnText] | None = None,
) -> dict[str, str]:
    """Every part of what a page section's recording looks like, each kept apart so a stale line can name it.

    The page joins in two pieces rather than as one file, so an edit to one scene moves the parts of
    the sections that play it and of no others, while an edit to the head, a style, a script or
    another shared part of the file moves every section of that page. The motion the render asks for
    is a part too, or a reduced build would reuse the full-motion recordings it made before, and so is
    the page policy, because an untrusted page whose other origins are refused may draw a different
    picture from the same page trusted. `assets` is every project file the page loaded, which the
    last run's log names, and the page file itself is left out of them because its two pieces are
    already here. `script` is what `turn_parts` reads the turns' text from in place of their takes.
    """
    settings = inputs.settings
    video, record, motion = settings.video, settings.record, settings.motion
    page = inputs.path(section.page)
    pieces = page_parts(page_source(page), section.scene)
    own = as_query(dict(section.params))
    cues = cue_times.query(section.number) if cue_times is not None and Q.CUES not in own else None
    named = sorted({Path(rel).as_posix() for rel in assets})
    return {
        **turn_parts(inputs, section, script),
        part_key(Part.SCENE): _digest(pieces.scene),
        part_key(Part.PAGE, Path(section.page).as_posix()): _digest(pieces.shared),
        **{part_key(Part.FILE, rel): file_digest(found) for rel in named if (found := inputs.path(rel)) != page},
        part_key(Part.ADDRESS): _digest(page_url(section.page, {Q.SCENE: section.scene, **own})),
        part_key(Part.CUES): _digest(cues or ""),
        part_key(Part.LENGTH): _digest(f"{seconds:.{SECOND_DIGITS}f}"),
        part_key(Part.FRAME): _digest(f"{video.width}x{video.height}@{video.fps}"),
        part_key(Part.COLOUR): _digest(record.color_scheme),
        part_key(Part.POLICY): _digest(record.page_policy),
        part_key(Part.MOTION): _digest(f"{motion.reduce}:{motion.scale:g}"),
        part_key(Part.ENGINE): _digest(stored.ENGINE_VERSION),
    }


def rendered_made_from(
    inputs: Inputs, section: PageSection, document: dict[str, object], script: Sequence[TurnText] | None = None
) -> dict[str, str]:
    """Every part a rendered section's video is drawn from: its turns, its command, its files and its document.

    Every word of the command that names a project file by a relative path is a part by its content,
    so editing the script the command runs records the section again, as editing a page does. Every
    other word, such as an absolute program or a flag, is a part through the command's own words.
    """
    named = project_files(inputs.root, section.render_command)
    return {
        **turn_parts(inputs, section, script),
        part_key(Part.COMMAND): _digest(json.dumps(section.render_command)),
        **{part_key(Part.FILE, word): file_digest(path) for word, path in sorted(named.items())},
        part_key(Part.DOCUMENT): _digest(json.dumps(document, sort_keys=True)),
        part_key(Part.ENGINE): _digest(stored.ENGINE_VERSION),
    }


def moved(before: Mapping[str, str], now: Mapping[str, str]) -> tuple[str, ...]:
    """The parts that changed between two recordings, in the order they are made from, naming causes over their effects.

    A part on one side alone has moved too, which is how a turn added to a section or a file the
    page stopped loading is named. The parts that only follow a take are named when nothing they
    follow from moved.
    """
    changed = [key for key in dict.fromkeys([*now, *before]) if before.get(key) != now.get(key)]
    causes = [key for key in changed if Part(key.partition(":")[0]) not in FOLLOWS]
    return tuple(causes or changed)


def said(keys: Sequence[str]) -> str:
    """The parts kept under `keys` as one phrase, joined as a sentence joins a list."""
    phrases = [phrase(key) for key in keys]
    return phrases[0] if len(phrases) == 1 else f"{', '.join(phrases[:-1])} and {phrases[-1]}"


@dataclass(frozen=True)
class Job:
    """One section the recorder is about to open, what it would be recorded from, and what is there now."""

    section: PageSection
    url: str
    seconds: float
    out: Path
    log_path: Path
    cue_times: CueTimes | None
    """The resolved cues the section is opened with, which the part list is taken over again once it has run."""
    made_from: dict[str, str]
    """Every part of what the section would be recorded from now, by the name a stale line reads it under."""
    previous: RecordingLog | None
    """The log of the recording already on disk, when there is one."""
    document: dict[str, object] | None = None
    """What a rendered section's command reads, or None for a section a page draws."""

    def made_from_now(self, inputs: Inputs, assets: Sequence[str | Path]) -> dict[str, str]:
        """The parts taken again over the files the page really loaded, which the log of this recording keeps.

        The page may have asked for a file the last run never saw, and a key that did not count it
        would call the section unchanged on the next run.
        """
        if self.document is not None:
            return self.made_from
        return page_made_from(inputs, self.section, self.cue_times, self.seconds, assets)

    @property
    def moved(self) -> tuple[str, ...]:
        """The parts that changed since the recording on disk was made, or none when nothing did."""
        before = self.previous.made_from if self.previous is not None else {}
        return moved(before, self.made_from)

    @property
    def unchanged(self) -> bool:
        """Whether the recording on disk was made from these exact parts, and is finished.

        A log with no checks in it belongs to a run that was stopped between placing the video
        and measuring it, so the section is recorded again rather than assembled from a picture
        nobody judged.
        """
        return (
            self.previous is not None
            and self.previous.made_from == self.made_from
            and self.previous.checks is not None
            and self.out.exists()
        )


def motion_of(inputs: Inputs, section: PageSection, cue_times: CueTimes | None, seconds: float) -> dict[str, object]:
    """The motion document a rendered section's command reads, from its take's words and its cue seconds."""
    video = inputs.settings.video
    return render.motion_document(
        fps=video.fps,
        width=video.width,
        height=video.height,
        seconds=seconds,
        words=inputs.words(section.number).words,
        cues=cue_times.times(section.number) if cue_times is not None else {},
    )


RENDERED_MARK = "render"
"""What a rendered section's URL opens with, so it can never be taken for a page's."""


def plan_job(
    inputs: Inputs,
    section: PageSection,
    cue_times: CueTimes | None,
    seconds: float,
    script: Sequence[TurnText] | None = None,
) -> Job:
    """What recording one section would open and write, and whether the recording on disk still stands.

    `script` gives the turns' text as the script has it, for a caller asking what the next build
    records rather than what this run would.
    """
    workspace = inputs.workspace
    previous = RecordingLog.previous(workspace.recording_log(section.key))
    out, log_path = workspace.recording(section.key), workspace.recording_log(section.key)
    if section.rendered:
        document = motion_of(inputs, section, cue_times, seconds)
        return Job(
            section=section,
            url=f"{RENDERED_MARK}:{' '.join(section.render_command)}",
            seconds=seconds,
            out=out,
            log_path=log_path,
            cue_times=cue_times,
            made_from=rendered_made_from(inputs, section, document, script),
            previous=previous,
            document=document,
        )
    assets = previous.recording.assets if previous is not None else ()
    return Job(
        section=section,
        url=scene_url(inputs, section, scene_params(section, cue_times)),
        seconds=seconds,
        out=out,
        log_path=log_path,
        cue_times=cue_times,
        made_from=page_made_from(inputs, section, cue_times, seconds, assets, script),
        previous=previous,
    )


__all__ = [
    "FOLLOWS",
    "PHRASES",
    "Job",
    "PageParts",
    "Part",
    "SceneSpans",
    "TurnText",
    "as_query",
    "moved",
    "page_made_from",
    "page_parts",
    "page_source",
    "part_key",
    "phrase",
    "plan_job",
    "rendered_made_from",
    "said",
    "scene_params",
    "scene_url",
    "turn_parts",
    "words_param",
    "spoken_query",
    "turns_param",
]
