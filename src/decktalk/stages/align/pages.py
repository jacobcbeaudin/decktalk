"""The two-way check of `cues.json` against the pages that play its cues.

A cue and the element it reveals are one thing written in two files, so each file is read against
the other. A cue id that appears nowhere in its page as a quoted literal would never be revealed,
and an element carrying `data-cue` that `cues.json` never names would wait for a phrase nobody
wrote. Both scans read the file's text alone, so an id a script builds from a variable is left to
`preflight`, which reads the page's own catalog.
"""

from __future__ import annotations

import re

from ...model import PageSection, Project
from ...model.cues import SectionCues, page_mentions

DATA_CUE_RE = re.compile(r"data-cue\s*=\s*([\"'])([^\"']+)\1")
# A page may build an id from a variable, as in data-cue="${IDS[i]}". No file can say what that
# reads as, so a static scan skips it and the runtime catalog reports it instead.
LITERAL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


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

    A cue id is owned by the section its prefix names, which is the rule the runtime follows, so one
    page holding every scene reports each element against the section that reveals it. A page is read
    once however many sections play it, and an id a script builds from a variable is left to the
    runtime catalog, because no reader of the file can say what it will be.
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
        for _quote, cue_id in DATA_CUE_RE.findall(path.read_text(encoding="utf-8")):
            if cue_id in listed or not LITERAL_ID_RE.match(cue_id):
                continue
            prefix = cue_id.split(".")[0]
            owner = int(prefix) if prefix.isdigit() and int(prefix) in numbers else section.number
            out.add((f"{owner:02d}", cue_id, section.page))
    return sorted(out)
