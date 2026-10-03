"""What `scripts/measure.py` reads out of the real commands it runs, held on small fixtures.

The script itself builds films and runs every measuring suite, which is slow and depends on the
machine, so it is never in the check table. What it reads from each command's JSON is a pure
function, and that is what these tests hold.
"""

from __future__ import annotations

import pytest

import measure


def cue(name: str, offset: float, *, skipped: str | None = None) -> dict[str, object]:
    """One row of `decktalk --json verify`'s cues, with only the fields the landing reads."""
    return {
        "section": 1,
        "cue": name,
        "spoken": 1.0,
        "shown": 1.0 + offset,
        "offset_seconds": offset,
        "skipped": skipped,
    }


def test_landing_is_the_distance_from_the_word_early_or_late_over_every_run() -> None:
    """The limit is early or late, so an early reveal counts by its size and not by its sign."""
    runs = [
        {"cues": [cue("1.1:title", 0.016), cue("1.1:files", -0.050), cue("1.2:deck", 0.0, skipped="hidden")]},
        {"cues": [cue("1.1:title", 0.020), cue("1.1:files", 0.030), cue("1.2:deck", 0.0, skipped="hidden")]},
    ]
    row = measure.landing(runs, limit_ms=80.0)
    assert row["limit_ms"] == 80.0
    assert row["reveals"] == 4
    assert row["skipped"] == 2
    assert row["worst_ms"] == 50.0
    assert row["median_ms"] == 25.0
    assert row["cues"] == [
        {"cue": "1.1:title", "offsets_ms": [16.0, 20.0]},
        {"cue": "1.1:files", "offsets_ms": [-50.0, 30.0]},
    ]


def test_every_reveal_past_the_limit_is_counted() -> None:
    """A worst landing past the limit is a finding verify raised, and the page says how many there were."""
    runs = [{"cues": [cue("1.1:title", 0.016), cue("1.1:files", -0.050), cue("1.2:deck", 0.081)]}]
    assert measure.landing(runs, limit_ms=40.0)["over_limit"] == 2
    assert measure.landing(runs, limit_ms=81.0)["over_limit"] == 0


def test_a_run_that_measured_no_reveal_is_refused() -> None:
    """A median of nothing would publish a page that says nothing as if it measured something."""
    with pytest.raises(SystemExit, match="no reveal"):
        measure.landing([{"cues": [cue("1.1:title", 0.0, skipped="hidden")]}], limit_ms=80.0)


def test_coverage_is_the_total_the_report_prints() -> None:
    """`coverage json` writes the same totals `coverage report` prints, so the page and the table agree."""
    report = {
        "meta": {"version": "7.10.0"},
        "files": {},
        "totals": {"covered_lines": 11966, "num_statements": 12200, "percent_covered": 98.08196721311475},
    }
    assert measure.coverage_totals(report) == {"percent": 98.1, "statements": 12200, "covered": 11966}


def test_the_sections_a_build_recorded_are_the_recordings_it_wrote() -> None:
    """A kept recording is not written again, so what a build wrote is what it recorded."""
    build = {
        "written": [
            "build/narrate/takes.json",
            "build/recordings/03.webm",
            "build/recordings/03.json",
            "build/sections/03.mp4",
        ]
    }
    assert measure.recorded_sections(build) == 1
    assert measure.recorded_sections({"written": []}) == 0


@pytest.mark.parametrize(
    "script",
    [
        pytest.param("## 3. Close\n\nNo such phrase.\n", id="missing"),
        pytest.param("## 3. Close\n\nChange a word. Change a word.\n", id="twice"),
        pytest.param("## 2. How\n\nChange a word.\n\n## 3. Close\n\nThe end.\n", id="another-section"),
    ],
)
def test_the_edit_is_refused_unless_its_phrase_is_there_once(script: str) -> None:
    """An edit that matched nothing measured an unchanged rebuild and would publish it as a change,
    and an edit in another section would publish the wrong section as the one that changed."""
    with pytest.raises(SystemExit, match="Change a word"):
        measure.edited(script)


def test_the_edit_changes_one_word() -> None:
    script = "## 2. How\n\nIt works.\n\n## 3. Close\n\nChange a word in the script.\n"
    assert measure.edited(script) == script.replace("Change a word", "Change one word")


def test_the_machine_names_its_tools_without_a_path() -> None:
    """A path on the page would name a home directory, and the versions are what a reader compares."""
    doctor = {
        "tools": [
            {"tool": "chromium", "version": "153.0.8010.12", "path": "/home/someone/chrome"},
            {"tool": "ffmpeg", "version": "8.1.2", "path": "/home/someone/ffmpeg"},
            {"tool": "ffprobe", "version": "8.1.2", "path": "/home/someone/ffprobe"},
        ],
        "python": "3.13.1 (/home/someone/.venv/bin/python)",
        "platform": "macOS-26.6.2-arm64-arm-64bit-Mach-O (darwin)",
    }
    versions = measure.versions(doctor, decktalk="decktalk 0.5.0")
    assert versions == {"decktalk": "0.5.0", "python": "3.13.1", "chromium": "153.0.8010.12", "ffmpeg": "8.1.2"}
    assert "someone" not in str(versions)
