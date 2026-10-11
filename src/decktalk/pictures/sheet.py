"""Frozen states drawn under one directory, the frames the project keeps, and the contact sheet that lays them out.

A frozen frame shows every reveal in its end state and nothing in motion, which is what makes two of
them comparable and what makes one of them worth looking at. `storyboard` draws them to be read and
`check` to be compared, through one `Sheet`, so a state either command drew is one the other reads
back rather than draws again, and one writer lays the panels out whichever command froze them,
because two contact sheets of one deck would be two answers to one question.
"""

from __future__ import annotations

import html
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from decktalk.inputs import Inputs, PageSection
from decktalk.inputs.script import ScriptSection
from decktalk.inputs.workspace import Workspace
from decktalk.machine.run import Run
from decktalk.media.origin import page_url
from decktalk.page import SECOND_DIGITS, Q
from decktalk.pictures import ProjectPage
from decktalk.pictures.address import Freeze, freeze_query, spoken_query
from decktalk.results import Panel, counted

PAGE_TITLE = "storyboard"
"""What the contact sheet calls itself, beside the name of the project it is a storyboard of."""


def still(
    inputs: Inputs,
    page: ProjectPage,
    section: PageSection,
    query: Mapping[Q, str],
    target: Path,
    documents: Mapping[str, bytes],
) -> bool:
    """Write the frozen state at `query` of one section to `target`, drawing it only when no kept frame is current.

    The frame is keyed on its URL and on everything else that decides how the page draws, and a kept
    frame is trusted only while every file the page loaded to draw it is unchanged. True means the
    state was drawn now, and false means it was read back from the frames the project keeps.
    """
    key = inputs.still_key(section.page, page_url(section.page, query), documents=documents)
    target.parent.mkdir(parents=True, exist_ok=True)
    kept = inputs.stills.find(key)
    if kept is not None:
        shutil.copyfile(kept, target)
        return False
    page.still(section.page, query, target)
    inputs.stills.keep(key, target, page.assets.paths)
    return True


@dataclass
class Sheet:
    """One browser drawing frozen states under one directory, with what it drew and the panels it kept.

    A state is drawn once however many panels or measured cues name it, because the same URL is the
    same picture, and every file it writes is reported through the run.
    """

    inputs: Inputs
    run: Run
    pages: Mapping[str, ProjectPage]
    directory: Path
    drawn: dict[str, Path] = field(default_factory=dict)
    panels: list[Panel] = field(default_factory=list)
    spoken: dict[int, dict[Q, str]] = field(default_factory=dict)
    """Each section's spoken words and turns as the page is handed them, read once per section rather than per state."""

    @cached_property
    def documents(self) -> dict[str, bytes]:
        """What the origin answers from memory, read once for every state this sheet keys."""
        return self.inputs.documents()

    def query(self, section: PageSection, freeze: Freeze) -> dict[Q, str]:
        """The query of one frozen state of one section."""
        if section.number not in self.spoken:
            self.spoken[section.number] = spoken_query(self.inputs, section)
        return freeze_query(section, freeze, self.spoken[section.number])

    def url(self, section: PageSection, freeze: Freeze) -> str:
        """The URL of one frozen state of one section."""
        return page_url(section.page, self.query(section, freeze))

    def frozen(self, section: PageSection, freeze: Freeze) -> Path:
        """The file holding one frozen state of one section, drawn now or read back from the kept frames."""
        query = self.query(section, freeze)
        url = page_url(section.page, query)
        if url not in self.drawn:
            target = self.directory / section.key / f"{freeze.label}.png"
            still(self.inputs, self.pages[section.page], section, query, target, self.documents)
            self.run.wrote(target)
            self.drawn[url] = target
        return self.drawn[url]

    def panel(self, section: PageSection, freeze: Freeze, cue: str | None, at: float) -> None:
        """Keep one drawn state as a panel of the contact sheet."""
        image = self.drawn.get(self.url(section, freeze))
        if image is None:
            return
        self.panels.append(
            Panel(
                section=section.number,
                slide=freeze.slide,
                cue=cue,
                at_seconds=round(at, SECOND_DIGITS),
                image=self.inputs.relative(image),
            )
        )


def section_voices(inputs: Inputs, sections: Sequence[ScriptSection]) -> dict[int, tuple[str, ...]]:
    """The voices that read each of these sections, in turn order, unless the default voice reads every one.

    A voice is named as its direction names it, so a sheet of a script with a direction no project
    voice answers still names who it meant. A film the default voice reads whole names nobody, so its
    sheet reads as it always has.
    """
    named = {
        section.number: tuple(dict.fromkeys(turn.voice or inputs.default_voice for turn in section.turns))
        for section in sections
    }
    return {} if all(voices == (inputs.default_voice,) for voices in named.values()) else named


def write_page(
    workspace: Workspace,
    panels: Sequence[Panel],
    *,
    title: str,
    voices: Mapping[int, tuple[str, ...]] | None = None,
) -> Path:
    """Lay every panel out on one page and give back where it was written.

    The page sits beside the stills rather than among them, so a reader opens one file and a rebuild
    replaces the whole sheet. One writer draws it whether `storyboard` or `check` froze the frames,
    because two contact sheets of one deck would be two answers to one question. `voices` names the
    voices that read each section, in turn order, which each of its panels says.
    """
    build = workspace.build.relative_to(workspace.root) if workspace.build.is_relative_to(workspace.root) else None
    figures = "\n".join(_figure(panel, build, (voices or {}).get(panel.section, ())) for panel in panels)
    page = workspace.storyboard_path
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_document(title, len(panels), figures), encoding="utf-8")
    return page


def _figure(panel: Panel, build: Path | None, voices: tuple[str, ...]) -> str:
    """One panel as a figure, with the picture above the moment it shows and the voices that read its section."""
    image = Path(panel.image)
    src = image.relative_to(build) if build is not None and image.is_relative_to(build) else image
    # Every part of the caption is escaped, because a cue id and a slide id are whatever the project's
    # author typed, and the storyboard is a page a person opens in a browser.
    moment = f"cue {panel.cue}" if panel.cue else "opening"
    read = (f"read by {', '.join(voices)}",) if voices else ()
    parts = (f"section {panel.section}", f"slide {panel.slide}", moment, f"{panel.at_seconds:.2f}s", *read)
    caption = " &middot; ".join(html.escape(part) for part in parts)
    alt = " · ".join(parts)
    return (
        f'<figure><img loading="lazy" src="{html.escape(src.as_posix())}" alt="{html.escape(alt)}">'
        f"<figcaption>{caption}</figcaption></figure>"
    )


def _document(title: str, count: int, figures: str) -> str:
    """The whole contact sheet, which is one plain page that needs nothing to open it."""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        f"<title>{html.escape(title)} {PAGE_TITLE}</title>\n"
        "<style>\n"
        "body{margin:0;padding:32px;background:#11141a;color:#e8ecf2;"
        "font-family:Inter,-apple-system,Helvetica,Arial,sans-serif}\n"
        "h1{font-size:20px;font-weight:600;margin:0 0 4px}\n"
        "p{color:#9aa4b2;margin:0 0 28px;font-size:14px}\n"
        ".sheet{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}\n"
        "figure{margin:0}\n"
        "img{width:100%;display:block;border-radius:6px;background:#000}\n"
        "figcaption{color:#9aa4b2;font-size:12px;padding-top:6px}\n"
        "</style></head><body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<p>{counted(count, 'panel')}, in the order the film plays them.</p>\n"
        f'<div class="sheet">\n{figures}\n</div>\n'
        "</body></html>\n"
    )


__all__ = ["PAGE_TITLE", "Sheet", "section_voices", "still", "write_page"]
