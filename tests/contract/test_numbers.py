"""No magic numbers: every number that decides something is a setting, an expression, or a named fact.

    uv run python tests/contract/test_numbers.py --write   # lower a count the code has shrunk

A number reaches the code through three doors and there is no fourth. It becomes a settings key when
a project would turn it. It is derived when it is computable, and then it is written as the
expression and never as the value. Otherwise it is bound to a module-level upper-case name in the
module that owns it, followed by one sentence opening with truth, derived or calibration. A name
with no sentence is half a door and does not admit its number. No
suppression comment is honoured, because the next reader reads a name and never reads a silencer,
and the third door costs one line so nobody has to fight it.

The rule ships with a committed per-file baseline, because it does not pass today and a rule that
fails on day one is suppressed on day two. A file that is not on the baseline may hold no bare
literal at all, and a file that is on it may never hold more than the count beside it. The baseline
is a to-do list that only ever shrinks, and `--write` can lower a count but never raise one.

The runtime's TypeScript is walked too. A frame-gap limit is capped dead by a literal in the bundle
that no walk of Python can see, which is exactly the class of number this rule exists to find.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# `--write` is run as a plain script, where pytest's own `pythonpath` is not in force yet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decktalk.settings import KEYS
from support import ratchet
from support.paths import REPO, SRC

BASELINE = Path(__file__).resolve().parent / "numbers-baseline.json"
"""The committed per-file count of literals still waiting for a door, which only ever shrinks."""

WALKED = ("stages", "media", "captions", "pagescan.py")
"""Where the rule holds today, which is every module that measures a film or decides about one."""

RUNTIME = Path("src") / "decktalk" / "runtime" / "src"
"""The TypeScript the recorder loads, walked for the same rule by a scan of its text."""

FREE = (0, 1, 2)
"""Truth: the literals that carry no judgement, which are an empty count, a single thing and a pair."""

COUNTING = ("range", "enumerate")
"""The two calls whose arguments are a count and never a measurement."""

NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")
"""One numeric literal in TypeScript, with a leading name or dot excluded so a member is not one."""

DECLARED = re.compile(r"^\s*(?:export\s+)?const\s+[A-Z][A-Z0-9_]*\s*(?::[^=]+)?=")
"""A module-level upper-case binding in TypeScript, which is the third door spelled in that language."""

SETTINGS_DEFAULTS = {key.name: key.default for key in KEYS}
"""Every settings key by its own name, so a default argument that repeats one can be caught."""


@dataclass(frozen=True)
class Bare:
    """One literal that went through no door, with the line a reader opens to give it one."""

    file: str
    line: int
    value: str


def _name(path: Path) -> str:
    """One file as the baseline names it, which is its repository path and nothing about this machine."""
    return path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.name


def walked() -> list[Path]:
    """Every Python file the rule holds over, in a stable order."""
    out: list[Path] = []
    for name in WALKED:
        here = SRC / name
        out += sorted(here.rglob("*.py")) if here.is_dir() else [here]
    return out


def bare_python(path: Path) -> list[Bare]:
    """Every numeric literal in one module that no door admits, and every repeated settings default."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {id(node) for node in _admitted(tree)}
    name = _name(path)
    out = [
        Bare(name, node.lineno, repr(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value not in FREE
        and id(node) not in allowed
    ]
    return out + _repeated_defaults(tree, name)


def _repeated_defaults(tree: ast.Module, name: str) -> list[Bare]:
    """Every default argument that repeats a settings default, which fails through every door.

    A key's default written a second time in a signature is a value that stops moving when the key
    moves, and no door admits it, because the number is already a setting and the signature is a copy
    of it rather than a home for it.
    """
    out: list[Bare] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        taken = node.args.posonlyargs + node.args.args
        for argument, default in zip(
            taken[len(taken) - len(node.args.defaults) :] + node.args.kwonlyargs,
            list(node.args.defaults) + list(node.args.kw_defaults),
            strict=False,
        ):
            if (
                isinstance(default, ast.Constant)
                and argument.arg in SETTINGS_DEFAULTS
                and default.value == SETTINGS_DEFAULTS[argument.arg]
            ):
                out.append(Bare(name, default.lineno, f"{node.name}({argument.arg}={default.value!r})"))
    return out


def _admitted(tree: ast.Module) -> Iterator[ast.Constant]:
    """Every literal a door already admits, which is what the count subtracts."""
    for node in ast.walk(tree):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            # Truth: minus one is the last element and the one index that carries no judgement.
            if isinstance(node.operand, ast.Constant) and node.operand.value == 1:
                yield node.operand
        elif isinstance(node, ast.Subscript):
            yield from _literals(node.slice)
        elif isinstance(node, ast.Call):
            yield from _from_call(node)
    for node, following in zip(tree.body, [*tree.body[1:], None], strict=True):
        if (door := _door(node, following)) is not None:
            yield from _literals(door)


def _from_call(node: ast.Call) -> Iterator[ast.Constant]:
    """The arguments of the three calls whose numbers are a count or a precision rather than a measurement."""
    if not isinstance(node.func, ast.Name):
        return
    if node.func.id in COUNTING:
        for argument in node.args:
            yield from _literals(argument)
    elif node.func.id == "round" and len(node.args) > 1:
        yield from _literals(node.args[1])


def _door(node: ast.stmt, following: ast.stmt | None) -> ast.expr | None:
    """The value a module-level statement names when it is the third door, a name and its sentence together.

    A name with no sentence is half a door. The next reader meets the number and still has to work
    out whether it follows from a standard, from arithmetic, or from a measurement of a tool, which
    is the whole question the rule exists to answer once.
    """
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign):
        targets, value = [node.target], node.value
    else:
        return None
    if value is None or not all(_named(target) for target in targets):
        return None
    sentence = _sentence(following) if following is not None else None
    return value if sentence is not None and sentence.split(":")[0].strip().lower() in OPENERS else None


OPENERS = ("truth", "derived", "calibration")
"""The three words a constant's sentence opens with, which are the three reasons a number is fixed."""


def _named(target: ast.expr) -> bool:
    """Whether an assignment target is a module-level upper-case name, which is the third door."""
    return isinstance(target, ast.Name) and target.id.isupper()


def _literals(node: ast.expr) -> Iterator[ast.Constant]:
    """Every literal inside one expression, so a constant written as a tuple goes through one door."""
    for found in ast.walk(node):
        if isinstance(found, ast.Constant):
            yield found


def bare_typescript(path: Path) -> list[Bare]:
    """Every numeric literal in one TypeScript module that no door admits.

    The scan reads the text rather than a syntax tree, because the language has no parser here and
    the rule is about a number a reader meets on a line. It costs a false positive on a number
    inside a string, which is why a string of digits is spelled as a name in these modules anyway.
    """
    name = _name(path)
    out: list[Bare] = []
    depth = 0
    binding = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        code = line.split("//")[0]
        if not binding and DECLARED.match(code):
            binding = True
        if binding:
            # A binding's whole initialiser is the one door, so a registry written as an object
            # literal goes through it once rather than once per number inside it.
            depth += code.count("{") + code.count("[") - code.count("}") - code.count("]")
            binding = depth > 0
            continue
        out += [Bare(name, number, found) for found in NUMBER.findall(code) if float(found) not in FREE or "." in found]
    return out


def measured() -> dict[str, int]:
    """The count of bare literals in every file that still has one, which is what the baseline holds."""
    found: dict[str, int] = {}
    for path in walked():
        bare = bare_python(path)
        if bare:
            found[bare[0].file] = len(bare)
    for path in sorted((REPO / RUNTIME).rglob("*.ts")) if (REPO / RUNTIME).is_dir() else []:
        bare = bare_typescript(path)
        if bare:
            found[bare[0].file] = len(bare)
    return found


SOURCE = '''
LIMIT_MS = 42
"""Truth: a fact about a codec, which is what a name and a sentence together admit."""

LOOSE = 43
"""It has a sentence and the sentence opens with nothing, so the door is only half there."""

def measure(frames, sample_rate=48000):
    head = frames[3:7]
    later = [f for f in range(0, 9)]
    scaled = round(head[0] * 1.5, 3)
    return scaled + later[-1] + 0.25
'''
"""One module holding every door and every failure, so each clause is shown rather than assumed."""


def test_each_door_admits_its_own_number_and_nothing_else(tmp_path: Path) -> None:
    """The rule is only useful if a reader can see exactly which numbers it lets through."""
    path = tmp_path / "sample.py"
    path.write_text(SOURCE, encoding="utf-8")
    found = {bare.value for bare in bare_python(path)}
    assert "1.5" in found, "a bare multiplier goes through no door"
    assert "0.25" in found, "a bare measurement goes through no door"
    assert "43" in found, "a name whose sentence opens with nothing is half a door"
    assert "measure(sample_rate=48000)" in found, "a default that repeats a settings default fails"
    assert "42" not in found, "a name with its own sentence is the third door"
    assert "3" not in found and "7" not in found, "a slice bound carries no judgement"
    assert "9" not in found, "a range argument is a count"


TYPESCRIPT = """
export const SPANS: Record<string, number> = {
  quick: 0.12,
  slow: 0.48,
};

export function late(ms: number): boolean {
  return ms > 100;
}
"""
"""One TypeScript module whose registry goes through the binding door and whose cap does not."""


def test_the_typescript_scan_finds_the_cap_a_python_walk_cannot_see(tmp_path: Path) -> None:
    """The limit capped dead inside the bundle is the number this half of the rule exists for."""
    path = tmp_path / "sample.ts"
    path.write_text(TYPESCRIPT, encoding="utf-8")
    assert [bare.value for bare in bare_typescript(path)] == ["100"]


def test_the_baseline_only_shrinks() -> None:
    """A file off the list holds no bare number, a file on it never grows, and a beaten count is written down.

    One walk of the tree answers all three, so the message names every kind of drift at once.
    """
    added, grown, stale = ratchet.drift(measured(), ratchet.baseline(BASELINE))
    assert not (added or grown or stale), (
        f"These files hold a number that went through no door: {added}. Make it a settings key, write "
        "it as the expression it is, or bind it to a module-level upper-case name with one sentence "
        f"opening with truth, derived or calibration. These files grew a number: {grown}. These files "
        f"hold fewer numbers than the baseline excuses: {stale}. Run "
        "`uv run python tests/contract/test_numbers.py --write` to lower a beaten count."
    )


def _sentence(node: ast.stmt) -> str | None:
    """The docstring that follows a binding, or null when the binding has none."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return node.value.value
    return None


if __name__ == "__main__":
    sys.exit(
        ratchet.write(
            BASELINE, measured(), noun="numbers", refusal="these files are not excused and must go through a door"
        )
    )
