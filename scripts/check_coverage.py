"""One coverage floor, measured from a real run and written down rather than typed.

    uv run scripts/check_coverage.py --write    # record what the run just measured
    uv run scripts/check_coverage.py --check    # exit 1 if the run measured less than the record

A number typed into a configuration file is a magic number wherever it sits, and a floor is the one
number in the repository that decides whether a change may land. So the number here is measured:
`--write` reads the combined coverage data and records what a complete run scored, `--check` reads
the same data and gates against that record, and the record only ever rises, which is what makes it
a ratchet rather than a wish.

**The floor is one number over every suite.** A second floor on the unit suite alone would pressure
a contributor to cover `media/frames.py` with mocks, which turns a real gap into a fake proof, and
the whole point of measuring against ffmpeg and a real Chromium is that the proof is real. A floor
per module would do the same thing one module at a time, and it would be a floor measured on one
machine and gated on another, which is a gate that fails on the runner rather than on the change.

**A leg that did not report is a failure rather than a lower floor.** A cancelled job leaves the
combined data short, and without this the floor would quietly drop on exactly the day something
broke. Each suite writes a data file named after itself, the combine keeps those files, and every
one of them has to be there and to have measured something, so a silent leg is named as itself
rather than as whichever module fell first.

**The floor allows a point of margin.** A runner slower than the one the record was measured on
takes a different branch here and there: a timeout that fires, a page that answers before it is
asked. That is a fact about the machine rather than about the change, so the gate is the recorded
measurement less `MARGIN`, and a real regression is far larger than that.

This script carries no `# /// script` header, unlike every other script here, because it reads the
coverage data through the same `coverage` the suite wrote it with. The other two commands of the
`coverage` group run from the project environment for the same reason, so all three agree about what
a run scored.

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

import check
import coverage

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "scripts" / "coverage-floor.json"
DATA_FILE = ".coverage"
"""What `coverage` calls its data file, which every suite writes a file of its own beside."""

MARGIN = 1
"""How many whole points under the recorded measurement a run may score and still pass.

One point is about a hundred statements here, which is more than a runner's own timing costs and far
less than a change that stopped testing something.
"""

STALE = "{path} is out of date. Run: uv run scripts/check_coverage.py --write"
"""The one sentence every generator in this repository fails with, naming the file and the command."""


def reporting_legs() -> tuple[str, ...]:
    """Every data file a group of the check table measures into, named as the suite that writes it.

    The table is read rather than copied here, because a list of suites kept in two places is a list
    that disagrees with itself on the day a suite is added. The three groups that also run on macOS
    and Windows measure into the file their Linux row names, so the names are one per suite.
    """
    named = {
        Path(value).name.removeprefix(f"{DATA_FILE}.")
        for group in check.GROUPS
        for name, value in group.env
        if name == "COVERAGE_FILE"
    }
    return tuple(sorted(named))


def leg_files(leg: str) -> list[Path]:
    """Every data file one suite left behind, on this machine and as the coverage job renames them.

    A local run writes `.coverage.unit`. The coverage job unpacks one artifact per leg and renames
    each file after the leg it came from, so the same suite arrives as `.coverage.unit-<leg>.<n>`,
    once per Python the group runs and once more for every subprocess a suite measured.
    """
    return sorted(ROOT.glob(f"{DATA_FILE}.{leg}")) + sorted(ROOT.glob(f"{DATA_FILE}.{leg}-*"))


def lines_measured(leg: str) -> int:
    """How many lines one suite's own data files really measured, which is none when it never ran."""
    total = 0
    for path in leg_files(leg):
        data = coverage.CoverageData(basename=str(path))
        data.read()
        total += sum(len(data.lines(measured) or ()) for measured in data.measured_files())
    return total


def silent_legs() -> list[str]:
    """Every suite that wrote no data file, or wrote one that measured nothing, named as itself."""
    return [leg for leg in reporting_legs() if not lines_measured(leg)]


def measured_total() -> int:
    """What the combined data scored over the whole package, rounded down.

    `analysis2` is the measurement the text report prints, so the record and the report can never
    disagree about what a run scored.
    """
    data = coverage.Coverage()
    data.load()
    statements = missing = 0
    for measured in sorted(data.get_data().measured_files()):
        _name, lines, _excluded, absent, _formatted = data.analysis2(measured)
        statements += len(lines)
        missing += len(absent)
    return floor_percent(statements - missing, statements)


def floor_percent(covered: int, statements: int) -> int:
    """What a run scored, rounded down, so the record reads as a floor rather than as a measurement."""
    return math.floor(100 * covered / statements) if statements else 0


@dataclass(frozen=True)
class Record:
    """The committed measurement: what a complete run scored, and the platform it scored it on."""

    measured_on: str
    total: int

    @classmethod
    def read(cls) -> Record:
        """The committed file, typed once here so that nothing below it reads an untyped mapping."""
        document: dict[str, Any] = json.loads(RECORD.read_text(encoding="utf-8"))
        return cls(measured_on=str(document["measured_on"]), total=int(document["total"]))

    @classmethod
    def empty(cls) -> Record:
        """What the first write starts from, which is nothing measured nowhere."""
        return cls(measured_on=platform.system().lower(), total=0)

    @property
    def floor(self) -> int:
        """The number a run is held to, which is the measurement less the margin a runner may cost."""
        return self.total - MARGIN


def rendered(total: int, measured_on: str) -> str:
    """The record as the committed file holds it, which is two-space JSON with a trailing newline."""
    document = {
        "_comment": (
            "What a complete run of every measuring suite scored, written by "
            "scripts/check_coverage.py --write and gated by --check, which allows "
            f"{MARGIN} point under it. The number is a whole percent rounded down and only ever "
            "rises. Never lower it to make a run pass: write the test it is asking for."
        ),
        "measured_on": measured_on,
        "total": total,
    }
    return json.dumps(document, indent=2) + "\n"


def roll_call() -> int:
    """Print every suite that did not report, and answer how many there were."""
    silent = silent_legs()
    if silent:
        print(
            f"these suites measured nothing, so their legs never reported: {', '.join(silent)}. "
            "A leg that did not run is a failure rather than a lower floor, so find out which it "
            "was before touching this record."
        )
    return len(silent)


def check_floor() -> int:
    """Gate the measured run against the committed record, naming what fell."""
    if not RECORD.exists():
        print(STALE.format(path=RECORD.relative_to(ROOT).as_posix()))
        return 1
    if roll_call():
        return 1
    committed = Record.read()
    total = measured_total()
    if total < committed.floor:
        print(
            f"coverage is {total} percent and the floor is {committed.floor}, which is the "
            f"{committed.total} percent measured on {committed.measured_on} less {MARGIN} point of "
            "margin. Write the test the change is asking for rather than lowering the record."
        )
        return 1
    print(f"coverage is {total} percent against a floor of {committed.floor}, and every suite reported.")
    return 0


def write() -> int:
    """Record what this run measured, and refuse to lower the record, which is the ratchet."""
    if roll_call():
        print("Nothing was written, because a record written from a short run is a floor no complete run can reach.")
        return 1
    committed = Record.read() if RECORD.exists() else Record.empty()
    total = measured_total()
    if total < committed.total:
        print(
            f"this run scored {total} percent against the {committed.total} on record, so nothing "
            "was written. The record only ever rises: write the test the difference is asking for."
        )
        return 1
    RECORD.write_text(rendered(total, platform.system().lower()), encoding="utf-8")
    print(
        f"wrote {RECORD.relative_to(ROOT).as_posix()} with {total} percent measured, so the floor is {total - MARGIN}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if the run measured less than the record")
    parser.add_argument("--write", action="store_true", help="record what the run just measured")
    args = parser.parse_args()
    if args.write:
        return write()
    return check_floor()


if __name__ == "__main__":
    sys.exit(main())
