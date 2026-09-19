"""What a project's four input files say, and what it has built so far, read from disk.

`status(project)` collects everything `decktalk status` reports into a `StatusResult`. The CLI's
table and its `--json` output both read that one object, so the two never disagree.

It also reads the four input files against each other, because they are only right together: a
section in `decktalk.toml` needs its heading in `script.md`, a cue in `cues.json` needs its
section, and a page or clip a section names needs to exist. Reading the inputs against each other
is this command's work rather than its obstacle, so a file that does not parse is a certain finding
here and not the error it is everywhere else, and one read-only command says what is wrong before
anything is voiced or recorded.

It reads `build/progress.jsonl` too, so `run` answers whether a build is going without anyone
tailing a file. A build started with `--progress PATH` keeps its log out of this report, which is
the point of moving it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import Takes
from .errors import DeckTalkError
from .jsonio import relative
from .media.ffmpeg import probe_duration
from .model import ClipSection, PageSection, Project, Section
from .verdicts import Finding, Findings, Verdict


@dataclass
class SectionStatus:
    """One section: whether it is a page or a clip, where it comes from, and what exists for it."""

    key: str
    kind: str  # "page" or "clip"
    source: str  # "deck/index.html?scene=1" for a page, the clip's path for a clip
    recorded: bool
    cut: bool


@dataclass
class OutputStatus:
    """One file assemble writes beside the final mp4."""

    label: str  # "captions" or "chapters"
    key: str  # "srt", "vtt" or "chapters"
    path: Path
    exists: bool


@dataclass
class RunStatus:
    """The build the progress log describes, and whether the process that wrote it is still going."""

    pid: int | None
    started: str | None
    stage: str | None
    sections_done: int | None
    sections_total: int | None
    alive: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "started": self.started,
            "stage": self.stage,
            "sections_done": self.sections_done,
            "sections_total": self.sections_total,
            "alive": self.alive,
        }


def _running(pid: Any) -> bool:
    """Whether the process that wrote the progress log is still on this machine."""
    if not isinstance(pid, int):
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return bool(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:  # Another user owns it, so it is running and not ours to signal.
        return True
    except OSError:
        return False
    return True


def read_run(path: Path) -> RunStatus | None:
    """The run `build/progress.jsonl` describes, or None when no build has written one here.

    A run is over when its last stage closed or a stage failed, and only an unfinished run asks the
    machine whether its process is still there, so a finished build never depends on a reused pid.
    """
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:  # The writer appends and flushes, so a torn last line is normal.
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows:
        return None
    last = rows[-1]
    done_sections = {r.get("section") for r in rows if r.get("event") == "done" and r.get("section") is not None}
    ended = last.get("event") == "fail" or (
        last.get("event") == "done" and last.get("stage_index", 0) >= last.get("stage_count", 0)
    )
    return RunStatus(
        pid=last.get("pid"),
        started=rows[0].get("ts"),
        stage=last.get("stage"),
        sections_done=len(done_sections) or None,
        sections_total=None,
        alive=not ended and _running(last.get("pid")),
    )


@dataclass
class StatusResult:
    """Everything `decktalk status` prints, as data."""

    root: Path
    name: str
    script: Path
    script_exists: bool
    cues: Path
    cues_exists: bool
    sections: list[SectionStatus]
    takes: Takes | None
    cue_times_exists: bool
    cue_times_sections: dict[str, dict[str, float]]
    final: Path
    final_exists: bool
    final_duration: float | None
    outputs: list[OutputStatus] = field(default_factory=list)
    problems: list[Finding] = field(default_factory=list)
    run: RunStatus | None = None

    @property
    def findings(self) -> Findings:
        """Certain: an input file that is missing, unreadable, or disagrees with the others."""
        return Findings.of(row.verdict for row in self.problems)

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The report as JSON-ready data, with every path relative to the project root."""
        index = self.takes
        return {
            "project": {
                "root": self.root.as_posix(),
                "name": self.name,
                "script": relative(self.script, root),
                "script_exists": self.script_exists,
                "cues": relative(self.cues, root),
                "cues_exists": self.cues_exists,
            },
            "sections": [
                {
                    "key": s.key,
                    "kind": s.kind,
                    "source": s.source,
                    "recorded": s.recorded,
                    "cut": s.cut,
                }
                for s in self.sections
            ],
            "narration": {
                "exists": index is not None,
                "estimated": bool(index and index.estimated),
                "total_seconds": index.total_seconds if index else None,
                "sections": [] if index is None else [_clock_row(index, key) for key in index.keys],
            },
            "cue_times": {
                "exists": self.cue_times_exists,
                "sections": [{"key": key, "cues": dict(cues)} for key, cues in self.cue_times_sections.items()],
            },
            "final": {
                "path": relative(self.final, root),
                "exists": self.final_exists,
                "duration": self.final_duration,
            },
            "outputs": {o.key: {"path": relative(o.path, root), "exists": o.exists} for o in self.outputs},
            # The rows this command judged, under a key of its own, so `findings.items[]` is lifted
            # out of the payload here as it is on every other command rather than passed beside it.
            "problems": [row.to_dict() for row in self.problems],
            "run": None if self.run is None else self.run.to_dict(),
        }


def _missing(section: Section, path: Path, root: Path, what: str) -> Finding:
    """One section's file that is not there, named as the row a reader receives."""
    return Finding(
        detail=f"section {section.key} names a {what} that is not there",
        verdict=Verdict.MISSING,
        section=section.number,
        where=relative(path, root),
    )


def unreadable(error: DeckTalkError, path: Path, root: Path, verdict: Verdict = Verdict.UNREADABLE) -> Finding:
    """One input file a loader refused, with everything the error knows folded into its row.

    `UNREADABLE` is a file that would not parse and `INCONSISTENT` is a file that parsed and
    contradicts another, because one is repaired and the other is reconciled. Neither is `MISSING`,
    since a reader that took a file that is there for a file that is not would write over the
    author's. The line and the next action go into `detail`, which is the only slot a row has for
    either.
    """
    where = relative(error.path or path, root)
    line = f" Line {error.line}." if error.line is not None else ""
    hint = f" {error.hint}" if error.hint else ""
    return Finding(detail=f"{error}{line}{hint}", verdict=verdict, where=where)


def _parses(path: Path) -> bool:
    """Whether the file's own syntax is readable, asked of the format rather than of the loader."""
    if path.suffix != ".json" or not path.exists():
        return True
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    return True


def _refused(error: DeckTalkError, path: Path, root: Path) -> Finding:
    """One input file the loaders refused, read as a parse failure or as a disagreement.

    The two are told apart by asking the file's own format, because a file whose syntax is fine and
    whose content contradicts another is reconciled and never repaired.
    """
    return unreadable(error, path, root, Verdict.UNREADABLE if not _parses(path) else Verdict.INCONSISTENT)


def problems(project: Project) -> list[Finding]:
    """Every way the four input files disagree, read without running a stage.

    `script.md` and `cues.json` are each parsed against the sections `decktalk.toml` declares, and
    the loaders already refuse a section that is not there or a heading that is missing, so the
    error each of them raises is the finding, with the file it is about. A page or a clip a section
    names is checked last, because a project with no cues at all still has to open its pages.
    """
    found: list[Finding] = []
    if not project.script.exists():
        found.append(Finding(detail="script.md is not there.", verdict=Verdict.MISSING,
                             where=relative(project.script, project.root)))  # fmt: skip
    else:
        try:
            project.script_sections()
        except DeckTalkError as err:
            found.append(_refused(err, project.script, project.root))
    try:
        project.cue_specs()
    except DeckTalkError as err:
        found.append(_refused(err, project.cues, project.root))
    for section in project.sections:
        if isinstance(section, PageSection):
            page = project.path(section.page)
            if not page.exists():
                found.append(_missing(section, page, project.root, "page"))
        elif not section.optional and not project.path(section.clip).exists():
            found.append(_missing(section, project.path(section.clip), project.root, "clip"))
    return found


def _read(load: Callable[[], Any], path: Path, root: Path) -> tuple[Any, Finding | None]:
    """One build artifact, or nothing and the row that says the file on disk will not parse."""
    try:
        return load(), None
    except (DeckTalkError, json.JSONDecodeError, UnicodeDecodeError) as err:
        detail = f"{path.name} is not valid JSON: {err}."
        return None, Finding(detail=detail, verdict=Verdict.UNREADABLE, where=relative(path, root))


def _clock_row(index: Takes, key: str) -> dict[str, Any]:
    """Where one section sits on the narration clock, which the take index works out from its rows."""
    return {
        "key": key,
        "title": index.sections[key].chapter,
        "start": index.start(key),
        "end": index.end(key),
        "duration": index.span(key),
    }


def status(project: Project) -> StatusResult:
    """Read what exists for the project. Nothing is written, and only the final mp4 is probed."""
    sections = []
    for sec in project.sections:
        if isinstance(sec, ClipSection):
            kind, source = "clip", sec.clip
        else:
            kind, source = "page", f"{sec.page}?scene={sec.scene}"
        sections.append(
            SectionStatus(
                key=sec.key,
                kind=kind,
                source=source,
                recorded=project.recording(sec).exists(),
                cut=project.section_video(sec).exists(),
            )
        )
    duration = probe_duration(project.final) if project.final.exists() else None
    paths = project.workspace.output_paths()
    outputs = [
        OutputStatus(label, key, paths[key], paths[key].exists())
        for label, key in (("captions", "srt"), ("captions", "vtt"), ("chapters", "chapters"))
    ]
    found = problems(project)
    # A build artifact a hand edit broke is a row like any other, because reading what is on disk is
    # this command's work and a reader that met exit 3 here would be told DeckTalk was at fault.
    cue_times, broken = _read(project.cue_times, project.cue_times_path, project.root)
    takes, takes_broken = _read(project.takes, project.takes_path, project.root)
    found += [row for row in (broken, takes_broken) if row is not None]
    times = {} if cue_times is None else {k: cue_times.times(k) for k in cue_times.sections if cue_times.sections[k]}
    return StatusResult(
        root=project.root,
        name=project.name,
        script=project.script,
        script_exists=project.script.exists(),
        cues=project.cues,
        cues_exists=project.cues.exists(),
        sections=sections,
        takes=takes,
        cue_times_exists=project.cue_times_path.exists(),
        cue_times_sections=times,
        final=project.final,
        final_exists=project.final.exists(),
        final_duration=duration,
        outputs=outputs,
        problems=found,
        run=read_run(project.build / "progress.jsonl"),
    )
