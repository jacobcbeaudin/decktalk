"""The two-way check of `cues.json` against the pages that play its cues.

A cue and the element it reveals are one thing written in two files, so each file is read against
the other. A cue id that appears nowhere in its page as a quoted literal would never be revealed,
and an element carrying `data-cue` that `cues.json` never names would wait for a phrase nobody
wrote. Both scans read the file's text alone, so an id a script builds from a variable is left to
`preflight`, which reads the page's own catalog.

An element inside a `data-scene` wrapper belongs to that scene, because the runtime mounts it only
when that scene plays. So an element in a scene no section of the project plays waits for nothing,
and an element in a played scene is reported against the section that plays it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from ...model import PageSection, Project
from ...model.cues import SectionCues, page_mentions

DATA_CUE_RE = re.compile(r"data-cue\s*=\s*([\"'])([^\"']+)\1")
# A page may build an id from a variable, as in data-cue="${IDS[i]}". No file can say what that
# reads as, so a static scan skips it and the runtime catalog reports it instead.
LITERAL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
# Elements that never have an end tag, so an open one never holds anything.
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class SceneSpans(HTMLParser):
    """Where each `data-scene` wrapper of a page begins and ends, as offsets into its text.

    An end tag closes the most recent open element of its name, and everything opened after it, which
    is how a browser recovers from a missing end tag. A wrapper still open at the end of the text runs
    to the end.
    """

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        self.stack: list[tuple[str, str | None, int]] = []  # (tag, scene id or None, start offset)
        self.spans: list[tuple[int, int, str]] = []  # (start, end, scene id)
        self.feed(text)
        self.close()
        for _tag, scene, start in self.stack:
            if scene is not None:
                self.spans.append((start, len(text), scene))

    def here(self) -> int:
        """Where the tag being read starts, as an offset into the text."""
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in VOID_TAGS:
            self.stack.append((tag, dict(attrs).get("data-scene"), self.here()))

    def handle_endtag(self, tag: str) -> None:
        names = [name for name, _scene, _start in self.stack]
        if tag not in names:
            return
        at = len(names) - 1 - names[::-1].index(tag)
        end = self.here()
        self.spans += [(start, end, scene) for _tag, scene, start in self.stack[at:] if scene is not None]
        del self.stack[at:]

    def scene_at(self, offset: int) -> str | None:
        """The innermost scene whose wrapper holds this offset, or None outside every scene."""
        holding = [(start, scene) for start, end, scene in self.spans if start <= offset < end]
        return max(holding)[1] if holding else None


def unknown_cue_ids(project: Project, specs: list[SectionCues]) -> list[tuple[str, str, str]]:
    """(section key, cue id, page) for every cue id that its page never mentions.

    Clip sections have no page, and a page file that does not exist is reported by the
    recorder instead, so both are skipped.
    """
    pages: dict[str, str] = {}
    out: list[tuple[str, str, str]] = []
    for spec in specs:
        section = project.section(spec.number)
        if not isinstance(section, PageSection):
            continue
        if section.page not in pages:
            path = project.path(section.page)
            if not path.exists():
                continue
            pages[section.page] = path.read_text(encoding="utf-8")
        html = pages[section.page]
        out += [(section.key, cue.cue, section.page) for cue in spec.cues if not page_mentions(html, cue.cue)]
    return out


def uncued_elements(project: Project, specs: list[SectionCues]) -> list[tuple[str, str, str]]:
    """(section key, cue id, page) for every `data-cue` element that `cues.json` never names.

    An element inside a scene wrapper is reported against the section that plays that scene on its
    page, and not at all when no section plays it, because the runtime never mounts it then. Any other
    element, such as one a script writes, is owned by the section its prefix names, which is the rule
    the runtime follows, else by the first section that plays the page. A page is read once however
    many sections play it, and an id a script builds from a variable is left to the runtime catalog,
    because no reader of the file can say what it will be.
    """
    if not project.cues.exists():
        # Every page then runs its own built-in timing, so no element on one waits for a phrase.
        return []
    listed = {cue.cue for spec in specs for cue in spec.cues}
    numbers = {s.number for s in project.sections}
    out: set[tuple[str, str, str]] = set()
    read: set[str] = set()
    for section in project.page_sections:
        path = project.path(section.page)
        if section.page in read or not path.exists():
            continue
        read.add(section.page)
        players: dict[str, list[int]] = {}
        for other in project.page_sections:
            if other.page == section.page:
                players.setdefault(other.scene, []).append(other.number)
        text = path.read_text(encoding="utf-8")
        scenes = SceneSpans(text)
        for match in DATA_CUE_RE.finditer(text):
            cue_id = match.group(2)
            if cue_id in listed or not LITERAL_ID_RE.match(cue_id):
                continue
            prefix = cue_id.split(".")[0]
            named = int(prefix) if prefix.isdigit() else None
            scene = scenes.scene_at(match.start())
            if scene is None:
                owner = named if named in numbers else section.number
            elif scene not in players:
                continue
            else:
                owner = named if named in players[scene] else players[scene][0]
            out.add((f"{owner:02d}", cue_id, section.page))
    return sorted(out)
