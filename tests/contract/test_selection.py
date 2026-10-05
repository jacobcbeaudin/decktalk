"""The collection hook, the network guard and the Hypothesis profile in `tests/conftest.py`, run on a suite of its own.

The hook decides what every command in `scripts/check.py` runs, and a hook nobody reads rots, so it
is driven here through `pytester` rather than described. The suite below is the real conftest and
the real marker list, copied into a throwaway project.
"""

from __future__ import annotations

import socket
import tomllib

import pytest

from support.paths import TESTS

CONFTEST = (TESTS / "conftest.py").read_text(encoding="utf-8")

PROJECT = tomllib.loads((TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))
REGISTERED = PROJECT["tool"]["pytest"]["ini_options"]["markers"]
"""The marker list the suite really runs under, read rather than copied so this file cannot rot."""

INI = "[pytest]\naddopts = --strict-markers\nmarkers =\n" + "".join(f"    {row}\n" for row in REGISTERED)

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

@pytest.mark.platform
def test_asks_this_machine():
    pass
"""


DRAWN = "from hypothesis import given, strategies\n\n@given(strategies.text())\ndef test_drawn(text):\n    pass\n"
"""A property test over text, which makes Hypothesis build and cache its unicode tables."""

WHEEL = "def test_reads_the_built_wheel():\n    pass\n"
"""A test at the wheel test's own path, which carries no marker and is marked by where it is."""


@pytest.fixture
def suite(pytester):
    """A throwaway project running the real hook over one test per marker, the wheel's at its own path."""
    pytester.makeconftest(CONFTEST)
    pytester.makeini(INI)
    pytester.makepyfile(test_suite=SUITE)
    pytester.makepyfile(**{"contract/test_wheel": WHEEL})
    return pytester


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="a bare run is the tests that need no tool"),
        pytest.param(("-m", "media"), id="a suite is reached by naming its marker"),
        # A bare run on a fresh machine leaves the platform suite out, because it needs a tool nobody has fetched.
        pytest.param(("-m", "platform"), id="the platform suite is reached the same way"),
        # `-m "not e2e"` collects no other marked suite, so it never starts the five-minute scaffold build.
        pytest.param(("-m", "not e2e"), id="naming one marker never admits another"),
    ],
)
def test_each_selection_runs_exactly_one_test(suite, args):
    suite.runpytest(*args).assert_outcomes(passed=1, deselected=5)


def test_the_wheel_test_is_marked_by_its_path_and_runs_only_when_named(suite):
    """It reads the wheel `uv build` wrote into `dist/`, which a fresh clone does not have."""
    assert "test_reads_the_built_wheel" not in suite.runpytest("-v").stdout.str()
    named = suite.runpytest("-v", "-m", "wheel")
    named.assert_outcomes(passed=1, deselected=5)
    named.stdout.fnmatch_lines(["*contract/test_wheel.py::test_reads_the_built_wheel PASSED*"])


def test_an_empty_selection_is_an_error_naming_the_markers(suite):
    result = suite.runpytest("-m", "browser", "-k", "nothing_matches_this")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*no test was selected*browser, media, e2e, scaffold, platform, wheel*"])


def test_a_property_test_writes_under_the_suite_output_and_never_in_the_directory_it_runs_in(suite, monkeypatch):
    """Hypothesis keeps its unicode tables and constants in a folder of its own, which defaults to the
    working directory. The conftest points it at `tests/out/`, which git already ignores."""
    suite.makepyfile(test_drawn=DRAWN)
    monkeypatch.setenv("PYTHONPATH", str(TESTS))
    suite.runpytest_subprocess("test_drawn.py").assert_outcomes(passed=1)
    assert not (suite.path / ".hypothesis").exists()
    assert (suite.path / "out" / "hypothesis").is_dir()


REACH = "import socket\n\ndef test_reaches_out():\n    socket.getaddrinfo('decktalk.invalid', 443)\n"
"""A test that resolves a host other than this machine, which is the first step of sending it anything."""

PLACES = ("contract", "scripts", "support", "e2e", "platform", "decktalk")
"""Every directory under `tests/`, since the guard is the root conftest's and no directory is outside it."""


@pytest.mark.parametrize(
    ("place", "args"),
    [
        *[pytest.param(place, (), id=f"an unmarked test under {place}") for place in PLACES],
        *[pytest.param("e2e", ("-m", name), id=f"a test the {name} suite runs") for name in ("e2e", "platform")],
    ],
)
def test_a_test_anywhere_that_resolves_another_host_fails_before_it_is_asked(suite, monkeypatch, place, args):
    """The guard wraps whatever resolver is in place, so a recorder stands in for DNS and nothing is sent."""
    asked: list[object] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *_args, **_kwargs: asked.append(host) or [])
    marker = f"@pytest.mark.{args[1]}\n" if args else ""
    suite.makepyfile(**{f"{place}/test_reach": "import pytest\n" + REACH.replace("def ", marker + "def ", 1)})
    result = suite.runpytest(f"{place}/test_reach.py", *args)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*tried to reach 'decktalk.invalid'*"])
    assert asked == []


RAW = {
    "an IPv4 literal": ("socket.AF_INET", "connect(('192.0.2.1', 443))"),
    "an IPv6 literal": ("socket.AF_INET6", "connect(('2001:db8::1', 443, 0, 0))"),
    "a connect that answers with a number": ("socket.AF_INET", "connect_ex(('192.0.2.1', 443))"),
    "a datagram": ("socket.AF_INET, socket.SOCK_DGRAM", "sendto(b'x', ('192.0.2.1', 53))"),
    "a name the socket resolves itself": ("socket.AF_INET", "connect(('decktalk.invalid', 443))"),
}
"""Each way a test can open or address a socket without asking `socket.getaddrinfo` first."""

OPENS = ("connect", "connect_ex", "sendto")
"""The socket methods that take an address to reach, which a recorder stands in for below."""


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every address a socket was asked to reach, recorded in place of reaching it."""
    asked: list[object] = []
    for name in OPENS:
        monkeypatch.setattr(socket.socket, name, lambda _self, *args: asked.append(args[-1]) or 0)
    return asked


def raw(kind: str, call: str) -> str:
    """A test that opens a socket of `kind` and makes `call` on it."""
    return f"import socket\n\ndef test_raw():\n    with socket.socket({kind}) as raw:\n        raw.{call}\n"


@pytest.mark.parametrize(("kind", "call"), RAW.values(), ids=RAW.keys())
def test_a_socket_addressed_to_another_host_fails_before_it_is_opened(suite, opened, kind, call):
    suite.makepyfile(test_raw=raw(kind, call))
    result = suite.runpytest("test_raw.py")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*tried to reach*which is not this machine*"])
    assert opened == []


def test_a_socket_addressed_to_this_machine_is_opened(suite, opened):
    suite.makepyfile(test_raw=raw("socket.AF_INET", "connect(('127.0.0.1', 9))"))
    suite.runpytest("test_raw.py").assert_outcomes(passed=1)
    assert opened == [("127.0.0.1", 9)]
