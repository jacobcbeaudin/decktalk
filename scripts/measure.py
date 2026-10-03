"""Measure DeckTalk on this machine and write docs/data/measured.json, which the numbers page reads.

    uv run python scripts/measure.py                # every measurement, about five minutes
    uv run python scripts/measure.py --repeats 5    # more builds per measurement

Nothing on the measured-numbers page is typed. This script runs the real commands and writes what
they reported, and `scripts/build_measured.py` renders the page and the README's measured block from
that file, so the page can say only what a run said.

It measures three things, each with the command a reader can run to see the same:

- **Landing error.** The starter `decktalk init` writes is built with `--no-spend`, so every section
  plays the placeholder voice, whose words have a real clock. `decktalk verify` then decodes the
  film and reports how far each reveal sits from its word, and the limit is the project's own
  `verify.cue_offset_max_ms`. The placeholder has no voice of its own to misplace a word, so these
  numbers measure the cue, the recorder and the cut, and not a voice's timing.
- **Build times.** A cold build in a fresh project, the same build again with nothing changed, and
  the build after one word of one section changed, each timed as the wall time of the whole
  command. Each build also says how many sections it recorded, which is what the cache saves.
- **Coverage.** Every suite the coverage floor measures, run through the check table, then the
  total `coverage report` prints, read from `coverage json`.

It is not a row of the check table, because it takes minutes and its numbers belong to the machine
it ran on. The file names that machine, the tool versions, the commit and the command, and keeps
every landing row another measurement wrote, so the aligner bench can add its row beside this one.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import check
import check_coverage
from decktalk.results import TakeStatus

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "data" / "measured.json"
SCRIPT = f"uv run python scripts/{Path(__file__).name}"

SCHEMA = 1
"""Truth: the shape version of the data file, which `scripts/build_measured.py` refuses any other of."""

RUN = "measure"
"""The name this script's run goes by in the file, which every row it writes points at."""

REPEATS = 3
"""Calibration: three samples of each build give a median that one slow start cannot move, in a few minutes."""

MS_PER_SECOND = 1000
"""Truth: verify reports seconds and the limit is in milliseconds."""

LIMIT_KEY = "verify.cue_offset_max_ms"
"""The setting verify grades every reveal against, early or late."""

PROJECT = "starter"
"""What `decktalk init --no-input` writes, which is the project the quickstart builds."""

EDIT_SECTION = 3
"""The section whose sentence the one-section build changes, which is the starter's last."""

EDIT = ("Change a word", "Change one word")
"""The one-word change, chosen outside every cue phrase so that no cue moves to another word."""

PERCENT_DECIMALS = 1
"""Calibration: one decimal of a percent, finer than a run moves between machines and coarse enough to read."""

MS_DECIMALS = 1
"""Truth: verify reports whole milliseconds in seconds, and one decimal drops only the float's noise."""

LOAD_DECIMALS = 2
"""Truth: the operating system reports its load average to the hundredth."""

COVERAGE_GROUP = "coverage"
"""The check table's row that combines every suite's data and gates the floor."""

BUILDS = ("cold", "unchanged", "one section changed")
"""The three builds timed, in the order they run in one project."""


def landing(verifies: list[dict[str, Any]], *, limit_ms: float) -> dict[str, Any]:
    """The landing row: how far each reveal sat from its word over every run, early or late alike."""
    offsets: dict[str, list[float]] = {}
    skipped = 0
    for result in verifies:
        for row in result["cues"]:
            if row["skipped"] is not None:
                skipped += 1
                continue
            offsets.setdefault(row["cue"], []).append(round(row["offset_seconds"] * MS_PER_SECOND, MS_DECIMALS))
    distances = [abs(value) for values in offsets.values() for value in values]
    if not distances:
        raise SystemExit("verify measured no reveal in any run, so there is no landing error to publish.")
    return {
        "run": RUN,
        "voice": TakeStatus.PLACEHOLDER.value,
        "limit_ms": limit_ms,
        "reveals": len(distances),
        "skipped": skipped,
        "over_limit": sum(1 for distance in distances if distance > limit_ms),
        "worst_ms": max(distances),
        "median_ms": statistics.median(distances),
        "cues": [{"cue": name, "offsets_ms": values} for name, values in offsets.items()],
    }


def coverage_totals(report: dict[str, Any]) -> dict[str, Any]:
    """The total a `coverage json` report holds, which is the total `coverage report` prints."""
    totals = report["totals"]
    return {
        "percent": round(totals["percent_covered"], PERCENT_DECIMALS),
        "statements": totals["num_statements"],
        "covered": totals["covered_lines"],
    }


def recorded_sections(build: dict[str, Any]) -> int:
    """How many sections a build recorded, which is how many recordings it wrote rather than kept."""
    return sum(1 for path in build["written"] if path.startswith("build/recordings/") and path.endswith(".webm"))


def edited(script: str) -> str:
    """The starter's script with one word of `EDIT_SECTION` changed, refused unless the phrase is there once."""
    before, after = EDIT
    if script.count(before) != 1:
        raise SystemExit(
            f"the script says {before!r} {script.count(before)} times, so the one-word edit has no target."
        )
    heading = re.search(rf"^## {EDIT_SECTION}\.", script, re.MULTILINE)
    following = re.search(rf"^## {EDIT_SECTION + 1}\.", script, re.MULTILINE)
    end = following.start() if following else len(script)
    if heading is None or not heading.start() < script.index(before) < end:
        raise SystemExit(f"the script says {before!r} outside section {EDIT_SECTION}, which is the one the edit names.")
    return script.replace(before, after)


def versions(doctor: dict[str, Any], *, decktalk: str) -> dict[str, str]:
    """The versions a reader compares, from `decktalk --json doctor`, with every path left out."""
    tools = {row["tool"]: row["version"] for row in doctor["tools"]}
    return {
        "decktalk": decktalk.strip().removeprefix("decktalk ").strip(),
        "python": doctor["python"].split(" ")[0],
        "chromium": tools["chromium"],
        "ffmpeg": tools["ffmpeg"],
    }


def machine() -> dict[str, Any]:
    """The machine as a reader would name it: the system, the processor and how many cores."""
    system = platform.system()
    if system == "Darwin":
        name = f"macOS {platform.mac_ver()[0]} {platform.machine()}"
        cpu = text(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    else:
        name = f"{system} {platform.release()} {platform.machine()}"
        cpu = cpu_model() or platform.processor()
    return {"platform": name, "cpu": cpu, "cores": os.cpu_count()}


def cpu_model() -> str | None:
    """The processor's model name on Linux, which `platform` does not read."""
    info = Path("/proc/cpuinfo")
    if not info.exists():
        return None
    for line in info.read_text(encoding="utf-8").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def text(cmd: list[str], *, cwd: Path = ROOT) -> str:
    """One command's output, with a failure raised as the command and what it said."""
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} exited {done.returncode}:\n{done.stdout}{done.stderr}")
    return done.stdout


def decktalk(*args: str, cwd: Path) -> tuple[dict[str, Any], float]:
    """One `decktalk --json` command in a project, its answer and its wall time in seconds.

    An exit of 1 is a finding and still a measurement, so only an answer with an error is refused.
    """
    cmd = [sys.executable, "-m", "decktalk", "--json", *args]
    started = time.monotonic()
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    seconds = time.monotonic() - started
    answer: dict[str, Any] = json.loads(done.stdout)
    if answer.get("error") is not None:
        raise SystemExit(f"decktalk {' '.join(args)} could not run: {answer['error']['message']}")
    if answer.get("stopped_at") is not None:
        raise SystemExit(f"decktalk {' '.join(args)} stopped at {answer['stopped_at']}, so it made no film to time.")
    return answer, seconds


def one_project(scratch: Path) -> tuple[list[dict[str, Any]], dict[str, Any], float, dict[str, Any]]:
    """Build one fresh starter three ways and verify its first film.

    Answers each build's seconds and sections recorded, verify's answer, the limit and the film.
    """
    project = scratch / PROJECT
    text([sys.executable, "-m", "decktalk", "init", str(project), "--no-input"])
    builds: list[dict[str, Any]] = []
    cold, seconds = decktalk("build", "--no-spend", cwd=project)
    builds.append({"build": BUILDS[0], "seconds": seconds, "recorded": recorded_sections(cold)})
    verified, _ = decktalk("verify", cwd=project)
    limit = decktalk("config", "get", LIMIT_KEY, cwd=project)[0]["key"]["value"]
    again, seconds = decktalk("build", "--no-spend", cwd=project)
    builds.append({"build": BUILDS[1], "seconds": seconds, "recorded": recorded_sections(again)})
    script = project / "script.md"
    script.write_text(edited(script.read_text(encoding="utf-8")), encoding="utf-8")
    changed, seconds = decktalk("build", "--no-spend", cwd=project)
    builds.append({"build": BUILDS[2], "seconds": seconds, "recorded": recorded_sections(changed)})
    film = {"film_seconds": verified["film_seconds"], "sections": len(verified["starts"])}
    return builds, verified, limit, film


def coverage() -> dict[str, Any]:
    """Every suite the floor measures, run through the check table, and the total they scored."""
    # The groups run in the table's order, so the coverage row combines after every suite has measured.
    wanted = {group.name for group in check_coverage.suites().values()} | {COVERAGE_GROUP}
    groups = [group.name for group in check.GROUPS if group.name in wanted]
    command = ["uv", "run", "python", "scripts/check.py", "--group", ",".join(groups)]
    print(f"$ {' '.join(command)}", file=sys.stderr)
    if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:
        raise SystemExit("the check table failed, so its coverage is not a measurement of this code.")
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "coverage.json"
        text(["uv", "run", "coverage", "json", "--quiet", "-o", str(report)])
        totals = coverage_totals(json.loads(report.read_text(encoding="utf-8")))
    return {"run": RUN, "command": " ".join(command), **totals, "suites": list(check_coverage.reporting_legs())}


def kept(previous: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The runs and landing rows another measurement wrote, which this one leaves where they are."""
    if previous.get("schema") != SCHEMA:
        return {}, []
    runs = {name: run for name, run in previous["runs"].items() if name != RUN}
    return runs, [row for row in previous["landing"] if row["run"] != RUN]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=REPEATS, help="how many fresh projects to build")
    args = parser.parse_args()
    command = SCRIPT if args.repeats == REPEATS else f"{SCRIPT} --repeats {args.repeats}"

    # Coverage runs first, so no suite shares the machine with a build being timed.
    covered = coverage()
    # Other work on the machine slows a recording and moves its landing, so the load is published beside them.
    load = round(os.getloadavg()[0], LOAD_DECIMALS) if hasattr(os, "getloadavg") else None
    samples: list[list[dict[str, Any]]] = []
    verifies: list[dict[str, Any]] = []
    limits: set[float] = set()
    films: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        print(f"building fresh project {repeat + 1} of {args.repeats}", file=sys.stderr)
        with tempfile.TemporaryDirectory() as scratch:
            builds, verified, limit, film = one_project(Path(scratch))
        samples.append(builds)
        verifies.append(verified)
        limits.add(limit)
        films.append(film)
    if len(limits) != 1 or any(film != films[0] for film in films):
        raise SystemExit(f"the fresh projects disagreed about the limit or the film: {sorted(limits)}, {films}")

    doctor, _ = decktalk("doctor", cwd=ROOT)
    run = {
        "what": "the starter `decktalk init` writes, built with the placeholder voice",
        "command": command,
        "commit": text(["git", "rev-parse", "HEAD"]).strip(),
        "source_changed": bool(text(["git", "status", "--porcelain", "--", "src", "pyproject.toml", "uv.lock"])),
        "machine": machine(),
        "versions": versions(doctor, decktalk=text([sys.executable, "-m", "decktalk", "--version"])),
        "repeats": args.repeats,
        "load": load,
    }
    previous = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}
    other_runs, other_rows = kept(previous)
    document = {
        "schema": SCHEMA,
        "runs": {RUN: run, **other_runs},
        "landing": [landing(verifies, limit_ms=limits.pop()), *other_rows],
        "builds": {
            "run": RUN,
            "project": PROJECT,
            **films[0],
            "edit": {"section": EDIT_SECTION, "from": EDIT[0], "to": EDIT[1]},
            "rows": [
                {
                    "build": name,
                    "seconds": [round(sample[index]["seconds"], 2) for sample in samples],
                    "recorded": [sample[index]["recorded"] for sample in samples],
                }
                for index, name in enumerate(BUILDS)
            ],
        },
        "coverage": covered,
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2))
    print(f"wrote {TARGET.relative_to(ROOT).as_posix()}. Run `uv run scripts/build_measured.py --write` next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
