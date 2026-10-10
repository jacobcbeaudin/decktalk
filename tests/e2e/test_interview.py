"""The interview example, a narrator and a guest in dialogue, builds on placeholders and says who reads what.

    uv run pytest -m e2e tests/e2e/test_interview.py

The interview is the one packaged project whose section reads in two voices, and both of its voices
ship with an empty id, so a fresh copy built without spending plays one placeholder per turn and
names, for each turn, the id that would let it be bought. This runs on every pull request, because
the turn-by-turn join, the per-turn findings and the reports that name each voice are what a
one-voice project never exercises. Nothing here needs a key, a network or a voice.

The command line is driven as a real subprocess of `python -m decktalk`, the way an author runs it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from decktalk.findings import Code
from support.commands import HOSTILE_DIRECTORY, clean_environ, codes, flat
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, assert_build_finished, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

EXAMPLE = "interview"
"""The packaged project this file builds."""

DIALOGUE = ("narrator", "guest", "narrator", "guest")
"""The voices of the interview's second section, turn by turn."""


@dataclass(frozen=True)
class Built:
    """One fresh interview built without spending, and what the build reported."""

    root: Path
    home: Path
    report: dict[str, Any]


def decktalk(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One `decktalk` command with no key, no voice variable and no machine file in reach."""
    return subprocess.run(
        [sys.executable, "-m", "decktalk", *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd),
        cwd=cwd,
    )


def answer(built: Built, *args: str) -> dict[str, Any]:
    """The one object a read-only command printed under `--json` for the built interview."""
    ran = decktalk("--project", str(built.root), *args, "--json", cwd=built.home)
    return flat(ran.stdout, args)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, pytestconfig: pytest.Config) -> Built:
    """`init --example interview`, then `build --no-spend`, once for every test in this file."""
    home = tmp_path_factory.mktemp("interview") / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = home / EXAMPLE
    made = decktalk("init", str(root), "--example", EXAMPLE, "--json", cwd=home)
    assert made.returncode == 0, made.stderr
    assert flat(made.stdout)["voices"] == ["narrator", "guest"]
    ran = decktalk("--project", str(root), "build", "--no-spend", "--json", cwd=home)
    report = flat(ran.stdout)
    assert_build_finished(ran.returncode, codes(report), ran.stderr, pytestconfig)
    return Built(root=root, home=home, report=report)


def test_the_interview_builds_with_no_spend_one_placeholder_per_turn(built: Built) -> None:
    film = built.root / built.report["film"]
    assert film.is_file() and film.stat().st_size > 0
    rows = json.loads((built.root / "build" / "narrate" / "takes.json").read_text(encoding="utf-8"))["sections"]
    assert [(row["section"], row["turn"]) for row in rows] == [(1, 1), (2, 1), (2, 2), (2, 3), (2, 4), (3, 1)]
    assert tuple(row["voice"] for row in rows if row["section"] == 2) == DIALOGUE
    assert not any(row["voiced"] for row in rows)
    missing = [row["message"] for row in built.report["findings"] if row["code"] == Code.TAKE_MISSING.value]
    assert len(missing) == len(rows)
    for voice in ("narrator", "guest"):
        assert any(
            f"which {voice} reads" in message and f"Name the voice {voice} in [voices.{voice}] id" in message
            for message in missing
        ), voice


def test_the_interview_says_who_reads_what(built: Built) -> None:
    status = answer(built, "status")
    assert [(voice["name"], voice["default"], voice["named"]) for voice in status["voices"]] == [
        ("narrator", True, False),
        ("guest", False, False),
    ]
    second = next(section for section in status["sections"] if section["section"] == 2)
    assert second["voices"] == ["narrator", "guest"]
    assert tuple(turn["voice"] for turn in second["turns"]) == DIALOGUE

    words = answer(built, "words", "--section", "2")
    (section,) = words["sections"]
    assert tuple(turn["voice"] for turn in section["turns"]) == DIALOGUE
    assert sum(turn["word_count"] for turn in section["turns"]) == len(section["words"])

    checked = answer(built, "check", "--no-pages")
    voice_codes = {Code.SCRIPT_VOICE_UNKNOWN, Code.SCRIPT_VOICE_UNMARKED, Code.SCRIPT_VOICE_BARE, Code.VOICE_UNUSED}
    assert not voice_codes & set(codes(checked)), checked["findings"]
