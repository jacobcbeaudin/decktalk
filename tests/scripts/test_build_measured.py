"""The measured-numbers page and the README's measured block, rendered from the data file alone.

Every number either comes from `docs/data/measured.json` or is not printed, so these tests render a
small fixture and look for its numbers. The committed file is held by the generator's own `--check`
in the generated row, which renders it, so no test here reads it.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

import build_measured
from decktalk.results import TakeOutcome

RUN: dict[str, Any] = {
    "what": "the starter `decktalk init` writes, built with placeholders",
    "command": "uv run python scripts/measure.py",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "source_changed": False,
    "machine": {"platform": "macOS-26.6.2-arm64", "cpu": "Test CPU", "cores": 8},
    "versions": {"decktalk": "9.9.9", "python": "3.12.4", "chromium": "150.0.1", "ffmpeg": "8.0.0"},
    "repeats": 3,
    "load": 1.25,
}

DATA: dict[str, Any] = {
    "schema": 1,
    "runs": {"measure": RUN},
    "landing": [
        {
            "run": "measure",
            "voice": TakeOutcome.PLACEHOLDER.value,
            "limit_ms": 80.0,
            "reveals": 6,
            "skipped": 0,
            "over_limit": 0,
            "worst_ms": 61.0,
            "median_ms": 33.5,
            "cues": [
                {"cue": "1.1:title", "offsets_ms": [16.0, -20.0, 18.0]},
                {"cue": "1.1:files", "offsets_ms": [35.0, 61.0, 32.0]},
            ],
        }
    ],
    "builds": {
        "run": "measure",
        "project": "starter",
        "film_seconds": 50.4,
        "sections": 3,
        "edit": {"section": 3, "from": "Change a word", "to": "Change one word"},
        "rows": [
            {"build": "cold", "seconds": [27.31, 26.02, 28.4], "recorded": [3, 3, 3]},
            {"build": "unchanged", "seconds": [0.42, 0.39, 0.45], "recorded": [0, 0, 0]},
            {"build": "one section changed", "seconds": [9.1, 8.8, 9.6], "recorded": [1, 1, 1]},
        ],
    },
    "coverage": {
        "run": "measure",
        "command": "uv run python scripts/check.py --group unit,browser,media,e2e,coverage",
        "percent": 98.1,
        "statements": 12200,
        "covered": 11966,
        "suites": ["browser", "e2e", "media", "unit"],
    },
}


def test_the_page_states_every_measured_number_from_the_data() -> None:
    page = build_measured.page(DATA)
    assert page.startswith("---\ntitle: Measured numbers\n")
    for said in ("61 ms", "34 ms", "80 ms", "27.3 s", "0.42 s", "9.1 s", "98.1 percent", "12,200"):
        assert said in page, f"the page does not say {said}"
    assert "1 of 3" in page
    assert "Test CPU" in page and "9.9.9" in page and "0123456789ab" in page
    assert "uv run python scripts/measure.py" in page
    assert "load average of 1.2 when the builds began" in page


def test_the_readme_block_states_the_headline_numbers_and_links_the_page() -> None:
    block = build_measured.readme_block(DATA)
    for said in ("61 ms", "80 ms", "0.42 s", "9.1 s", "98.1 percent"):
        assert said in block, f"the block does not say {said}"
    assert "https://docs.decktalk.ai/reference/measured" in block


def test_a_bench_row_is_a_second_landing_row_and_leaves_the_first_alone() -> None:
    """The aligner bench adds a run and a landing row, and the page grows a row rather than a shape."""
    data = copy.deepcopy(DATA)
    data["runs"]["bench"] = {**RUN, "what": "a voiced take aligned by a local aligner"}
    data["landing"].append({**DATA["landing"][0], "run": "bench", "voice": "aligned", "worst_ms": 44.0})
    page = build_measured.page(data)
    assert "44 ms" in page and "61 ms" in page
    assert build_measured.readme_block(data) == build_measured.readme_block(DATA)


def test_reveals_past_the_limit_are_named_on_the_page_and_in_the_readme() -> None:
    """`scripts/measure.py` counts a reveal past the limit by verify's rule, the limit plus half a frame,
    so the page and the README name that one limit and never the setting alone."""
    data = copy.deepcopy(DATA)
    data["landing"][0] |= {"worst_ms": 103.0, "over_limit": 2}
    page = build_measured.page(data)
    block = build_measured.readme_block(data)
    assert "| 2 |" in page
    assert "2 of 6 reveals landed past it." in block
    assert "alone" not in page
    assert "alone" not in block


def test_a_recount_that_differs_between_repeats_is_shown_as_a_range() -> None:
    data = copy.deepcopy(DATA)
    data["builds"]["rows"][2]["recorded"] = [1, 2, 1]
    assert "1 to 2 of 3" in build_measured.page(data)


def test_data_of_an_unknown_schema_is_refused() -> None:
    with pytest.raises(SystemExit, match="schema"):
        build_measured.page({**DATA, "schema": 2})
