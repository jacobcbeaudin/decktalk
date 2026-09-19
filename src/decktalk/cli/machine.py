"""The commands that act on a machine rather than on a project: `init`, `install` and `doctor`.

None of them loads a project, and `doctor` is the one place a person is told what a run would use,
in a table or as the block `--report` prints for a bug report. Neither form carries a secret.
"""

from __future__ import annotations

from pathlib import Path

from .. import __version__
from ..media.ffmpeg import installed_paths
from ..scaffold import doctor as components
from ..scaffold import init as write_starter
from ..scaffold import install as fetch
from ..scaffold import report_block
from ..verdicts import Finding, Findings, Verdict
from . import options as opt
from . import output
from .envelope import Outcome


def init(opts: opt.InitOptions) -> Outcome:
    result = write_starter(
        Path(opts.dir), name=opts.name, force=opts.force, example_name=opts.example, skills=not opts.no_skills
    )
    files = [path.relative_to(result.root).as_posix() for path in result.written]
    payload = {
        "project": result.root.as_posix(),
        "name": opts.name or result.root.name,
        "example": result.example,
        "skills": result.skills,
        "files": files,
    }
    return Outcome(payload=payload, summary={"files": len(files)}, written=files, text=output.starter_note(result))


def install(opts: opt.InstallOptions) -> Outcome:
    fetch()
    found = installed_paths()
    payload = {"ffmpeg": found[0] if found else None, "ffprobe": found[1] if found else None}
    return Outcome(payload=payload, text="install complete")


def doctor(opts: opt.DoctorOptions) -> Outcome:
    rows = components()
    missing = [r for r in rows if not r.ok and not r.optional]
    found = [Finding(detail=f"{r.name}: {r.detail}", verdict=Verdict.MISSING, where=r.name) for r in missing]
    text = report_block(rows, __version__) if opts.report else output.doctor_table(rows)
    return Outcome(
        payload={"components": [r.to_dict() for r in rows]},
        summary={"components": len(rows), "missing": len(missing)},
        findings=Findings(certain=len(missing)),
        rows=found,
        text=text,
    )
