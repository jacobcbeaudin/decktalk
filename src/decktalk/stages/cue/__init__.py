"""Stage 2: every cue phrase becomes a second on its own section's clock.

    cues.json            which spoken phrase each moment lands on
    build/cue-times.json every cue resolved against the words its section speaks

    resolve.py   the arithmetic, with no project and no file
    catalog.py   `cues.json` read against the catalog the page published, and the fixes

The page owns what a moment looks like and the project file owns when it happens, so the page names
a moment and `cues.json` gives it a phrase. This stage joins the two: it reads the words each
section speaks off the take index and writes the second every moment fires at.

The two files are read against each other as well, because a cue and the moment it fires are one
thing written twice. The page's side is the catalog the runtime published, which the recordings
keep whole, so a moment with no row and a row no page declares are both named from what the page
itself said rather than from a scan of its markup. A section whose page has never been recorded is
left unjudged rather than guessed at, and `check` makes the same comparison against a catalog it
reads live.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from decktalk.artifacts import CueTimes, Takes
from decktalk.events import Level
from decktalk.findings import Finding
from decktalk.inputs import CuedSection, Inputs
from decktalk.machine import Run
from decktalk.media.pagereport import MeasuredScene
from decktalk.pagescan import overlap_findings
from decktalk.pipeline import Artifact, Stage
from decktalk.results import CueResult, SectionCues, Word
from decktalk.stages import clock, selects, since
from decktalk.stages.cue.catalog import cue_findings, declared_cues, measured_rows
from decktalk.stages.cue.resolve import ambiguity, resolve_sections, short_section

__all__ = ["cue"]


def cue(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None, allow_unknown: bool = False) -> CueResult:
    """Resolve every cue phrase against the narration and write `build/cue-times.json`.

    `only` resolves those sections and leaves every other section's rows in the file as they were,
    so a run aimed at one section never drops the cues of the rest. `allow_unknown` keeps a row no
    page declares out of the findings, which is the author saying they know about it.
    """
    started = clock()
    wanted = selects(only)
    takes = Takes.require(inputs.workspace.takes_path, Artifact.TAKES)
    cued = [block for block in inputs.cues() if wanted(block.number)]
    words = {take.section: inputs.words(take.section, take.hash) for take in takes.sections}
    estimated = {take.section for take in takes.sections if not take.voiced}

    sections, judged = resolve_sections(
        cued,
        words,
        clips=inputs.document.clip_numbers,
        estimated=estimated,
        cues_file=inputs.relative(inputs.cues_path),
        stage=Stage.CUE,
    )
    for block in cued:
        with run.section(Stage.CUE, block.number):
            _say_what_was_chosen(run, block, words.get(block.number, ()))
    for found in judged:
        run.found(found)
    for found in _catalog_findings(inputs, only, allow_unknown):
        run.found(found)
    for found in _overlap_findings(inputs, sections):
        run.found(found)

    written = _write(inputs, sections, replacing=bool(only))
    run.wrote(written)
    return run.result(
        CueResult,
        sections=sections,
        file=inputs.relative(written),
        seconds=since(started),
    )


def _say_what_was_chosen(run: Run, block: CuedSection, words: Sequence[Word]) -> None:
    """Every choice this section's cues made for their author, as one sentence each.

    A phrase that occurs twice and a section whose speech runs shorter than its visuals ask for are
    both readings rather than judgements, so each is a line on the stream and no code is invented
    for it.
    """
    for row in block.cues:
        said = ambiguity(row, words)
        if said:
            run.note(said, level=Level.WARNING)
    short = short_section(block, words)
    if short:
        run.note(short, level=Level.WARNING)


def _catalog_findings(inputs: Inputs, only: Sequence[int] | None, allow_unknown: bool) -> list[Finding]:
    """Every moment with no row and every row no page declares, judged from what the pages published."""
    wanted = selects(only)
    sections = [section for section in inputs.document.page_sections if wanted(section.number)]
    declared = declared_cues(_catalogs(inputs), sections)
    cued = [block for block in inputs.cues() if wanted(block.number)]
    return cue_findings(
        declared,
        cued,
        cues_path=inputs.cues_path,
        root=inputs.root,
        stage=Stage.CUE,
        allow_unknown=allow_unknown,
    )


def _catalogs(inputs: Inputs) -> dict[str, tuple[MeasuredScene, ...]]:
    """The catalog each page published, read from the recording the last run left beside it.

    A recording log keeps the page's whole report, so the catalog is the page's own document rather
    than a reading of its markup. A page nothing has recorded is absent from this map, which is what
    leaves its sections unjudged instead of judged against nothing.
    """
    out: dict[str, tuple[MeasuredScene, ...]] = {}
    for section in inputs.document.page_sections:
        if section.page in out:
            continue
        log = inputs.recording_log(section.key)
        if log is not None and log.report.catalog:
            out[section.page] = tuple(log.report.catalog)
    return out


def _overlap_findings(inputs: Inputs, sections: Sequence[SectionCues]) -> list[Finding]:
    """Every pair of cues that fire closer together than the first one is still moving for.

    The span comes from the measured rows the page published, so a pair is judged against the motion
    the deck actually declares. A section whose page nothing has recorded has no spans to judge
    against, and a ceiling guessed for it would report every quick reveal in the deck as an overlap,
    so it is left alone.
    """
    catalogs = _catalogs(inputs)
    scale = inputs.settings.motion.scale
    by_number = {block.section: block for block in sections}
    found: list[Finding] = []
    for section in inputs.document.page_sections:
        block = by_number.get(section.number)
        entries = catalogs.get(section.page)
        if block is None or entries is None:
            continue
        entry = next((one for one in entries if str(one.scene) == str(section.scene)), None)
        if entry is None:
            continue
        times = {row.cue: row.seconds for row in block.cues if row.seconds is not None}
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
    previous = inputs.cue_times() if replacing else None
    kept = [block for block in previous.sections if block.section not in resolved] if previous else []
    blocks = sorted([*kept, *sections], key=lambda block: block.section)
    return CueTimes(sections=tuple(blocks)).write(inputs.workspace.cue_times_path)
