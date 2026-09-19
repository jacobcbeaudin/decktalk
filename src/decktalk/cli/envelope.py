"""The one `--json` envelope, the exit policy behind it, and the progress log of a long run.

Every command prints exactly one object on stdout under `--json`, and an error prints it too, so a
caller parses the same shape whatever happened. `schema` is that shape's version and is bumped only
when the shape changes, which is what lets a reader tell a shape change from a release.

`findings.items[]` is the one place a reader looks for rows, whichever command ran. A payload row
carries its verdict as the one `{code, label, certain}` object, so the rows are lifted out
of the payload rather than written a second time by hand, and a command that grows a row gets it in
the envelope without the envelope knowing anything about that command. The failing rows lead.

Nothing here imports a stage or knows a result's shape. A result satisfies `StageResult`, which is a
tally and JSON-ready data, and this module turns the pair into an exit code and one object.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

from ..errors import DeckTalkError
from ..jsonio import dumps, relative
from ..verdicts import Finding, Findings, StageResult, Verdict

SCHEMA = 1
"""The envelope's shape version. It is not the product version, and it moves only with the shape."""

VERDICT_KEYS = ("code", "label", "certain")
USAGE = "USAGE"
INTERNAL = "INTERNAL"


@dataclass(frozen=True)
class Outcome:
    """What one handler produced, before the exit policy turns it into a code and an envelope.

    `text` is what a person reads and `payload` is what a program reads, and both come from the one
    result object, so the table and the JSON can never disagree. `rows` is filled only by a command
    whose findings live nowhere in its payload, and every other command's rows are lifted from it.
    """

    payload: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    findings: Findings = field(default_factory=Findings)
    written: list[str] = field(default_factory=list)
    text: str = ""
    rows: list[Finding] = field(default_factory=list)
    after: Callable[[], None] | None = None
    """What to do once the envelope is printed, for the one command that then blocks."""


def wrote(root: Path, *paths: Path | None) -> list[str]:
    """The files a command wrote, project-relative, in the order it wrote them and each one once."""
    named = [relative(p, root) for p in paths if p is not None and p.exists()]
    return list(dict.fromkeys(named))


def of(
    result: StageResult,
    root: Path,
    summary: dict[str, Any],
    written: list[str],
    text: str,
    rows: list[Finding] | None = None,
) -> Outcome:
    """One stage result as an outcome, without the CLI knowing that result's shape.

    `rows` is given by a command whose rows are not inside its payload, so they are never written
    into the payload a second time under another name.
    """
    return Outcome(
        payload=result.to_dict(root),
        summary=summary,
        findings=result.findings,
        written=written,
        text=text,
        rows=list(rows or []),
    )


# ---- the exit policy -------------------------------------------------------------------


def exit_for(findings: Findings, strict: bool, exit_zero: bool) -> int:
    """The exit code of a command that ran, from what it found.

    A certain finding exits 1. An uncertain finding exits 1 only with `strict`. With `exit_zero`
    the command exits 0 whatever it found, which never hides an error, because an error exits 3
    before this is reached.
    """
    if exit_zero:
        return 0
    if findings.certain or (strict and findings.uncertain):
        return 1
    return 0


def error_code(exc: BaseException) -> str:
    """The code of an error, drawn from its class name. Anything unplanned is `INTERNAL`."""
    if isinstance(exc, DeckTalkError) and type(exc) is not DeckTalkError:
        name = type(exc).__name__.removesuffix("Error")
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
    return INTERNAL


def error_of(exc: BaseException, root: Path | None = None) -> dict[str, Any]:
    """The envelope's error slot: the code, one sentence, the smallest next action and where it is.

    `hint`, `path` and `line` are read from the exception when it carries them, so a raiser says
    where the problem is without the envelope guessing, and they are null when it does not.
    """
    path = getattr(exc, "path", None)
    if isinstance(path, PurePath):
        path = relative(Path(path), root) if root is not None else Path(path).as_posix()
    return {
        "code": error_code(exc),
        "message": str(exc) or type(exc).__name__,
        "hint": getattr(exc, "hint", None),
        "path": path,
        "line": getattr(exc, "line", None),
    }


def usage_error(message: str) -> dict[str, Any]:
    """The error slot of a command line the parser refused."""
    return {"code": USAGE, "message": message, "hint": "run `decktalk <command> --help`", "path": None, "line": None}


# ---- the rows every command shares -----------------------------------------------------


def expand(value: Any) -> Any:
    """JSON-ready data in which a verdict is the `{code, label, certain}` object, never a label.

    A verdict is a string enum, so `json.dumps` would write it as its label, and a reader would be
    left parsing labels in every payload at once. Opening it out here is the one place that happens.
    """
    if isinstance(value, Verdict):
        return value.to_dict()
    if isinstance(value, Enum):
        return expand(value.value)
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(k): expand(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [expand(v) for v in value]
    return value


def _is_verdict(value: Any) -> bool:
    return isinstance(value, dict) and all(k in value for k in VERDICT_KEYS)


def _is_finding(value: Any) -> bool:
    """A row written by `Finding.to_dict` already has the envelope's shape and is taken as it is."""
    return _is_verdict(value) and "detail" in value


def _judged(code: Any) -> bool:
    """Whether a code names a finding. A passing verdict and a bare note are neither kind."""
    try:
        return not Verdict[str(code)].passing
    except KeyError:
        return True


def _section(node: dict[str, Any], inherited: int | None) -> int | None:
    for key in ("section", "key"):
        value = node.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return inherited


def _first(node: dict[str, Any], *keys: str) -> Any:
    return next((node[k] for k in keys if isinstance(node.get(k), str) and node[k]), None)


def _row(node: dict[str, Any], verdict: dict[str, Any], section: int | None) -> dict[str, Any]:
    """One payload row as the uniform findings row, taking what the row happens to name."""
    return {
        "code": verdict["code"],
        "label": verdict["label"],
        "certain": verdict["certain"],
        "section": _section(node, section),
        "cue": _first(node, "cue", "check"),
        "where": _first(node, "where", "page", "file", "path", "after", "before"),
        "detail": _first(node, "detail", "note", "reason"),
    }


def _verdicts_in(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every verdict a row carries, whether the row names one or a list of them."""
    found: list[dict[str, Any]] = []
    for value in node.values():
        items = value if isinstance(value, list) else [value]
        found += [v for v in items if _is_verdict(v) and not _is_finding(v)]
    return found


def _walk(node: Any, out: list[dict[str, Any]], section: int | None) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, out, section)
        return
    if not isinstance(node, dict):
        return
    if _is_finding(node):
        out.append({k: node.get(k) for k in (*VERDICT_KEYS, "section", "cue", "where", "detail")})
        return
    here = _section(node, section)
    out += [_row(node, verdict, here) for verdict in _verdicts_in(node)]
    for value in node.values():
        _walk(value, out, here)


def finding_rows(payload: Any, extra: Iterable[Finding] = ()) -> list[dict[str, Any]]:
    """Every judged row of a payload, in one shape, with the certain rows first.

    Long output leads with what failed, so a reader that stops after the first rows has read the
    rows worth reading. Rows a command holds outside its payload are passed in and sort with them.
    """
    rows: list[dict[str, Any]] = [f.to_dict() for f in extra]
    _walk(payload, rows, None)
    judged = [r for r in rows if _judged(r.get("code"))]
    return [r for r in judged if r.get("certain")] + [r for r in judged if not r.get("certain")]


# ---- the envelope ----------------------------------------------------------------------


def envelope_of(
    command: str,
    version: str,
    *,
    ok: bool,
    exit_code: int,
    summary: dict[str, Any] | None = None,
    findings: Findings | None = None,
    items: list[dict[str, Any]] | None = None,
    written: list[str] | None = None,
    error: dict[str, Any] | None = None,
    payload: Any = None,
) -> dict[str, Any]:
    """One envelope, in the order a reader meets it: what ran, how it ended, what it found."""
    tally = (findings or Findings()).to_dict()
    return {
        "schema": SCHEMA,
        "version": version,
        "command": command,
        "ok": ok,
        "exit_code": exit_code,
        "summary": summary or {},
        "findings": {**tally, "items": items or []},
        "written": written or [],
        "error": error,
        command: payload,
    }


def emit(doc: dict[str, Any]) -> None:
    """Print the envelope, and nothing else, on stdout."""
    print(dumps(expand(doc)))


# ---- the progress log ------------------------------------------------------------------


@dataclass
class ProgressLog:
    """JSON Lines of what a long run is doing, so a caller polls a file instead of tailing a log.

    The file is truncated when the run starts and appended to as it goes, one object per event, so
    a reader that arrives late reads this run and never the one before it. `stages` is the sequence
    the run will execute, so the index and the count it writes are the ones a caller can count on.

    Every row carries the process id of the run, which is how `status` answers whether a build that
    left no closing event is still going or died.
    """

    path: Path
    stages: tuple[str, ...] = ()
    pid: int = field(default_factory=os.getpid)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def event(self, stage: str, event: str, *, section: int | None = None, detail: str | None = None) -> None:
        """Append one event. A stage the run did not announce takes the last index, so a reader takes
        its `done` event for the end of the run."""
        index = self.stages.index(stage) + 1 if stage in self.stages else len(self.stages)
        row = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "pid": self.pid,
            "stage": stage,
            "stage_index": index,
            "stage_count": len(self.stages),
            "section": section,
            "event": event,
            "detail": detail,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def line_buffer_stdout() -> None:
    """Keep stdout line buffered, so the envelope and the stderr log stay in order in a pipe."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)
