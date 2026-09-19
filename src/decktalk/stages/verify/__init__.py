"""Stage 5: the one read-only checker, over the finished mp4 and the logs that made it.

    plan.py      the probe arithmetic, with no ffmpeg, no file and no project
    measure.py   the ffmpeg calls behind the plan, the onset scan and the cue loop
    seams.py     the start, cut and seam checks

`verify` reports five tables. `recordings` repeats what each recording log judged, so a page that
threw or an equation that was never typeset is still a finding after the build that recorded it.
`starts` checks that every section opens on a real picture past the dip to black. `cuts` checks that
the narration is quiet in the window before each cut, so no cut lands on speech. `seams` checks that
a section which sets `seamless` opens on the picture the section before it ended on. `cues` measures
each reveal against its cue time, and after a build without voice against the click on its word too.
`reference/verify.mdx` defines every measurement and every limit.

Section starts are the cumulative lengths of `build/sections/NN.mp4` for the sections in
`decktalk.toml`, in order, which is the cut's own arithmetic measured again on the files it wrote.
A leftover section video of a section that is not in `decktalk.toml` is ignored with a warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import RecordingLog
from ...errors import ConfigError, MissingInputError
from ...jsonio import relative
from ...model import Project
from ...pipeline import Stage
from ...verdicts import Findings, Verdict
from .measure import assembled_starts, cue_checks
from .plan import CueCheck, default_checks, skipped, thin_change
from .seams import CutCheck, SeamCheck, StartCheck, cut_checks, seam_checks, start_checks

log = logging.getLogger(__name__)


def opted_out(project: Project) -> set[tuple[str, str]]:
    """(section key, cue id) for every cue that cues.json marks "verify": false."""
    return {(f"{spec.number:02d}", cue.cue) for spec in project.cue_specs() for cue in spec.cues if not cue.verify}


def wanted_checks(
    project: Project, checks: list[str] | None, only: list[int] | None
) -> tuple[list[str], set[tuple[str, str]]]:
    """(the cues to measure, the cues to skip as opted out).

    `checks` is the cues a caller named, checked for shape, or None for every resolved cue. `only`
    keeps the cues of those section numbers, whichever list they came from. A cue named by a caller
    is measured although `cues.json` opts it out, because naming it is asking for it, so the
    opted-out set is empty unless the list is the default one.
    """
    if checks is None:
        wanted = default_checks(project.cue_times(), only)
        return wanted, opted_out(project) if wanted else set()
    for check in checks:
        if ":" not in check or not check.split(":", 1)[0].strip().isdigit():
            raise ConfigError(
                f"the check {check!r} is not a section and a cue.",
                hint="Write it as SECTION:CUE, such as 3:3.1draw.",
            )
    return [c for c in checks if not only or int(c.split(":", 1)[0]) in only], set()


__all__ = [
    "CueCheck",
    "CutCheck",
    "LoggedRecording",
    "SeamCheck",
    "StartCheck",
    "VerifyResult",
    "opted_out",
    "skipped",
    "thin_change",
    "verify",
]


@dataclass(frozen=True)
class LoggedRecording:
    """One page section as its recording log reports it, which is `record`'s judgement read again."""

    key: str
    verdicts: tuple[Verdict, ...]
    page_errors: tuple[str, ...]
    t0_method: str | None
    where: str  # The recording log, relative to the project root.

    @property
    def ok(self) -> bool:
        return not self.verdicts

    @property
    def detail(self) -> str | None:
        """The one sentence this row carries, which names what the log judged and the first page error."""
        if not self.verdicts:
            return None
        said = " ".join(v.label for v in self.verdicts)
        first = f" The page said: {self.page_errors[0]}" if self.page_errors else ""
        return f"The recording log for section {self.key} reports {said}.{first}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "where": self.where,
            "t0_method": self.t0_method,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "detail": self.detail,
            "page_errors": list(self.page_errors),
        }


def logged_recordings(project: Project, only: list[int] | None = None) -> list[LoggedRecording]:
    """What each page section's recording log judged, for the sections `only` keeps."""
    rows: list[LoggedRecording] = []
    for section in project.page_sections:
        if only and section.number not in only:
            continue
        path = project.recording_log(section)
        recording_log = RecordingLog.load(path)
        if recording_log is None:
            continue
        rows.append(
            LoggedRecording(
                key=section.key,
                verdicts=recording_log.checks.verdicts if recording_log.checks else (),
                page_errors=tuple(recording_log.page_errors),
                t0_method=recording_log.t0_method,
                where=relative(path, project.root),
            )
        )
    return rows


@dataclass
class VerifyResult:
    """Everything one `verify` run judged about a finished film."""

    total_seconds: float
    recordings: list[LoggedRecording] = field(default_factory=list)
    starts: list[StartCheck] = field(default_factory=list)
    cuts: list[CutCheck] = field(default_factory=list)
    cues: list[CueCheck] = field(default_factory=list)
    seams: list[SeamCheck] = field(default_factory=list)
    final: Path | None = None
    silent: bool = False  # The build carries placeholder narration, so the a/v column is measured.

    @property
    def ok(self) -> bool:
        return (
            all(r.ok for r in self.recordings)
            and all(s.ok for s in self.starts)
            and all(c.ok for c in self.cuts)
            and all(c.ok for c in self.seams)
            and all(c.ok for c in self.cues if not c.skipped)
        )

    @property
    def black_starts(self) -> int:
        return sum(not s.ok for s in self.starts)

    @property
    def recorded_findings(self) -> Findings:
        """What the recording logs judged, which this stage repeats rather than measures."""
        return Findings.of(v for r in self.recordings for v in r.verdicts)

    @property
    def film_findings(self) -> Findings:
        """Every verdict this run measured on the finished film itself."""
        return Findings.of(
            [
                *(s.verdict for s in self.starts),
                *(c.verdict for c in self.cuts),
                *(k.verdict for k in self.seams),
                *(c.verdict for c in self.cues),
            ]
        )

    @property
    def findings(self) -> Findings:
        """Every recording, start, cut, seam and cue verdict, tallied."""
        return self.recorded_findings + self.film_findings

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data: numbers as numbers, and paths relative to the project root."""
        # Every row of these four tables judges the finished film, so that is the file each one is
        # about, and a reader dispatching on findings.items[] alone is told which file to open.
        film = None if self.final is None else relative(self.final, root)
        return {
            "final": film,
            "total_seconds": round(self.total_seconds, 3),
            "silent": self.silent,
            "recordings": [r.to_dict() for r in self.recordings],
            "starts": [s.to_dict(film) for s in self.starts],
            "cuts": [c.to_dict(film) for c in self.cuts],
            "seams": [c.to_dict(film) for c in self.seams],
            "cues": [c.to_dict(film) for c in self.cues],
        }


def verify(project: Project, checks: list[str] | None = None, only: list[int] | None = None) -> VerifyResult:
    """Check the recordings, the section starts, the cuts, the seams and the cues on the final mp4.

    `checks` names cues as SECTION:CUE. None checks every cue in cue-times.json except those opted
    out in cues.json, and an empty list checks no cues. `only` keeps the rows of those section
    numbers. A cue named explicitly is measured even when it is opted out.
    """
    final = project.final
    for message in project.stray_section_warnings(Stage.VERIFY):
        log.warning(message)
    starts, total = assembled_starts(project)
    if not starts or not final.exists():
        raise MissingInputError(
            "the section videos and the final mp4 are not both there, so there is nothing to verify.",
            hint="Run `decktalk assemble` first.",
        )
    takes = project.takes()
    result = VerifyResult(
        total_seconds=total,
        final=final,
        silent=bool(takes and takes.estimated),
        recordings=logged_recordings(project, only),
    )
    result.starts = start_checks(project, final, starts)
    result.cuts = cut_checks(project, project.takes(), starts)
    result.seams = seam_checks(project, final, starts)
    wanted, opt_out = wanted_checks(project, checks, only)
    if wanted:
        result.cues = cue_checks(project, final, starts, total, wanted, opt_out)
    return result
