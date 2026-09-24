"""No closed vocabulary is spelled as a string outside the enum that defines it.

    uv run python tests/contract/test_vocabulary.py --write   # lower a count the code has shrunk

A stage, an outcome, a finding code, an error code, a certainty, a layer, a scope, a take status, a
sound status, a section kind, a skip reason, a substitute, a voicing, a spend state, a setting's
nature and a setting's source are each a member of a plain enum, and a plain enum never compares
equal to a string. So a string literal that spells one of them is either
dead, because it is compared with a member and is always unequal, or it is a second spelling of the
vocabulary that the enum cannot see. Either way it is how a test once compared a JSON object with a
code and passed without testing anything. This walk reads the AST of every Python file under `src/`
and `tests/` and fails on a string literal equal to a member of any of them, wherever the literal
stands for the thing itself.

A literal is not the thing itself, and is left alone, when it is:

- in the module that defines the enum, or in the mirrored test that holds its frozen list, which
  between them are the one place each word is written,
- prose, which is a docstring or anything inside an f-string,
- a name `__all__` exports, which is a Python identifier,
- or, for a word in lower case, the name of a JSON key or an attribute, which is a key of a dict
  display, the index of a subscript, the key a mapping method reads or a membership test looks up,
  a word of a tuple or a list whose name ends in `KEYS`, the name `getattr` reads, or the key
  `setitem` sets, and a word of a command line, which is typed as a person types it: an argument of
  a call that runs or declares a command line, or a word of a list or a tuple that holds a flag.

Every finding code and every error code is written in capitals, and every JSON value and command
word is in lower case, so only a lower-case word can be a key, and a capitalised code is refused in
every position a key or a command line would take.

The rule ships with a committed per-file baseline, for the same reason the numbers rule does: it
does not pass today and a rule that fails on day one is suppressed on day two. A file that is not on
the baseline may spell no word at all, a file on it may never spell more than the count beside it,
and `--write` can lower a count and never raise one.

`ALSO_NAMES` is the other half of the rule, and it is the founder's one-word design rather than a
suppression. One word names the stage, its module, its `decktalk.toml` table, its directory under
`build/` and its event, so a literal spelling one of those names the table or the path and not the
member. Each such word carries the sentence saying what else it names, and a test holds the list to
words an enum really owns.
"""

from __future__ import annotations

import ast
import json
import sys
from enum import Enum
from pathlib import Path

import pytest

# `--write` is run as a plain script, where pytest's own `pythonpath` is not in force yet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decktalk.errors import ErrorCode
from decktalk.findings import Applicability, Certainty, Code, RaisedBy
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    Layer,
    Scope,
    SectionKind,
    SkipReason,
    SoundKind,
    SoundStatus,
    SpendState,
    Substitute,
    TakeStatus,
    Voicing,
)
from decktalk.tomlmap import Nature, Source
from support.paths import REPO

ROOT = REPO

BASELINE = Path(__file__).resolve().parent / "vocabulary-baseline.json"
"""The committed per-file count of literals still spelling a closed vocabulary, which only ever shrinks."""

DEFINING = {
    "src/decktalk/pipeline.py",
    "src/decktalk/findings.py",
    "src/decktalk/errors.py",
    "src/decktalk/page.py",
    "src/decktalk/results.py",
    "src/decktalk/events.py",
    "src/decktalk/tomlmap.py",
    "tests/decktalk/test_pipeline.py",
    "tests/decktalk/test_findings.py",
    "tests/decktalk/test_errors.py",
    "tests/decktalk/test_page.py",
    "tests/decktalk/test_results.py",
    "tests/decktalk/test_events.py",
    "tests/decktalk/test_tomlmap.py",
}
"""The modules that define the enums, where every word of the vocabulary is written once.

`page.py` is here although no enum below is declared in it, because it publishes the page's half of
the finding codes as `PageWarning` and its own closed word sets, so it spells the same words by
being the contract. The mirrored test of each of these modules is here for the same reason: holding
the frozen list is its whole subject, so it writes every word out on purpose.
"""

KEY_READERS = {"get", "pop", "setdefault"}
"""Mapping methods whose first argument is a key. A method named `get_<kind>` reads a key too."""

COMMAND_LINE_RUNNERS = {"main", "parse", "parse_args", "cli", "command", "Command", "invoke", "run_cli"}
"""Calls whose arguments are a command line: the CLI's own entry, the parser, the declaration of one
command, and the end to end runners."""

WORD_ENUMS: tuple[type[Enum], ...] = (
    Stage,
    Outcome,
    Certainty,
    RaisedBy,
    Applicability,
    Layer,
    Scope,
    SectionKind,
    SkipReason,
    SoundKind,
    SoundStatus,
    SpendState,
    Substitute,
    TakeStatus,
    Voicing,
    Nature,
    Source,
)
"""Every enum whose value is a word that travels in the JSON, beside the two enums of codes.

`events.Level` and `events.Unit` are deliberately absent. Every member of each is an ordinary noun
the payload also uses as a key, such as the `error` a result carries and the `section` a location
names, so a literal spelling one is no evidence of a second spelling. Both are held instead by the
event assertions in `tests/contract/test_results.py`.
"""

CODE_ENUMS: tuple[type[Enum], ...] = (Code, ErrorCode)
"""The two enums whose member is a code an agent dispatches on, written in capitals."""

SHARED = {
    "kept": "A take that was not re-voiced and a sound that was not regenerated are the same fact twice.",
    "placeholder": "A run asks for a placeholder voicing and a row reports one, so it is the request and the outcome.",
    "project": "A layer is where a value was written and a scope is where it may be, and both are the project file.",
    "machine": "A layer is where a value was written and a scope is where it may be, and both are the machine file.",
}
"""Every word two families own, and the one sentence saying why one word names one thing in both."""

ALSO_NAMES = {
    "project": "It names the `[project]` table and the module a caller opens, so a literal is one of those.",
    "soundscape": "It names the `[soundscape]` table, the stage module and `build/soundscape`.",
    "narrate": "It names the stage module and `build/narrate`, which the workspace joins a path from.",
    "cue": "It names the stage module and the key of a row in `cues.json`.",
    "clip": "It names the stage module, the `clip` key of a section and the command a caller calls.",
    "page": "It names the vocabulary module and the `page` key of a section.",
    "record": "It names the stage module and the `[record]` table.",
    "verify": "It names the stage module and the `[verify]` table.",
    "assemble": "It names the stage module the facade imports by name.",
    "machine": "It names the module that holds the machine and the file its settings are written to.",
    "music": "It names the bed the `[mix]` table points a file at, which the `[soundscape]` table does not list.",
    "ambience": "It names the bed the `[mix]` table points a file at, which the `[soundscape]` table does not list.",
    "derived": "It is the word a published number's sentence opens with, which `x-numbers` reads back.",
    "environment": "It names the layer and the kind of unknown key a refusal reports, which is the same fact.",
}
"""Every word the one-word design gives a second job, and the sentence saying what that job is.

This is not a suppression. The founder's design has one word name the stage, its module, its table,
its directory and its event, so a literal spelling one of them names the table or the path rather
than the member. A word leaves this list by leaving the enums, which the test below holds.
"""


def vocabulary() -> dict[str, str]:
    """Every word the enums own, and what it spells."""
    words: dict[str, str] = {}
    for enum in (*CODE_ENUMS, *WORD_ENUMS):
        for member in enum:
            words.setdefault(str(member.value), f"{enum.__name__}.{member.name}")
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


def _prose(node: ast.AST) -> list[ast.Constant]:
    """The string constants in one node that are prose, which is a docstring or an f-string part."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return [node.value]  # A docstring, or a bare string standing as one.
    if isinstance(node, ast.JoinedStr):
        return _constants(node)
    if isinstance(node, ast.Assign):
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        return _constants(node.value) if "__all__" in names else []
    return []


def _keys_of_call(node: ast.Call) -> list[ast.Constant]:
    """The string constants one call reads as a key, an attribute name or a word of a command line."""
    name = _called(node)
    if name in COMMAND_LINE_RUNNERS:
        return _words([*node.args, *(keyword.value for keyword in node.keywords)])
    if isinstance(node.func, ast.Attribute) and (name in KEY_READERS or name.startswith("get_")):
        return _words(node.args[:1])
    if name in ("getattr", "hasattr", "setattr", "delattr", "setitem", "delitem"):
        return _words(node.args[1:2])
    return []


def _keys(node: ast.AST) -> list[ast.Constant]:
    """The string constants in one node that name a key, an attribute or a word of a command line."""
    if isinstance(node, ast.Assign):
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        return _words([node.value]) if any(name.endswith("KEYS") for name in names) else []
    if isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
        return _words([node.left])
    if isinstance(node, ast.Dict):
        return _words([key for key in node.keys if key is not None])
    if isinstance(node, ast.Subscript):
        return _words([node.slice])
    if isinstance(node, ast.Call):
        return _keys_of_call(node)
    if isinstance(node, (ast.List, ast.Tuple)):
        words = _words(list(node.elts))
        return words if any(word.value.startswith("-") and len(word.value) > 1 for word in words) else []
    return []


def exempt(tree: ast.Module) -> set[int]:
    """The ids of every string constant that is prose, an export, a lower-case key or a command word."""
    prose: set[int] = set()
    keys: list[ast.Constant] = []
    for node in ast.walk(tree):
        prose.update(id(constant) for constant in _prose(node))
        keys += _keys(node)
    return prose | {id(word) for word in keys if word.value == word.value.lower()}


def violations(source: str, where: str) -> list[str]:
    """Every literal in one file that spells the vocabulary where it stands for the thing itself."""
    tree = ast.parse(source)
    skip = exempt(tree)
    found = [
        f"{where}:{node.lineno}: {node.value!r} is {WORDS[node.value]}"
        for node in _constants(tree)
        if node.value in WORDS and node.value not in ALSO_NAMES and id(node) not in skip
    ]
    return sorted(set(found), key=lambda line: (line.split(":")[0], int(line.split(":")[1]), line))


def python_files() -> list[Path]:
    files = [*(ROOT / "src" / "decktalk").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    return sorted(p for p in files if "__pycache__" not in p.parts and "out" not in p.relative_to(ROOT).parts)


def measured() -> dict[str, int]:
    """How many literals each file still spells, which is what the baseline is a count of."""
    found: dict[str, int] = {}
    for path in python_files():
        where = path.relative_to(ROOT).as_posix()
        if where in DEFINING:
            continue
        count = len(violations(path.read_text(encoding="utf-8"), where))
        if count:
            found[where] = count
    return found


def baseline() -> dict[str, int]:
    return dict(json.loads(BASELINE.read_text(encoding="utf-8"))["files"])


def test_no_file_outside_the_baseline_spells_a_word_of_the_vocabulary():
    """A file with no entry uses the enum member, which is the whole rule for new code."""
    excused = baseline()
    added = {name: count for name, count in measured().items() if name not in excused}
    assert not added, (
        "these files spell a closed vocabulary as a string: "
        + ", ".join(f"{name} ({count})" for name, count in sorted(added.items()))
        + ". Use the enum member, and `.value` where the word is written into text."
    )


def test_no_file_on_the_baseline_spells_more_than_it_did():
    """The list only shrinks, so a change that adds a literal to a file already on it is refused."""
    found = measured()
    grown = {name: (count, found[name]) for name, count in baseline().items() if found.get(name, 0) > count}
    assert not grown, "these files grew a literal: " + ", ".join(
        f"{name} {was} to {now}" for name, (was, now) in sorted(grown.items())
    )


def test_a_baseline_entry_no_file_needs_any_more_is_removed():
    """A count the code has beaten is a debt list rather than a rule, so it is written down as zero."""
    found = measured()
    stale = {name: count for name, count in baseline().items() if found.get(name, 0) < count}
    assert not stale, (
        "these files spell fewer words than the baseline excuses, so the baseline is stale: "
        + ", ".join(f"{name} {count} to {found.get(name, 0)}" for name, count in sorted(stale.items()))
        + ". Run `uv run python tests/contract/test_vocabulary.py --write`."
    )


PLANTED = [
    ("prose", '"""skipped"""\nwhy = f"{row} is not skipped"\nif row == "skipped": pass', [Outcome.SKIPPED.value]),
    ("export", '__all__ = ["voiced"]\nSTATES = ["voiced"]', [TakeStatus.VOICED.value]),
    (
        "subscript",
        'closed = row["outcome"] == "skipped"\ncount = tally["CUE_OFF"]',
        [Outcome.SKIPPED.value, Code.CUE_OFF.value],
    ),
    ("dict key", 'row = {"skipped": 1}\ntally = {"CUE_OFF": 1}', [Code.CUE_OFF.value]),
    (
        "mapping read",
        'seen = doc.get("certain")\nsure = row == "uncertain"',
        [Certainty.UNCERTAIN.value],
    ),
    ("attribute", 'quiet = getattr(args, "quiet")\ncode = getattr(Code, "CUE_OFF")', [Code.CUE_OFF.value]),
    ("setitem", 'mp.setitem(HANDLERS, "voiced", f)\nmp.setitem(HANDLERS, "INPUT", f)', [ErrorCode.INPUT.value]),
    ("command line", 'main(["verify", "--json"], check=lambda d: d.code == "CUE_OFF")', [Code.CUE_OFF.value]),
    ("flag list", 'lines = [("--strict", "voiced")]\nmore = [("--strict", "PAGE_BLACK")]', [Code.PAGE_BLACK.value]),
    ("command", 'Command("clip", "cut a span")\nCommand("CUE_OFF", "judge")', [Code.CUE_OFF.value]),
    (
        "membership",
        'both = "voiced" in raw and "CUE_OFF" in tally\nmade = "generated" == item.status',
        [Code.CUE_OFF.value, SoundStatus.GENERATED.value],
    ),
    (
        "key tuple",
        'WHERE_KEYS = ("where", "page")\nCODE_KEYS = ("PAGE_BLACK",)\nSTATES = ("kept",)',
        [Code.PAGE_BLACK.value, TakeStatus.KEPT.value],
    ),
]
"""Each exemption beside a literal it must still see: (the exemption, a source, the words it must find)."""


@pytest.mark.parametrize(("exemption", "source", "caught"), PLANTED, ids=[case[0] for case in PLANTED])
def test_the_walk_sees_the_literal_beside_each_exemption(exemption, source, caught):
    """The guard is only worth its exemptions if it still sees the literal each of them is not."""
    found = [line.split(": ", 1)[1].split(" is ", 1)[0] for line in violations(source, f"{exemption}.py")]
    assert found == [repr(word) for word in caught]


def owners() -> dict[str, set[str]]:
    """Each enum against the words it owns, which is what makes a literal unambiguous."""
    return {enum.__name__: {str(member.value) for member in enum} for enum in (*CODE_ENUMS, *WORD_ENUMS)}


@pytest.mark.parametrize("word", sorted(WORDS))
def test_every_word_is_spelled_by_one_family_or_by_a_declared_pair(word):
    """A word two enums shared would make a literal ambiguous, so a shared word needs its sentence."""
    families = sorted(name for name, words in owners().items() if word in words)
    if len(families) > 1:
        assert word in SHARED, f"{word!r} is owned by {families}, so give it a sentence in SHARED or rename one."
    else:
        assert len(families) == 1, word


@pytest.mark.parametrize("word", sorted(SHARED))
def test_every_shared_word_is_really_shared(word):
    """A sentence about a word one family owns is a note nobody has to read, so the list stays true."""
    families = sorted(name for name, words in owners().items() if word in words)
    assert len(families) > 1, f"{word!r} is owned by {families} alone, so its row in SHARED can go."
    assert SHARED[word].endswith("."), word


@pytest.mark.parametrize("word", sorted(ALSO_NAMES))
def test_every_word_with_a_second_job_is_still_a_word_an_enum_owns(word):
    """A sentence about a word no enum owns any more is a note nobody has to read, so the list shrinks."""
    assert word in WORDS, f"{word!r} is no longer a member of any enum, so its row in ALSO_NAMES can go."
    assert ALSO_NAMES[word].endswith("."), word


def test_every_defining_module_is_still_there():
    for name in sorted(DEFINING):
        assert (ROOT / name).exists(), f"{name} defines part of the vocabulary and is not there any more."


def _write() -> int:
    """Lower every count the code has beaten, and refuse to raise one, which is the ratchet."""
    committed = baseline() if BASELINE.exists() else {}
    found = measured()
    lowered = {name: min(count, found.get(name, 0)) for name, count in {**found, **committed}.items()}
    kept = {name: count for name, count in sorted(lowered.items()) if count}
    raised = sorted(name for name, count in found.items() if count > committed.get(name, count))
    BASELINE.write_text(json.dumps({"files": kept}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {BASELINE.name} with {len(kept)} files and {sum(kept.values())} literals")
    if raised:
        print("these files are not excused and must use the enum member: " + ", ".join(raised))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_write())
