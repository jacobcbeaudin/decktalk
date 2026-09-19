"""The URL a page section is opened at, and the capture that records it.

The page is opened at `http://project.localhost/<page>?scene=<scene>&<params>&words=…&cues=id@t,…`,
which the local origin serves from the project directory, and it is recorded for its span in the
narration plus `record_margin_seconds`. A section whose frames stall is recorded again while the
machine is quieter, up to `[record] retries` times.

A section is keyed on that URL, which already carries the scene, the resolved cues and the spoken
words with their times, on the frame geometry and colour scheme, and on what the page puts on screen
for this one section: the markup of the scene it plays, the rest of the page file, which every scene
shares, and the content of every project file the page loaded when it was last recorded. A run that
would open the same page with the same everything would record the same pixels, so `record` keeps
the recording it has.

The key is cut that way because a page holds every scene of a film. A digest of the whole file would
call all nine sections of a nine-scene page stale for one slide's edit, which on the one-page project
`decktalk init` writes is every section there is. The scene slice is taken from the page source, not
from the browser, because `status` and `record --only` ask whether a recording still stands and
neither may open Chromium to find out. The asset list is what keeps the key honest in the other
direction: a page that swaps one picture for another changes no line of HTML, so the markup alone
would say nothing had moved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ...artifacts import CueTimes, RecordingLog, Word, input_hash, text_digest
from ...errors import ConfigError
from ...media.browser import record_page
from ...media.origin import page_url
from ...model import PageSection, Project

log = logging.getLogger(__name__)


# ---- the page URL ---------------------------------------------------------------------------


def scene_params(section: PageSection, cue_times: CueTimes | None) -> dict[str, str]:
    """The section's own query parameters, plus its resolved cues unless the section sets `cues` itself."""
    params = dict(section.params)
    if cue_times is not None and "cues" not in params:
        query = cue_times.query(section.key)
        if query:
            params["cues"] = query
    return params


def scene_url(project: Project, section: PageSection, params: dict[str, str]) -> str:
    """The local origin URL the recorder and the frame screenshots open for a page section."""
    page = project.path(section.page)
    if not page.exists():
        raise ConfigError(f"section {section.number}: page not found: {page}")
    query = {"scene": section.scene, **params}
    words = words_query(project, section)
    if words and "words" not in query:
        query["words"] = words
    prev = prev_words_query(project, section)
    if prev and "prevwords" not in query:
        query["prevwords"] = prev
    query["t0"] = "signal"
    return page_url(section.page, query)


def words_param(words: list[Word]) -> str | None:
    """A section's words as word@seconds pairs, in seconds after that section starts."""
    if not words:
        return None
    return ",".join(f"{w.word.replace(',', '').replace('@', '')}@{max(0.0, w.start):.2f}" for w in words)


def _spoken(project: Project, key: str) -> list[Word]:
    """One section's words in seconds after it starts, or nothing when it has no take."""
    takes = project.takes()
    take = takes.sections.get(key) if takes is not None else None
    return [] if take is None else project.section_words(key, take.words_file)


def words_query(project: Project, section: PageSection) -> str | None:
    """The section's spoken words with their seconds after the section starts, for data-text="spoken" reveals."""
    return words_param(_spoken(project, section.key))


def prev_words_query(project: Project, section: PageSection) -> str | None:
    """The spoken words of the section before a seamless one, in seconds after that section starts.

    A page that opens on the previous section's last frame reads them, so a value it carries across
    the cut, such as a word's time, matches what the previous recording showed. A section that opens
    on its own picture reads nothing from the section before it, and giving it those words anyway
    would put them in its recorded URL and re-record it whenever the section before it was reworded.
    """
    takes = project.takes()
    if takes is None or not section.seamless or section.key not in takes.sections:
        return None
    keys = takes.keys
    at = keys.index(section.key)
    return words_param(_spoken(project, keys[at - 1])) if at > 0 else None


# ---- the page, cut into the scene one section plays and the part every scene shares -----------


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
        # The offset each line starts at, so the parser's (line, column) becomes an index into the
        # file. The parser counts a line per "\n" and nothing else, so this split has to agree.
        self.line_starts = [0]
        for line in source.split("\n"):
            self.line_starts.append(self.line_starts[-1] + len(line) + 1)
        self.spans: list[tuple[str, int, int]] = []  # (scene id, start, end) in source order
        self.open: tuple[str, str, int] | None = None  # the wrapper's tag, its scene id, where it begins
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
        _, scene, start = self.open
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

    scene: str  # the markup of the scene this section plays, or nothing when the page declares none
    shared: str  # everything else in the file: the head, the styles, the scripts, the other scenes


def page_parts(source: str, scene: str) -> PageParts:
    """Cut a page into the scene a section plays and the part every scene of that page shares.

    A page that declares its scenes in script has no `[data-scene]` element to cut out, and neither
    has a page the parser could not follow, so the whole file is shared and every section of it is
    recorded again whenever it is edited. That is the conservative answer, and it is the right one:
    a `render` function is reached by any line of the script around it.
    """
    spans = SceneSpans.of(source)
    if spans is None or not any(found == scene for found, _, _ in spans):
        return PageParts(scene="", shared=source)
    kept: list[str] = []
    cut = 0
    for _, start, end in spans:
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


# ---- what a section's recording is keyed on --------------------------------------------------


def section_hash(project: Project, section: PageSection, url: str, seconds: float, assets: list[str]) -> str:
    """The digest of everything that decides what this section's recording looks like.

    The page joins the key in two pieces rather than as one file, so an edit to one scene moves the
    key of the sections that play it and of no others, while an edit to the head, a style, a script
    or another shared part of the file moves every section of that page. `assets` is every project
    file the page loaded, which the last run's log names, and the page file itself is left out of
    them because its two pieces are already here.
    """
    video, cfg = project.settings.video, project.settings.record
    page = project.path(section.page)
    parts_of_page = page_parts(page_source(page), section.scene)
    parts = [
        url,
        f"{seconds:.3f}",
        f"{video.width}x{video.height}@{video.fps}",
        cfg.color_scheme,
        f"scene:{text_digest(parts_of_page.scene)}",
        f"page:{text_digest(parts_of_page.shared)}",
    ]
    files = {rel: found for rel in assets if (found := project.path(rel)) != page}
    return input_hash(parts, files)


@dataclass(frozen=True)
class Job:
    """One section the recorder is about to open, what it would be recorded from, and what is there now."""

    section: PageSection
    url: str
    seconds: float
    out: Path
    log_path: Path
    input_hash: str
    previous: RecordingLog | None  # The log of the recording already on disk, when there is one.

    @property
    def unchanged(self) -> bool:
        """Whether the recording on disk was made from these exact inputs, and is finished."""
        return (
            self.previous is not None
            and bool(self.previous.input_hash)
            and self.previous.input_hash == self.input_hash
            and self.previous.t0_seconds is not None
            and self.out.exists()
        )


def plan_job(project: Project, section: PageSection, cue_times: CueTimes | None, seconds: float) -> Job:
    """What recording one section would open and write, and whether the recording on disk still stands."""
    url = scene_url(project, section, scene_params(section, cue_times))
    previous = RecordingLog.load(project.recording_log(section))
    return Job(
        section=section,
        url=url,
        seconds=seconds,
        out=project.recording(section),
        log_path=project.recording_log(section),
        input_hash=section_hash(project, section, url, seconds, list(previous.assets) if previous else []),
        previous=previous,
    )


# ---- the capture ----------------------------------------------------------------------------


def capture_section(project: Project, browser: object, job: Job) -> RecordingLog:
    """Record one section, retrying while its frames stall, and return the log of the run that stuck."""
    cfg = project.settings.record
    video = project.settings.video
    recording_log = None
    for attempt in range(1, cfg.retries + 2):
        recording_log = record_page(
            browser,
            job.url,
            job.seconds,
            job.out,
            root=project.root,
            settle_seconds=cfg.settle_seconds,
            min_cover_seconds=cfg.min_cover_seconds,
            width=video.width,
            height=video.height,
            color_scheme=cfg.color_scheme,
        )
        stall = recording_log.worst_stall_ms
        if stall <= cfg.stall_ms or attempt > cfg.retries:
            break
        # A stalled page froze a reveal for a few frames, which no cut can repair, so the section
        # is recorded again while the machine is quieter.
        log.warning(
            "       frames stalled for %d ms, so section %s is recorded again (%d/%d)",
            stall,
            job.section.key,
            attempt,
            cfg.retries,
        )
    assert recording_log is not None  # the loop runs at least once
    # The page may have loaded a file the last run never saw, so the key is recomputed on what it did load.
    recording_log.input_hash = section_hash(project, job.section, job.url, job.seconds, recording_log.assets)
    return recording_log
