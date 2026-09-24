"""One coverage floor, measured from a real run and written down rather than typed.

    uv run scripts/check_coverage.py --write    # record what the run just measured
    uv run scripts/check_coverage.py --check    # exit 1 if the run measured less than the record

A number typed into a configuration file is a magic number wherever it sits, and a floor is the one
number in the repository that decides whether a change may land. So the floor here is measured:
`--write` reads the combined coverage data and records the total and one row per module, and
`--check` reads the same data and gates against that record. A row only ever rises, which is what
makes the record a ratchet rather than a wish, and `--write` refuses to lower one and says which
rows it refused.

**There is no second floor on the unit suite.** A floor on the fast suite alone would pressure a
contributor to cover `media/frames.py` with mocks, which turns a real gap into a fake proof, and the
whole point of measuring against ffmpeg and a real Chromium is that the proof is real.

**A suite that did not report is a failure rather than a lower floor.** A cancelled job leaves the
combined data short, and without this the floor would quietly drop on exactly the day something
broke. Each suite in `WITNESSES` names the module only that suite executes, so a leg that never
reported is named by its own suite rather than by whichever module fell first. The combined data
does not remember which leg contributed which line, because a context would have to be configured on
every leg and would be a second place to keep the suite list in step, so the evidence is the module
rather than a label.

This script carries no `# /// script` header, unlike every other script here, because it reads the
coverage data through the same `coverage` the suite wrote it with. The other two commands of the
`coverage` group run from the project environment for the same reason, so all three agree about what
a module scored.

The record holds whole percentages. A fraction of a point moves with a runner's own skip set and is
not a fact about the code, so the record rounds down and reads as a floor rather than as a
measurement.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import coverage

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "scripts" / "coverage-floor.json"
PACKAGE = "decktalk"

STALE = "{path} is out of date. Run: uv run scripts/check_coverage.py --write"
"""The one sentence every generator in this repository fails with, naming the file and the command."""

WITNESSES = {
    "browser": "decktalk/media/browser.py",
    "media": "decktalk/media/frames.py",
}
"""Each suite against the module only that suite really executes, which is how a missing leg is named.

The `e2e` leg has no witness yet. It drives the command line as a subprocess and a subprocess is
measured only when `COVERAGE_PROCESS_START` is set for it, so that leg contributes no data at all
today and its absence cannot be told from its presence. Its witness is `decktalk/__main__.py` the
day the environment variable is set on the leg that runs it.
"""

NAMED = {
    "decktalk/speech/elevenlabs.py": (
        "The one paid path. Every take the founder buys flows through it and no toolchain excuses "
        "it, so its row is named rather than left to the global number to absorb."
    ),
}
"""Every module whose row carries a sentence of its own, because the global number would hide it."""


def percentages() -> tuple[int, dict[str, int]]:
    """The total and the per-module rows of the combined coverage data, each rounded down.

    `analysis2` is the measurement the text report prints, so the record and the report can never
    disagree about what a module scored.
    """
    data = coverage.Coverage()
    data.load()
    rows: dict[str, int] = {}
    total_statements = total_missing = 0
    for measured in sorted(data.get_data().measured_files()):
        _name, statements, _excluded, missing, _formatted = data.analysis2(measured)
        if not statements:
            continue  # An empty module scores nothing, and `skip_empty` leaves it out of the report.
        total_statements += len(statements)
        total_missing += len(missing)
        rows[module_of(measured)] = floor_percent(len(statements) - len(missing), len(statements))
    return floor_percent(total_statements - total_missing, total_statements), rows


def module_of(measured: str) -> str:
    """One measured file as the record names it, which is its path from the package directory up."""
    parts = Path(measured).parts
    return "/".join(parts[parts.index(PACKAGE) :])


def floor_percent(covered: int, statements: int) -> int:
    """What a module scored, rounded down, so the record reads as a floor rather than a measurement."""
    return math.floor(100 * covered / statements) if statements else 0


@dataclass(frozen=True)
class Record:
    """The committed floor: what the numbers were measured on, the total, and one row per module."""

    measured_on: str
    total: int
    modules: dict[str, int]

    @classmethod
    def read(cls) -> Record:
        """The committed file, typed once here so that nothing below it reads an untyped mapping."""
        document: dict[str, Any] = json.loads(RECORD.read_text(encoding="utf-8"))
        return cls(
            measured_on=str(document["measured_on"]),
            total=int(document["total"]),
            modules={str(name): int(score) for name, score in document["modules"].items()},
        )

    @classmethod
    def empty(cls) -> Record:
        """What the first write starts from, which is a floor of nothing measured nowhere."""
        return cls(measured_on=platform.system().lower(), total=0, modules={})


def rendered(total: int, rows: dict[str, int], measured_on: str) -> str:
    """The record as the committed file holds it, which is two-space JSON with a trailing newline."""
    document = {
        "_comment": (
            "The coverage floor, measured by scripts/check_coverage.py --write and gated by --check. "
            "Every number is a whole percent rounded down and only ever rises. Never lower a row to "
            "make a run pass: write the test the row is asking for."
        ),
        "measured_on": measured_on,
        "total": total,
        "named": {name: NAMED[name] for name in sorted(NAMED) if name in rows},
        "modules": dict(sorted(rows.items())),
    }
    return json.dumps(document, indent=2) + "\n"


def silent_suites(rows: dict[str, int], modules: dict[str, int]) -> list[str]:
    """Every suite whose witness module scored under its own row, which is a leg that never reported.

    The test is the witness module's row rather than a zero, because the unit suite imports the same
    module behind a fake and an import alone already scores. Only the leg that really drives the tool
    reaches the row, so the row is the evidence that the leg ran.
    """
    return sorted(suite for suite, module in WITNESSES.items() if rows.get(module, 0) < modules.get(module, 0))


def check() -> int:
    """Gate the measured run against the committed record, naming every row that fell."""
    if not RECORD.exists():
        print(STALE.format(path=RECORD.relative_to(ROOT).as_posix()))
        return 1
    committed = Record.read()
    total, rows = percentages()
    modules = committed.modules

    silent = silent_suites(rows, modules)
    if silent:
        print(
            f"the {', '.join(silent)} suite covered less than its own floor, so either that leg "
            "never reported or it really regressed. A leg that did not run is a failure rather than "
            "a lower floor, so find out which before touching this record."
        )
        return 1

    floor = committed.total
    fallen = {name: (was, rows[name]) for name, was in modules.items() if name in rows and rows[name] < was}
    gone = sorted(name for name in modules if name not in rows)
    added = sorted(name for name in rows if name not in modules)
    if total < floor:
        print(f"coverage is {total} percent and the floor is {floor} percent, measured on {committed.measured_on}.")
    if fallen:
        print(
            "these modules are covered less than they were: "
            + ", ".join(f"{name} {was} to {now}" for name, (was, now) in sorted(fallen.items()))
            + ". Write the test the row is asking for rather than lowering it."
        )
    if gone:
        print(f"these modules have a row and were not measured, so they were deleted or a leg failed: {gone}")
    if added:
        print(f"these modules have no row yet: {added}. " + STALE.format(path=RECORD.relative_to(ROOT).as_posix()))
    if total < floor or fallen or gone or added:
        return 1
    print(f"coverage is {total} percent against a floor of {floor}, and no module fell.")
    return 0


def write() -> int:
    """Raise every row the run has beaten, and refuse to lower one, which is the ratchet."""
    committed = Record.read() if RECORD.exists() else Record.empty()
    total, rows = percentages()
    modules = committed.modules
    silent = silent_suites(rows, modules)
    if silent:
        # Writing from a short run would raise a row off a partial import and leave a floor no full
        # run can reach, so a record that already exists is never written from an incomplete one.
        print(
            f"the {', '.join(silent)} suite covered less than its own floor, so nothing was written. "
            "Run every leg before recording a floor, because a row raised from a short run is a "
            "floor no complete run can reach."
        )
        return 1
    refused = {name: (was, rows[name]) for name, was in modules.items() if name in rows and rows[name] < was}
    kept = {name: max(score, modules.get(name, 0)) for name, score in rows.items()}
    floor = max(total, committed.total)
    RECORD.write_text(rendered(floor, kept, platform.system().lower()), encoding="utf-8")
    print(f"wrote {RECORD.relative_to(ROOT).as_posix()} with a floor of {floor} percent")
    if refused:
        print(
            "these rows were kept where they were, because a row only ever rises: "
            + ", ".join(f"{name} {was} against {now}" for name, (was, now) in sorted(refused.items()))
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if the run measured less than the record")
    parser.add_argument("--write", action="store_true", help="record what the run just measured")
    args = parser.parse_args()
    if args.write:
        return write()
    return check()


if __name__ == "__main__":
    sys.exit(main())
