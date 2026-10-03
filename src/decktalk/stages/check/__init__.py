"""Judge without producing, and price the narration a build would buy, before a single second is bought.

    script.py   what the voice would read out or swallow, and what it may misread
    freeze.py   which two frozen states each cue is measured between
    scan.py     drawing those states and reading what the difference between two of them means

`check` is the one command that says what a voiced build's narration would spend and what the build
would show while both can still be changed for nothing. It plans the takes the way `narrate` would,
prices them, resolves every cue against the words those takes will carry, reads the catalog each
page publishes, and freezes the frames either side of every cue so a reveal that would not be
measured is met here rather than after the credits are gone. The soundscape is priced by its own
stage, so a sound is never in this price.

It has two scope flags and no others, because neither names a stage a run could skip nor a knob a
project could turn. Without pages it judges the script, the cue phrases and the take plan with no
browser at all and says which judgements it could not reach, so a hook that has no browser still
prices the narration and reads its script. Every judgement that scaffolds a `cues.json` row reads the
catalog a page publishes, so a new deck gets those rows from a run with pages. Without frames it keeps the
browser and the catalog and drops the freeze comparison.

Nothing it writes is a deliverable: the frozen frames and the storyboard are there to be looked at,
and `cue` still owns `build/cue-times.json`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from playwright.sync_api import Page

from decktalk.errors import InputError
from decktalk.files import current_text
from decktalk.findings import Code, Finding, Location, ProjectPath, judge
from decktalk.inputs import Inputs
from decktalk.inputs.document import PageSection
from decktalk.inputs.script import Segment
from decktalk.machine import Run
from decktalk.media.browser import chromium
from decktalk.media.origin import Assets
from decktalk.media.pagereport import MeasuredScene, PageReport
from decktalk.pagescan import Slides, asset_findings, page_findings, scene_entry, slide_cues
from decktalk.results import CheckResult, Panel, SectionCues, SpendState
from decktalk.speech import check_host
from decktalk.stages import selects, voice_model
from decktalk.stages.check.scan import (
    judged_pages,
    landing_findings,
    opening_panels,
    seam_findings,
    static_findings,
)
from decktalk.stages.check.script import pause_findings, script_findings
from decktalk.stages.cue.catalog import cue_findings, declared_cues
from decktalk.stages.cue.resolve import resolve_sections
from decktalk.stages.narrate import TakePlan, planned_words, spend_of, voiced_plan
from decktalk.stages.narrate.plan import dropped_pauses, named_voice
from decktalk.stages.storyboard import Sheet, open_project_page, reports_of, write_page
from decktalk.stages.verify import opted_out

NEEDS_A_PAGE: tuple[Code, ...] = (
    Code.CUE_MISSING,
    Code.CUE_UNKNOWN,
    Code.CUE_OVERLAP,
    Code.PAGE_MOTION_OVERRUN,
    Code.PAGE_STAGGER_OVERRUN,
    Code.PAGE_NO_DESCRIPTION,
    Code.PAGE_SWAP_APART,
    Code.PAGE_CDN_ASSET,
)
"""Every judgement that needs the catalog a page publishes, which a run without pages cannot reach."""

NEEDS_A_FRAME: tuple[Code, ...] = (
    Code.CUE_NO_CHANGE,
    Code.CUE_THIN_CHANGE,
    Code.PAGE_THIN_DRAW,
    Code.CUT_POP,
)
"""Every judgement that needs two frozen frames, which a run without frames cannot reach."""


@dataclass
class Look:
    """What one pass over the pages published, judged and drew."""

    findings: list[Finding] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)
    catalogs: dict[str, tuple[MeasuredScene, ...]] = field(default_factory=dict)


def check(
    inputs: Inputs,
    run: Run,
    *,
    paths: Sequence[Path] = (),
    only: Sequence[int] | None = None,
    pages: bool = True,
    frames: bool = True,
) -> CheckResult:
    """Judge the script, the cue file and the pages, and price the narration a voiced build would buy.

    A voice whose base URL names a host its adapter does not allow is refused first, so the place a
    script would be sent is judged before anything is planned, priced or bought.
    """
    check_host(inputs.settings, inputs.settings.voice.provider, run.voices)
    wanted = selects(only)
    script = inputs.relative(inputs.script_path)
    segments = _segments(inputs, run)
    spoken = [one for one in segments if one.index not in inputs.document.clip_numbers and wanted(one.index)]
    for found in script_findings(current_text(inputs.script_path), spoken, script=script):
        run.found(found)

    extra = _named_pages(inputs, paths)
    plans = _plan(inputs, run, spoken)
    spend = spend_of(plans, inputs, state=SpendState.ESTIMATE)
    resolved, times = _resolve(inputs, run, plans, wanted)
    sections = [one for one in inputs.document.page_sections if wanted(one.number)]

    looked = _look(inputs, run, sections, extra, times, frames=frames) if pages else _unreached(run, frames=frames)
    if pages:
        for found in _two_way(inputs, looked, sections, wanted):
            run.found(found)
    for found in looked.findings:
        run.found(found)
    sheet = write_page(inputs.workspace, looked.panels, title=inputs.document.name) if looked.panels else None
    if sheet is not None:
        run.wrote(sheet)
    return run.result(
        CheckResult,
        judged=_judged(inputs, script, resolved, sections if pages else (), extra if pages else ()),
        pages=pages,
        frames=pages and frames,
        spend=spend,
        storyboard=None if sheet is None else inputs.relative(sheet),
    )


# ---- the files the author writes ---------------------------------------------------------------


def _segments(inputs: Inputs, run: Run) -> list[Segment]:
    """Every section of the script, or a judgement and no sections when it cannot be read."""
    try:
        return list(inputs.script())
    except InputError as refused:
        where = inputs.relative(inputs.script_path)
        run.found(
            judge(
                Code.FILE_MISSING,
                f"{where.as_posix()} cannot be read as a script, so nothing was planned from it ({refused}).",
                Location(where=where.as_posix(), file=where),
            )
        )
        return []


def _plan(inputs: Inputs, run: Run, spoken: Sequence[Segment]) -> list[TakePlan]:
    """What a voiced run would do with each spoken section, and every timed pause its model would drop."""
    if not spoken:
        return []
    model = voice_model(inputs)
    dropped = dropped_pauses(inputs, list(spoken), model=model)
    script = inputs.relative(inputs.script_path)
    for found in pause_findings(dropped, provider=inputs.settings.voice.provider, model=model, script=script):
        run.found(found)
    plans, why = voiced_plan(inputs, list(spoken), model=model, voice_id=named_voice(inputs))
    if why:
        run.note(why)
    return plans


def _resolve(
    inputs: Inputs, run: Run, plans: Sequence[TakePlan], wanted: Callable[[int], bool]
) -> tuple[tuple[SectionCues, ...], dict[int, dict[str, float]]]:
    """Every cue resolved against the words a voiced run would leave, and the seconds they landed on.

    The words are the ones each section will have after the build this check is pricing, which is a
    cached take's own words or the evenly spaced words of a placeholder, so a cue phrase is judged
    before anything is voiced rather than after.
    """
    planned = {plan.segment.index: planned_words(inputs, plan) for plan in plans}
    words = {number: row[0] for number, row in planned.items()}
    estimated = {number for number, row in planned.items() if row[2]}
    cued = [block for block in inputs.cues() if wanted(block.number)]
    sections, found = resolve_sections(
        cued,
        words,
        clips=inputs.document.clip_numbers,
        estimated=estimated,
        cues_file=inputs.relative(inputs.cues_path),
        cues_text=inputs.cues_text(),
    )
    for one in found:
        run.found(one)
    times = {
        block.section: {row.cue: row.seconds for row in block.cues if row.seconds is not None} for block in sections
    }
    return sections, times


def _named_pages(inputs: Inputs, paths: Sequence[Path]) -> tuple[str, ...]:
    """Every page a caller named on the command line, as the project sees it."""
    return tuple(dict.fromkeys(inputs.relative(inputs.path(one)).as_posix() for one in paths))


def _judged(
    inputs: Inputs,
    script: Path,
    resolved: Sequence[SectionCues],
    sections: Sequence[PageSection],
    extra: Sequence[str],
) -> tuple[ProjectPath, ...]:
    """Every file and page this call judged, project-relative and in the order it met them."""
    files: list[Path] = [script]
    if resolved or inputs.cues_path.is_file():
        files.append(inputs.relative(inputs.cues_path))
    return tuple(files) + tuple(Path(page) for page in judged_pages(sections, extra))


# ---- the pages ---------------------------------------------------------------------------------


def _unreached(run: Run, *, frames: bool) -> Look:
    """Say which judgements a run with no browser could not reach, and give back an empty look."""
    missed = ", ".join(code.name for code in (*NEEDS_A_PAGE, *NEEDS_A_FRAME))
    run.note(f"no page was opened, so these judgements were not reached: {missed}.")
    if frames:
        run.note("frames were asked for and no page was opened, so no frame was frozen either.")
    return Look()


def _look(
    inputs: Inputs,
    run: Run,
    sections: Sequence[PageSection],
    extra: Sequence[str],
    times: Mapping[int, Mapping[str, float]],
    *,
    frames: bool,
) -> Look:
    """Open every page once, keep what it published, and judge as much of it as this run asked for."""
    cfg = inputs.settings.record
    files = judged_pages(sections, extra)
    looked = Look()
    if not files:
        return looked
    with chromium(cfg.browser_path, policy=cfg.page_policy) as browser:
        opened: dict[str, tuple[Page, Assets]] = {}
        for page in files:
            if not inputs.path(page).exists():
                continue
            drawn, assets = open_project_page(browser, inputs)
            opened[page] = (drawn, assets)
            report = reports_of(drawn, inputs, [page]).get(page)
            looked.findings += _page_judgements(report, assets.external, where=page)
            if report is not None and report.catalog:
                looked.catalogs[page] = report.catalog
        _sections(inputs, run, sections, times, looked=looked, opened=opened, frames=frames)
    return looked


def _page_judgements(report: PageReport | None, external: Sequence[str], *, where: str) -> list[Finding]:
    """What one page said about itself and what it reached for, judged once per page rather than per section."""
    said = page_findings(report, page=where) if report is not None else []
    return [*said, *asset_findings(external, where=where)]


def _sections(
    inputs: Inputs,
    run: Run,
    sections: Sequence[PageSection],
    times: Mapping[int, Mapping[str, float]],
    *,
    looked: Look,
    opened: Mapping[str, tuple[Page, Assets]],
    frames: bool,
) -> None:
    """Judge every named section from the catalog its page published, and freeze its frames when asked."""
    sheet = Sheet(inputs, run, opened, inputs.workspace.frames_dir)
    slides: dict[int, Slides] = {}
    skipped = opted_out(inputs)
    for section in sections:
        run.check()
        entry = scene_entry(looked.catalogs.get(section.page), section.scene)
        if entry is None:
            run.note(
                f"section {section.number} plays scene {section.scene} of {section.page}, which published no "
                "catalog, so nothing about its slides could be judged."
            )
            continue
        found = slide_cues(entry)
        if found is None:
            continue
        slides[section.number] = found
        resolved = times.get(section.number, {})
        looked.findings += static_findings(
            entry, resolved, where=section.page, section=section.number, settings=inputs.settings
        )
        if not frames:
            continue
        looked.findings += landing_findings(sheet, section, entry, found, resolved, skipped=skipped)
        opening_panels(sheet, section, found, resolved)
    if frames:
        looked.findings += _seams(sheet, sections, slides, times)
    looked.panels += sheet.panels


def _seams(
    sheet: Sheet,
    sections: Sequence[PageSection],
    slides: Mapping[int, Slides],
    times: Mapping[int, Mapping[str, float]],
) -> list[Finding]:
    """One judgement per section that declares `seamless` and follows another page section."""
    found: list[Finding] = []
    for previous, section in pairwise(sections):
        if section.seamless:
            found += seam_findings(sheet, previous, section, slides, times)
    return found


def _two_way(
    inputs: Inputs, looked: Look, sections: Sequence[PageSection], wanted: Callable[[int], bool]
) -> list[Finding]:
    """Every moment with no row and every row no page declares, from the catalogs this run read live."""
    declared = declared_cues(looked.catalogs, sections)
    cued = [block for block in inputs.cues() if wanted(block.number)]
    return cue_findings(declared, cued, cues_path=inputs.cues_path, root=inputs.root)


__all__ = ["NEEDS_A_FRAME", "NEEDS_A_PAGE", "Look", "check"]
