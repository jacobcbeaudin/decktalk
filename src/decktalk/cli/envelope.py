"""The one `--json` envelope, and the exit policy behind it.

Every command prints exactly one object on stdout under `--json`, and an error prints it too, so a
caller parses the same shape whatever happened. `schema` is that shape's version and is bumped only
when the shape changes, which is what lets a reader tell a shape change from a release.

`findings.items[]` is the one place a reader looks for rows, whichever command ran. A payload row
carries its verdict as the one `{code, label, certain}` object, so the rows are lifted out
of the payload rather than written a second time by hand, and a command that grows a row gets it in
the envelope without the envelope knowing anything about that command. The failing rows lead.

Nothing here imports a stage or knows a result's shape. A result satisfies `StageResult`, which is a
tally and JSON-ready data, and this module turns the pair into an exit code and one object. The
payload is printed exactly as the result wrote it, so a stage's `to_dict` and the CLI's JSON are the
same data and never two spellings of it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from ..errors import DeckTalkError, ErrorCode
from ..jsonio import dumps, relative
from ..verdicts import FINDING_KEYS, VERDICT_KEYS, Finding, Findings, StageResult, Verdict

SCHEMA = 1
"""The envelope's shape version. It is not the product version, and it moves only with the shape."""
WHERE_KEYS = ("where", "page", "file", "path", "after", "before")
"""The keys a payload row may name its file under, in the order a findings row takes its `where` from."""


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


def error_code(exc: BaseException) -> ErrorCode:
    """The code of an error, which the error class it is a kind of carries.

    The codes are a closed list, so a stage that subclasses one of them to carry a result with its
    refusal reports the code of the class it inherits rather than minting a seventh name that no
    caller can dispatch on. Anything unplanned is `INTERNAL`.
    """
    return exc.code if isinstance(exc, DeckTalkError) else ErrorCode.INTERNAL


def error_of(exc: BaseException, root: Path | None = None) -> dict[str, Any]:
    """The envelope's error slot: the code, one sentence, the smallest next action and where it is.

    `hint`, `path` and `line` are read from the exception when it carries them, so a raiser says
    where the problem is without the envelope guessing, and they are null when it does not.
    """
    path = getattr(exc, "path", None)
    if isinstance(path, PurePath):
        path = relative(Path(path), root) if root is not None else Path(path).as_posix()
    return {
        "code": error_code(exc).value,
        "message": str(exc) or type(exc).__name__,
        "hint": getattr(exc, "hint", None),
        "path": path,
        "line": getattr(exc, "line", None),
    }


def usage_error(message: str) -> dict[str, Any]:
    """The error slot of a command line the parser refused."""
    hint = "run `decktalk <command> --help`"
    return {"code": ErrorCode.USAGE.value, "message": message, "hint": hint, "path": None, "line": None}


# ---- the rows every command shares -----------------------------------------------------


def _is_verdict(value: Any) -> bool:
    return isinstance(value, dict) and all(k in value for k in VERDICT_KEYS)


def _is_finding(value: Any) -> bool:
    """A row written by `Finding.to_dict` already has the envelope's shape and is taken as it is."""
    return _is_verdict(value) and "detail" in value


def _judged(row: dict[str, Any]) -> bool:
    """Whether a row names a finding. A passing verdict and a bare note are neither kind.

    The verdict is read back from its object, so a row whose code names no verdict, or whose label
    or certainty is not its code's, is a payload this package wrote wrong and stops the command.
    """
    return not Verdict.from_dict({key: row[key] for key in VERDICT_KEYS}).passing


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
    """One payload row as the uniform findings row, taking what the row happens to name.

    The file a row is about may sit under the name its own table gives it, and the sentence is always
    under `detail`, because a producer fills it on every judged row.
    """
    return {
        "code": verdict["code"],
        "label": verdict["label"],
        "certain": verdict["certain"],
        "section": _section(node, section),
        "cue": _first(node, "cue"),
        "where": _first(node, *WHERE_KEYS),
        "detail": _first(node, "detail"),
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
        out.append({key: node.get(key) for key in FINDING_KEYS})
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
    judged = [r for r in rows if _judged(r)]
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
    """Print the envelope, and nothing else, on stdout. A value JSON cannot hold is a bug, and refused."""
    print(dumps(doc))


def line_buffer_stdout() -> None:
    """Keep stdout line buffered, so the envelope and the stderr log stay in order in a pipe."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)
