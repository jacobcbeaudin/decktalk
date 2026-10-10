"""Stage 2: every cue phrase becomes a second on its own section's clock.

    cues.json            which spoken phrase each moment lands on
    build/cue-times.json every cue resolved against the words its section speaks

    resolve.py   the arithmetic, with no project and no file
    catalog.py   `cues.json` read against the catalog the page published, and the fixes

The page owns what a moment looks like and the project file owns when it happens, so the page names
a moment and `cues.json` gives it a phrase. This stage joins the two: it reads the words of each
section's take, found by the digest its take index row names, and writes the second every moment
fires at.

The two files are read against each other as well, because a cue and the moment it fires are one
thing written twice. The page's side is the catalog the runtime published, so a moment with no row
and a row no page declares are both named from what the page itself said rather than from a scan of
its markup. That catalog has to be the page as it is now. A section's recording keeps the catalog
its page published when it was recorded, and it is read from there while that recording still
stands, by the one rule `record` and `status` decide a recording is stale with. Once anything it
was made from has changed, the page, its cues or its words, the page is opened and read as it is
now, the way `check` reads it, because `record` runs after this stage and an edit that renamed a
cue in the page and in `cues.json` would otherwise be judged against the name the page had before.
A section that has never been recorded is left unjudged rather than guessed at, and `check` makes
the same comparison against a catalog it reads live.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from decktalk.artifacts import CueTimes
from decktalk.events import Level
from decktalk.findings import Finding
from decktalk.inputs import CuedSection, Inputs, PageSection
from decktalk.machine.run import Run
from decktalk.media.browser import chromium
from decktalk.media.pagereport import MeasuredScene
from decktalk.pagescan import measured_rows, overlap_findings, scene_entry
from decktalk.pipeline import Stage
from decktalk.results import CueResult, SectionCues, Word
from decktalk.stages import selects
from decktalk.stages.cue.catalog import cue_findings, declared_cues
from decktalk.stages.cue.resolve import resolve_sections, short_section
from decktalk.stages.record import stale_recording
from decktalk.stages.storyboard import open_project_page, reports_of

__all__ = ["cue"]


def cue(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None) -> CueResult:
    """Resolve every cue phrase against the narration and write `build/cue-times.json`.

    `only` resolves those sections and leaves every other section's rows in the file as they were,
    so a run aimed at one section never drops the cues of the rest. A row no page declares is always
    reported, and whether it fails the run is the caller's threshold to decide.
    """
    wanted = selects(only)
    takes = inputs.takes(required=True)
    cued = [block for block in inputs.cues() if wanted(block.number)]
    heard = {number: inputs.words(number) for number in takes.numbers}
    words = {number: said.words for number, said in heard.items()}
    estimated = {number for number, said in heard.items() if said.estimated}

    sections, judged, notes = resolve_sections(
        cued,
        words,
        clips=inputs.document.clip_numbers,
        estimated=estimated,
        cues_file=inputs.relative(inputs.cues_path),
        cues_text=inputs.cues_text(),
        stage=Stage.CUE,
    )
    for block in cued:
        with run.section(Stage.CUE, block.number):
            _say_what_was_chosen(run, block, words.get(block.number, ()), notes.get(block.number, ()))
    for found in judged:
        run.found(found)
    catalogs = _catalogs(inputs, run)
    for found in _catalog_findings(inputs, catalogs, only):
        run.found(found)
    for found in _overlap_findings(inputs, catalogs, sections):
        run.found(found)

    written = _write(inputs, sections, replacing=bool(only))
    run.wrote(written)
    return run.result(
        CueResult,
        sections=sections,
        file=inputs.relative(written),
    )


def _say_what_was_chosen(run: Run, block: CuedSection, words: Sequence[Word], chosen: Sequence[str]) -> None:
    """Every choice this section's cues made for their author, as one sentence each.

    A phrase that occurs twice and a section whose speech runs shorter than its visuals ask for are
    both readings rather than judgements, so each is a line on the stream and no code is invented
    for it. `chosen` is what resolving the section's cues already said about their phrases.
    """
    for said in chosen:
        run.note(said, level=Level.WARNING)
    short = short_section(block, words)
    if short:
        run.note(short, level=Level.WARNING)


def _catalog_findings(
    inputs: Inputs, catalogs: Mapping[int, tuple[MeasuredScene, ...]], only: Sequence[int] | None
) -> list[Finding]:
    """Every moment with no row and every row no page declares, judged from what the pages publish now."""
    wanted = selects(only)
    declared: dict[int, tuple[str, ...]] = {}
    for section in inputs.document.page_sections:
        if wanted(section.number) and section.number in catalogs:
            declared |= declared_cues({section.page: catalogs[section.number]}, [section])
    cued = [block for block in inputs.cues() if wanted(block.number)]
    return cue_findings(
        declared,
        cued,
        cues_path=inputs.cues_path,
        root=inputs.root,
        stage=Stage.CUE,
    )


def _catalogs(inputs: Inputs, run: Run) -> dict[int, tuple[MeasuredScene, ...]]:
    """The catalog each recorded section's page publishes now, by section number.

    A recording log keeps the page's whole report, so while a section's recording still stands its
    catalog is the page's own document as it is now, and no browser is opened for it. A recording
    that no longer stands kept a catalog the page may have moved on from, so that page is opened and
    read once, the way `check` reads it. A section nothing has recorded is absent from this map,
    which is what leaves it unjudged instead of judged against nothing.
    """
    out: dict[int, tuple[MeasuredScene, ...]] = {}
    changed: list[PageSection] = []
    for section in inputs.document.page_sections:
        log = inputs.recording_log(section.key)
        if log is None or not log.recording.report.catalog:
            continue
        if stale_recording(inputs, section) is None:
            out[section.number] = tuple(log.recording.report.catalog)
        else:
            changed.append(section)
    if not changed:
        return out
    pages = list(dict.fromkeys(section.page for section in changed))
    run.note(f"Read {', '.join(pages)} as it is now, because it changed since it was recorded.")
    with chromium(inputs.settings.tools.chromium, policy=inputs.settings.record.page_policy, spend=run.spend) as opened:
        page, _assets = open_project_page(opened, inputs)
        reports = reports_of(page, inputs, pages)
    for section in changed:
        report = reports.get(section.page)
        if report is not None and report.catalog:
            out[section.number] = tuple(report.catalog)
    return out


def _overlap_findings(
    inputs: Inputs, catalogs: Mapping[int, tuple[MeasuredScene, ...]], sections: Sequence[SectionCues]
) -> list[Finding]:
    """Every pair of cues that fire closer together than the first one is still moving for.

    The span comes from the measured rows the page publishes, so a pair is judged against the motion
    the deck actually declares. A section whose page nothing has recorded has no spans to judge
    against, and a ceiling guessed for it would report every quick reveal in the deck as an overlap,
    so it is left alone.
    """
    scale = inputs.settings.motion.scale
    by_number = {block.section: block for block in sections}
    found: list[Finding] = []
    for section in inputs.document.page_sections:
        block = by_number.get(section.number)
        entry = scene_entry(catalogs.get(section.number), section.scene)
        if block is None or entry is None:
            continue
        times = {row.id: row.seconds for row in block.cues if row.seconds is not None}
        found += [
            judged.model_copy(update={"stage": Stage.CUE})
            for judged in overlap_findings(
                measured_rows(entry), times, where=section.page, section=section.number, scale=scale
            )
        ]
    return found


def _write(inputs: Inputs, sections: Sequence[SectionCues], *, replacing: bool) -> Path:
    """Write `build/cue-times.json`, keeping the rows of every section this run did not resolve."""
    resolved = {block.section: block for block in sections}
    previous = CueTimes.previous(inputs.workspace.cue_times_path) if replacing else None
    kept = [block for block in previous.sections if block.section not in resolved] if previous else []
    blocks = sorted([*kept, *sections], key=lambda block: block.section)
    return CueTimes(sections=tuple(blocks)).write(inputs.workspace.cue_times_path)
