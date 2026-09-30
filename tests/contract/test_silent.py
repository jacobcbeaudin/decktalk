"""No failure passes without a trace: every except block says why it is quiet, and every child is traced.

The failure table in `tests/decktalk/test_logs.py` holds each failure path to the record it leaves,
and it can only name the paths that exist today. This walk is what keeps it complete as the code
grows. It reads the AST of every module under `src/decktalk` and fails on two things.

An `except` block is quiet when it neither raises, logs, says a sentence through a run, nor uses
the exception it caught. A quiet block is allowed only with a tag that says why, either the
`# noqa: BLE001  (reason)` the package already writes beside a broad catch, or a `# silent: reason`
comment on the `except` line or the first line inside it.

A child process is started in exactly three places, each of which traces its command, its exit
code, its time and its output: `ffmpeg._spawn`, `chromium_fetch.fetch_chromium` and
`machine._run_command`. A call that starts one anywhere else fails here.
"""

from __future__ import annotations

import ast
import re

from support.paths import SRC

TAG = re.compile(r"#\s*(?:silent:\s*\S|noqa: BLE001\s+\()")
"""The two spellings of a reason a quiet except block gives, which a reader can grep for."""

LOUD_OBJECTS = frozenset({"log", "logging"})
"""The names a call is made on to write a record, which is the module logger or the module itself."""

TRACED = frozenset(
    {("media/ffmpeg.py", "_spawn"), ("toolchain/chromium_fetch.py", "fetch_chromium"), ("machine.py", "_run_command")}
)
"""The three functions that start a child process, each of which traces what it started."""

STARTS = {"subprocess": {"run", "Popen", "call", "check_call", "check_output"}, "os": {"system", "popen"}}
"""Every call that starts a child process, by the module it is called on."""


def modules() -> list[tuple[str, str]]:
    """Every module of the package, as its text and its path under the package."""
    return [(path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()) for path in sorted(SRC.rglob("*.py"))]


def _tagged(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """Whether the except line, or the first line inside the block, gives a reason for being quiet."""
    first = handler.body[0].lineno if handler.body else handler.lineno
    return any(TAG.search(lines[number - 1]) for number in range(handler.lineno, first + 1))


def _loud(handler: ast.ExceptHandler) -> bool:
    """Whether the block raises, logs, says a sentence through a run, or carries the exception on."""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called = node.func
            if isinstance(called.value, ast.Name) and called.value.id in LOUD_OBJECTS:
                return True
            if called.attr == "note":
                return True
        if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
            return True
    return False


def quiet_blocks(text: str, where: str) -> list[str]:
    """Every except block in one module that is quiet and gives no reason for it."""
    lines = text.splitlines()
    return [
        f"{where}:{node.lineno}: {lines[node.lineno - 1].strip()}"
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ExceptHandler) and not _loud(node) and not _tagged(node, lines)
    ]


def untraced_starts(text: str, where: str) -> list[str]:
    """Every call in one module that starts a child process outside the three traced functions."""
    found: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else function
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                called = child.func
                owner = called.value.id if isinstance(called.value, ast.Name) else None
                if owner in STARTS and called.attr in STARTS[owner] and (where, function) not in TRACED:
                    found.append(f"{where}:{child.lineno}: {owner}.{called.attr} in {function or 'the module'}")
            visit(child, inside)

    visit(ast.parse(text), "")
    return found


def test_every_quiet_except_block_says_why_it_is_quiet() -> None:
    quiet = [line for text, where in modules() for line in quiet_blocks(text, where)]
    assert not quiet, (
        "These except blocks leave no record and give no reason. Log the failure, raise it, or add "
        "`# silent: <reason>` on the except line:\n" + "\n".join(quiet)
    )


def test_every_child_process_is_started_by_a_traced_runner() -> None:
    started = [line for text, where in modules() for line in untraced_starts(text, where)]
    assert not started, (
        "These calls start a child process that nothing traces. Start it through ffmpeg._spawn, or trace "
        "it with toolchain.traced and add its function to TRACED:\n" + "\n".join(started)
    )


PLANTED = """import subprocess


def quiet():
    try:
        return int("x")
    except ValueError:
        return 0


def starts():
    subprocess.run(["true"], check=False)
"""
"""A module with one of each thing the guard exists to find."""


def test_the_guard_finds_a_quiet_block_and_an_untraced_child() -> None:
    """The walk is only worth its name if it finds what it looks for, so it is pointed at one of each."""
    assert quiet_blocks(PLANTED, "planted.py") == ["planted.py:7: except ValueError:"]
    assert untraced_starts(PLANTED, "planted.py") == ["planted.py:12: subprocess.run in starts"]
    tagged = PLANTED.replace("except ValueError:\n", "except ValueError:\n        # silent: not a number.\n")
    assert quiet_blocks(tagged, "planted.py") == []
    assert untraced_starts(PLANTED, "machine.py") == ["machine.py:12: subprocess.run in starts"]
    traced = PLANTED.replace("def starts", "def _run_command")
    assert untraced_starts(traced, "machine.py") == []
