"""Stage 1: `script.md` becomes one take per section, indexed by content hash.

    build/narration/<hash>.mp3         one take, named by the content that produced it
    build/narration/<hash>.words.json  a start and an end for every word in it
    build/narration/takes.json         the index: which section plays which take, what it cost, and
                                       where each section lands once the takes are joined
    build/narration/narration.mp3      every take joined, with each section's silence around it

The script is markdown with "## N. Title" sections, parsed by `model/script.py`. The take a
section plays is found by content alone, so inserting a section, renumbering one or retitling one
moves no file and voices nothing, and two sections with the same words share one take.
`[narration] cache_dir` puts the take files alone outside `build/`, where a fresh clone and a second
worktree find them again. A hash names each one, so many projects may share one such directory,
while the index and the joined track stay under `build/narration/` because they are one project's.

`silent=True` needs no API key: placeholder click tracks sized at `silent_words_per_minute` plus
the declared pauses, with evenly spaced estimated words, so the whole pipeline runs offline. It
refuses only the targeted sections that already hold a paid take, so a project that has paid for
eight sections still rehearses its ninth for nothing.

`dry_run=True` sends nothing at all and returns the plan: what each section would send, what the
stitching context adds, and what it costs at `[voice] price_per_1000_characters`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import Take, Takes
from ...errors import ConfigError, MissingInputError
from ...jsonio import as_json, relative
from ...model import Project
from ...model.script import Segment
from ...settings import NarrationConfig
from ...verdicts import Finding, Findings
from .plan import (
    TakePlan,
    is_cached,
    plan_totals,
    silent_plan,
    speech_provider,
    take_name,
    text_hash,
    voiced_plan,
    words_name,
)
from .script_rules import check_script, symbol_findings
from .takes import (
    estimated_words,
    index_cached_take,
    join_takes,
    placed,
    write_silent_take,
    write_voiced_take,
)

log = logging.getLogger(__name__)

# The package's surface: what `preflight`, the CLI, the tables and the golden take-hash test read.
__all__ = [
    "NarrateResult",
    "TakePlan",
    "join_takes",
    "estimated_words",
    "is_cached",
    "narrate",
    "placed",
    "plan_totals",
    "take_name",
    "text_hash",
    "voiced_plan",
    "words_name",
]


def _refuse_silent_over_voiced(
    project: Project, previous: Takes | None, targets: list[Segment], spoken: list[Segment]
) -> None:
    """Raise when a run without voice would drop a paid take from the index, for a section it was asked to write.

    Only the targeted sections are weighed, so `--only` over the sections nobody has paid for is
    the cheap rehearsal, and a project that has paid for everything is the only one that stops. The
    advice is drawn from every spoken section rather than from the targets, because a run that was
    aimed at one paid section must still name the free sections it could have rehearsed instead.
    """
    rows = previous.sections if previous is not None else {}
    keys = {s.key for s in targets}
    paid = sorted(key for key, row in rows.items() if row.voiced and key in keys)
    if not paid:
        return
    free = sorted(s.key for s in spoken if not (s.key in rows and rows[s.key].voiced))
    advice = (
        f"Rehearse the sections nobody has paid for: {' '.join(f'--only {int(k)}' for k in free)}."
        if free
        else "Every section of this project is voiced already, so rehearse in a copy of it."
    )
    raise ConfigError(
        f"{relative(project.takes_path, project.root)} holds paid takes for section(s) {', '.join(paid)}, "
        f"and a run without voice would replace them in the take index, so the next voiced build would voice "
        f"them again and spend credits on all of them.",
        hint=advice,
        path=project.takes_path,
    )


@dataclass
class NarrateResult:
    """One narrate run: the plan it followed, the rows it judged, and the take index it left behind."""

    plans: list[TakePlan]
    voice: dict[str, Any]
    narration: NarrationConfig
    rate: float  # [voice] price_per_1000_characters, which prices the plan.
    rows: list[Finding] = field(default_factory=list)
    note: str | None = None  # Why the voice could not be set up, on a dry run that planned without it.
    takes: Takes | None = None  # None on a dry run, which writes nothing.
    synthesized: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)

    @property
    def segments(self) -> list[Segment]:
        """The sections this run considered, in script order."""
        return [p.segment for p in self.plans]

    @property
    def findings(self) -> Findings:
        """A script this stage cannot voice is an error, so what is left to judge is the wording."""
        return Findings.of(row.verdict for row in self.rows)

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The run as JSON-ready data: the plan and its price first, then what the run wrote."""
        index = self.takes
        return {
            "voice": self.voice,
            "note": self.note,
            "sections": [plan.to_dict(self.narration) for plan in self.plans],
            "totals": plan_totals(self.plans, self.narration, self.rate),
            "notes": [row.to_dict() for row in self.rows],
            "synthesized": list(self.synthesized),
            "cached": list(self.cached),
            "takes": None
            if index is None
            else {
                "model": index.model,
                "estimated": index.estimated,
                "total_seconds": index.total_seconds,
                "sections": [_take_row(key, take) for key, take in index.sections.items()],
            },
        }


def _take_row(key: str, take: Take) -> dict[str, Any]:
    """One row of the take index as JSON-ready data, with every field the row carries.

    The walker reads the dataclass, so a field added to `Take` reaches the payload with it and no
    third place has to learn the shape.
    """
    return {"key": key, **as_json(take)}


def narrate(
    project: Project,
    *,
    only: list[int] | None = None,
    force: bool = False,
    allow_placeholders: bool = False,
    silent: bool = False,
    model: str | None = None,
    dry_run: bool = False,
) -> NarrateResult:
    """Voice every targeted section, or plan what a voiced run would do and send nothing."""
    cfg = project.settings.narration
    where = relative(project.script, project.root)
    if not project.script.exists():
        raise MissingInputError(
            f"{where} is not there, so there is nothing to narrate.",
            hint="Write the script, or point [project] script at the file you meant.",
            path=project.script,
        )
    check_script(where, project.script.read_text(encoding="utf-8"))
    spoken = project.script_sections()[1]
    targets = [s for s in spoken if not only or s.index in set(only)]
    if not targets:
        raise ConfigError(f"no spoken section matches {only}. The spoken sections are {[s.index for s in spoken]}.")
    model = model or project.voice.model or cfg.model
    result = NarrateResult(
        plans=[],
        voice={"provider": project.voice.provider, "model": model, "settings": project.voice.api_settings()},
        narration=cfg,
        rate=project.voice.price_per_1000_characters,
        rows=symbol_findings(targets, where),
    )
    if dry_run:
        if silent:
            # A rehearsal spends nothing, so its plan is the placeholder plan and not a priced one.
            result.plans = silent_plan(project, targets, force=force)
        else:
            result.plans, result.note = voiced_plan(project, targets, model=model, force=force)
        return result
    if project.clip_numbers:
        log.info("skipping clip sections (no narration): %s", sorted(project.clip_numbers))
    previous = project.takes()
    provider = None
    if silent:
        if not force:
            _refuse_silent_over_voiced(project, previous, targets, spoken)
        result.plans = silent_plan(project, targets, force=force)
    else:
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled and not allow_placeholders:
            raise ConfigError(
                f"The script still holds the placeholders {unfilled}. Fill them, or pass allow_placeholders."
            )
        provider = speech_provider(project)
        result.plans, _note = voiced_plan(project, targets, model=model, force=force)
    # The index and the joined track are this project's own, and the takes may sit in a shared cache.
    project.narration_dir.mkdir(parents=True, exist_ok=True)
    project.takes_dir.mkdir(parents=True, exist_ok=True)
    takes = Takes(script=relative(project.script, project.root), model=model, output_format=cfg.output_format)
    if previous is not None:
        # The sections that --only leaves out keep the takes they already have.
        takes.sections = dict(previous.sections)
    for plan in result.plans:
        seg, digest, chapter = plan.segment, plan.digest, plan.chapter
        # A run that is not a dry run always has a provider, and so every plan carries a digest.
        assert digest is not None
        if plan.cached:
            takes.sections[seg.key] = index_cached_take(project, seg, chapter, digest, voiced=not silent)
            result.cached.append(seg.key)
            continue
        if silent:
            takes.sections[seg.key] = write_silent_take(project, seg, chapter, digest)
        else:
            assert provider is not None and plan.request is not None
            takes.sections[seg.key] = write_voiced_take(project, provider, seg, chapter, digest, plan.request)
        result.synthesized.append(seg.key)
        # The index is checkpointed after every call, paid or not, so a killed run loses nothing.
        takes.save(project.takes_path)
    valid = {s.key for s in spoken}
    # A section --only leaves out keeps its take, and is placed by the same rule as every other, so
    # its lead and tail follow its own settings whichever sections this run touched.
    touched = {plan.segment.key for plan in result.plans}
    takes.sections = {k: v if k in touched else placed(project, k, v) for k, v in takes.sections.items() if k in valid}
    estimated = any(not take.voiced for take in takes.sections.values())
    takes.estimate_basis = f"{cfg.silent_words_per_minute} wpm + declared pauses" if estimated else ""
    takes.save(project.takes_path)
    missing = [s.key for s in spoken if s.key not in takes.sections]
    if missing:
        log.warning("Sections %s have no take yet, so the narration covers the rest alone.", missing)
    result.takes = takes
    join_takes(project, takes, spoken)
    return result
