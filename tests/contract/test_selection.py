"""The collection hook in `tests/conftest.py`, run against a suite of its own.

The hook decides what every command in `scripts/check.py` runs, and a hook nobody reads rots, so it
is driven here through `pytester` rather than described. The suite below is the real conftest and
the real marker list, copied into a throwaway project.
"""

from __future__ import annotations

import pytest

from support.paths import TESTS

CONFTEST = (TESTS / "conftest.py").read_text(encoding="utf-8")

INI = """
[pytest]
addopts = --strict-markers
markers =
    browser: needs the headless Chromium that `decktalk install` fetches
    media: needs the ffmpeg that `decktalk install` fetches
    e2e: needs Chromium and ffmpeg, and builds the pipeline fixture in tests/e2e, about a minute
    scaffold: needs Chromium and ffmpeg, and builds every packaged project without a voice
"""

SUITE = """
import pytest

def test_no_tool():
    pass

@pytest.mark.media
def test_needs_ffmpeg():
    pass

@pytest.mark.browser
def test_needs_chromium():
    pass

@pytest.mark.scaffold
def test_builds_every_example():
    pass
"""


@pytest.fixture
def suite(pytester):
    """A throwaway project running the real hook over one test per marker."""
    pytester.makeconftest(CONFTEST)
    pytester.makeini(INI)
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def test_a_bare_run_is_the_tests_that_need_no_tool(suite):
    suite.runpytest().assert_outcomes(passed=1, deselected=3)


def test_a_suite_is_reached_by_naming_its_marker(suite):
    suite.runpytest("-m", "media").assert_outcomes(passed=1, deselected=3)


def test_naming_one_marker_never_admits_another(suite):
    """`-m "not e2e"` used to collect every other suite, including the five-minute scaffold build."""
    suite.runpytest("-m", "not e2e").assert_outcomes(passed=1, deselected=3)


def test_an_empty_selection_is_an_error_naming_the_markers(suite):
    result = suite.runpytest("-m", "browser", "-k", "nothing_matches_this")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*no test was selected*browser, media, e2e, scaffold*"])
