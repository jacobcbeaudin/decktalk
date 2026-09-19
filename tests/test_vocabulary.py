"""No closed vocabulary is spelled as a string outside the enum that defines it.

A verdict, a skip reason, a stage, a progress event, an error code, a take status, a soundscape
status, a section kind and a substitute are each a member of a plain enum, and a plain enum never
compares equal to a string. So a string literal that spells one of them
is either dead, because it is compared with a member and is always unequal, or it is a second
spelling of the vocabulary that the enum cannot see. Either way it is how a test once compared a JSON
object with a code and passed without testing anything. This walk reads the AST of every Python file
under `src/` and `tests/` and fails on a string literal equal to a code, a label or a value of any of
those enums, wherever the literal stands for the thing itself.

A literal is not the thing itself, and is left alone, when it is:

- in the module that defines the enum, which is the one place each word is written,
- prose, which is a docstring or anything inside an f-string,
- a name `__all__` exports, which is a Python identifier,
- or, for a word in lower case, the name of a JSON key or an attribute, which is a key of a dict
  display, the index of a subscript, the key a mapping method reads or a membership test looks up,
  a word of a tuple or a list whose name ends in `KEYS`, the name `getattr` reads, or the key
  `setitem` sets, and a word of a command line, which is typed as a person types it: an argument of
  a call that runs or declares a command line, or a word of a list or a tuple that holds a flag.

Every code and every label that is a finding is written in capitals, and every JSON key and command
word is in lower case, so only a lower-case word can be a key, and a capitalised code is refused in
every position a key or a command line would take.
"""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path

import pytest

from decktalk.errors import ErrorCode
from decktalk.pipeline import ProgressEvent, SectionKind, SoundscapeStatus, Stage, Substitute, TakeStatus
from decktalk.verdicts import SkipReason, Verdict

ROOT = Path(__file__).resolve().parent.parent

DEFINING = {"src/decktalk/verdicts.py", "src/decktalk/pipeline.py", "src/decktalk/errors.py"}
"""The modules that define the enums, where every word of the vocabulary is written once."""

KEY_READERS = {"get", "pop", "setdefault"}
"""Mapping methods whose first argument is a key. A method named `get_<kind>` reads a key too."""

COMMAND_LINE_RUNNERS = {"main", "parse", "parse_args", "cli", "envelope", "Command"}
"""Calls whose arguments are a command line: the CLI's own entry, the parser, the declaration of one
command, and the e2e runners."""

WORD_ENUMS: tuple[type[Enum], ...] = (
    Stage,
    ProgressEvent,
    ErrorCode,
    TakeStatus,
    SoundscapeStatus,
    SectionKind,
    Substitute,
)
"""The enums whose value is the word itself, beside `Verdict`, which is spelled by its code and its label."""


def vocabulary() -> dict[str, str]:
    """Every word the enums own, and what it spells."""
    words: dict[str, str] = {}
    for verdict in Verdict:
        words[verdict.name] = f"the code of Verdict.{verdict.name}"
        words.setdefault(verdict.label, f"the label of Verdict.{verdict.name}")
    for enum in (SkipReason, *WORD_ENUMS):
        for member in enum:
            words[member.value] = f"{enum.__name__}.{member.name}"
    return words


WORDS = vocabulary()


def _called(node: ast.Call) -> str:
    """The name a call is made by, as `f` for `f(...)` and `m` for `x.m(...)`."""
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _constants(node: ast.AST) -> list[ast.Constant]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _words(nodes: list[ast.expr]) -> list[ast.Constant]:
    """The string constants among these nodes, and among the elements of a list or a tuple among them."""
    found: list[ast.Constant] = []
    for node in nodes:
        items = node.elts if isinstance(node, (ast.List, ast.Tuple)) else [node]
        found += [item for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return found


def exempt(tree: ast.Module) -> set[int]:
    """The ids of every string constant that is prose, an export, a lower-case key or a command word."""
    prose: set[int] = set()
    keys: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            prose.add(id(node.value))  # A docstring, or a bare string standing as one.
        elif isinstance(node, ast.JoinedStr):
            prose.update(id(c) for c in _constants(node))
        elif isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if "__all__" in names:
                prose.update(id(c) for c in _constants(node.value))
            elif any(name.endswith("KEYS") for name in names):
                keys += _words([node.value])
        elif isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            keys += _words([node.left])
        elif isinstance(node, ast.Dict):
            keys += _words([key for key in node.keys if key is not None])
        elif isinstance(node, ast.Subscript):
            keys += _words([node.slice])
        elif isinstance(node, ast.Call):
            name = _called(node)
            if name in COMMAND_LINE_RUNNERS:
                keys += _words([*node.args, *(k.value for k in node.keywords)])
            elif isinstance(node.func, ast.Attribute) and (name in KEY_READERS or name.startswith("get_")):
                keys += _words(node.args[:1])
            elif name in ("getattr", "hasattr", "setattr", "delattr", "setitem", "delitem"):
                keys += _words(node.args[1:2])
        elif isinstance(node, (ast.List, ast.Tuple)):
            words = _words(list(node.elts))
            if any(word.value.startswith("-") and len(word.value) > 1 for word in words):
                keys += words
    return prose | {id(word) for word in keys if word.value == word.value.lower()}


def violations(source: str, where: str) -> list[str]:
    """Every literal in one file that spells the vocabulary where it stands for the thing itself."""
    tree = ast.parse(source)
    skip = exempt(tree)
    found = [
        f"{where}:{node.lineno}: {node.value!r} is {WORDS[node.value]}"
        for node in _constants(tree)
        if node.value in WORDS and id(node) not in skip
    ]
    return sorted(set(found), key=lambda line: (line.split(":")[0], int(line.split(":")[1]), line))


def python_files() -> list[Path]:
    files = [*(ROOT / "src" / "decktalk").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    return sorted(p for p in files if "__pycache__" not in p.parts and "out" not in p.relative_to(ROOT).parts)


def test_no_file_spells_a_word_of_the_vocabulary_as_a_string():
    found: list[str] = []
    for path in python_files():
        where = path.relative_to(ROOT).as_posix()
        if where not in DEFINING:
            found += violations(path.read_text(encoding="utf-8"), where)
    assert not found, "Use the enum member, and `.label` or `.value` where text is printed:\n" + "\n".join(found)


PLANTED = [
    ("prose", '"""record"""\nwhy = f"{stage} is not record"\nif stage == "record": pass', [Stage.RECORD.value]),
    ("export", '__all__ = ["align"]\nSTAGES = ["align"]', [Stage.ALIGN.value]),
    (
        "subscript",
        'closed = row["event"] == "done"\ncount = tally["OFF_CUE"]',
        [ProgressEvent.DONE.value, Verdict.OFF_CUE.name],
    ),
    ("dict key", 'row = {"skipped": 1, "OFF_CUE": 1}', [Verdict.OFF_CUE.name]),
    (
        "mapping read",
        'seen = doc.get("verdict") == "OFF CUE" or doc.get("OFF_CUE")',
        [Verdict.OFF_CUE.label, Verdict.OFF_CUE.name],
    ),
    ("attribute", 'quiet = getattr(args, "quiet")\ncode = getattr(Verdict, "OFF_CUE")', [Verdict.OFF_CUE.name]),
    ("setitem", 'mp.setitem(HANDLERS, "align", f)\nmp.setitem(HANDLERS, "CONFIG", f)', [ErrorCode.CONFIG.value]),
    ("command line", 'main(["verify", "--json"], check=lambda d: d.code == "OFF_CUE")', [Verdict.OFF_CUE.name]),
    ("flag list", 'lines = [("--strict", "record"), ("--strict", "SLATE")]', [Verdict.SLATE.name]),
    ("command", 'Command("clip", "cut a span")\nCommand("OFF_CUE", "judge")', [Verdict.OFF_CUE.name]),
    (
        "membership",
        'both = "clip" in raw and "OFF_CUE" in tally\nmade = "generated" == item.status',
        [Verdict.OFF_CUE.name, SoundscapeStatus.GENERATED.value],
    ),
    (
        "key tuple",
        'WHERE_KEYS = ("where", "page")\nCODE_KEYS = ("SLATE",)\nKINDS = ("page", "clip")',
        [Verdict.SLATE.name, SectionKind.CLIP.value, SectionKind.PAGE.value],
    ),
]
"""Each exemption beside a literal it must still see: (the exemption, a source, the words it must find)."""


@pytest.mark.parametrize(("exemption", "source", "caught"), PLANTED, ids=[case[0] for case in PLANTED])
def test_the_walk_sees_the_literal_beside_each_exemption(exemption, source, caught):
    """The guard is only worth its exemptions if it still sees the literal each of them is not."""
    found = [line.split(": ", 1)[1].split(" is ", 1)[0] for line in violations(source, f"{exemption}.py")]
    assert found == [repr(word) for word in caught]


@pytest.mark.parametrize("word", sorted(WORDS))
def test_every_word_is_spelled_by_exactly_one_member_family(word):
    """A word two enums shared would make a literal ambiguous, so each word belongs to one place."""
    owners = {
        "Verdict": {v.name for v in Verdict} | {v.label for v in Verdict},
        **{enum.__name__: {member.value for member in enum} for enum in (SkipReason, *WORD_ENUMS)},
    }
    assert sum(word in words for words in owners.values()) == 1, word
