"""The shape of the package: which module may import which, and what every stage returns.

`src/decktalk` is five layers deep, lowest to highest: **vocabulary**, **leaves**, **model**,
**stages** and **CLI**. A module may import from a layer below its own, or from inside its own
package, and from nothing else. The leaves are wide enough to be ranked among themselves, so the
table below gives every top-level module and package one rank and one comparison enforces both the
layer and the order inside it.

The walk reads the AST rather than the imports Python happens to run, so an import hidden in a
function body counts exactly as much as one at the top of the file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import get_origin, get_type_hints

import pytest

import decktalk
from decktalk.verdicts import Findings

SRC = Path(__file__).resolve().parent.parent / "src" / "decktalk"

# Every top-level module and package of decktalk, with its layer and its rank inside that layer.
# A module may import a name from a strictly lower rank, or from inside its own package.
LAYERS: dict[str, tuple[str, int]] = {
    "errors": ("vocabulary", 0),
    "secret": ("vocabulary", 0),
    "verdicts": ("vocabulary", 0),
    "jsonio": ("leaves", 1),
    "tomlmap": ("leaves", 1),
    "toolchain": ("leaves", 1),
    "settings": ("leaves", 2),
    "artifacts": ("leaves", 3),
    "captions": ("leaves", 4),
    "media": ("leaves", 4),
    "speech": ("leaves", 4),
    "model": ("model", 5),
    "stages": ("stages", 6),
    "status": ("stages", 6),
    "scaffold": ("stages", 6),
    "report": ("CLI", 7),
    "cli": ("CLI", 8),
    "__init__": ("CLI", 9),
    "__main__": ("CLI", 10),
}

# The imports that point sideways, each with the reason it exists, so every exception is designed
# and not accidental. `build` and `preflight` are the two commands whose purpose is to run or
# rehearse the others, which the package design states.
ALLOWED_STAGE_EDGES: dict[tuple[str, str], str] = {
    # build runs the five stages in order.
    ("stages.build", "stages.narrate"): "by design",
    ("stages.build", "stages.align"): "by design",
    ("stages.build", "stages.record"): "by design",
    ("stages.build", "stages.assemble"): "by design",
    ("stages.build", "stages.verify"): "by design",
    # preflight rehearses narrate and align and freezes what verify would measure.
    ("stages.preflight", "stages.narrate"): "by design",
    ("stages.preflight", "stages.align"): "by design",
    ("stages.preflight", "stages.verify"): "by design",
    # The recorder owns the URL a page is opened at, and the frozen renders and the screenshots
    # open the same page, so both read that one URL rather than spelling it a second time.
    ("stages.preflight", "stages.record"): "the page URL the recorder owns",
    ("stages.screenshots", "stages.record"): "the page URL the recorder owns",
}


@dataclass(frozen=True)
class Edge:
    """One import, from the module that makes it to the module it names."""

    source: str  # the importing module, as a dotted path under decktalk
    target: str  # the imported module, as a dotted path under decktalk
    line: int

    @property
    def where(self) -> str:
        return f"{self.source.replace('.', '/')}.py:{self.line}"


def modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def module_names() -> set[str]:
    """Every module and package inside decktalk, so `from . import x` can be told from a plain name."""
    return {dotted(p) for p in modules()}


def dotted(path: Path) -> str:
    """A source file as a dotted module path under decktalk, with __init__ standing for its package."""
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1]) if parts[-1] == "__init__" and len(parts) > 1 else ".".join(parts)


def package_of(path: Path) -> list[str]:
    """The dotted package a file lives in, as parts. A package's __init__ lives in the package itself."""
    return list(path.relative_to(SRC).with_suffix("").parts[:-1])


def resolve(node: ast.ImportFrom, package: list[str]) -> str | None:
    """The absolute module a `from ... import` names, or None when it leaves decktalk.

    A relative import counts one dot for the file's own package and one more for each level above it.
    """
    if node.level == 0:
        name = node.module or ""
        return name.removeprefix("decktalk.") if name.startswith("decktalk") else None
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def edges() -> list[Edge]:
    """Every import inside decktalk, at module level and in a function body alike.

    `from . import x` names no module of its own, so each of its aliases is resolved against the
    file's own package and kept when it names a module. Without that, the one import form a
    package uses on its own siblings would be invisible here.
    """
    known = module_names()
    out: list[Edge] = []
    for path in modules():
        source, package = dotted(path), package_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module is None:
                    # `from . import x` names no module of its own, so each alias is resolved
                    # against the file's own package and kept when it names one.
                    base = package[: len(package) - node.level + 1]
                    for alias in node.names:
                        name = ".".join([*base, alias.name])
                        if name in known:
                            out.append(Edge(source, name, node.lineno))
                    continue
                target = resolve(node, package)
                if target:
                    out.append(Edge(source, target, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("decktalk."):
                        out.append(Edge(source, alias.name.removeprefix("decktalk."), node.lineno))
    return out


def unit(module: str) -> str:
    """The top-level module or package a dotted path belongs to."""
    return module.split(".", 1)[0]


def private(name: str) -> bool:
    """Whether a name belongs to the module that defines it. A dunder such as __version__ does not."""
    return name.startswith("_") and not name.startswith("__")


def test_every_module_is_placed_in_a_layer():
    """A new top-level module joins the table above, which is what makes the rule enforceable."""
    top = {unit(dotted(p)) for p in modules()}
    assert top - set(LAYERS) == set(), sorted(top - set(LAYERS))


def test_no_import_points_up_a_layer_or_sideways_within_one():
    """Every import goes down a rank or stays inside its own package, at module level or in a body."""
    bad: list[str] = []
    for edge in edges():
        source_unit, target_unit = unit(edge.source), unit(edge.target)
        if source_unit == target_unit:
            continue
        source_layer, source_rank = LAYERS[source_unit]
        target_layer, target_rank = LAYERS[target_unit]
        if target_rank < source_rank:
            continue
        bad.append(
            f"{edge.where}: {edge.source} ({source_layer}, rank {source_rank}) imports "
            f"{edge.target} ({target_layer}, rank {target_rank})"
        )
    assert not bad, "\n".join(bad)


def test_no_stage_imports_another_stage_but_build():
    """A stage reaches its neighbours through the model and the leaves, never through another stage."""
    bad: list[str] = []
    for edge in edges():
        if not (edge.source.startswith("stages.") and edge.target.startswith("stages.")):
            continue
        source, target = ".".join(edge.source.split(".")[:2]), ".".join(edge.target.split(".")[:2])
        if source == target or (source, target) in ALLOWED_STAGE_EDGES:
            continue
        bad.append(f"{edge.where}: {source} imports {target}")
    assert not bad, "\n".join(bad)


def test_no_module_imports_a_private_name_from_another_module():
    """A name whose first character is `_` belongs to its module, and a package's surface is its __init__."""
    bad: list[str] = []
    for path in modules():
        source, package = dotted(path), package_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = resolve(node, package)
            if target is None or target == source:
                continue
            if private(target.split(".")[-1]):
                bad.append(f"{source.replace('.', '/')}.py:{node.lineno}: imports the private module {target}")
            for alias in node.names:
                if private(alias.name):
                    bad.append(f"{source.replace('.', '/')}.py:{node.lineno}: imports {alias.name} from {target}")
    assert not bad, "\n".join(bad)


# Every command, and the result its function returns. Each of the twelve satisfies `StageResult`.
STAGE_RESULTS = {
    "narrate": "NarrateResult",
    "align": "AlignResult",
    "record": "RecordResult",
    "assemble": "AssembleResult",
    "verify": "VerifyResult",
    "preflight": "PreflightResult",
    "screenshots": "ScreenshotsResult",
    "words": "WordsResult",
    "clip": "ClipResult",
    "soundscape": "SoundscapeResult",
    "build": "BuildResult",
    "status": "StatusResult",
}


@pytest.mark.parametrize("command", sorted(STAGE_RESULTS))
def test_every_stage_returns_a_stage_result(command):
    """A result carries `findings -> Findings` and `to_dict(root)`, so the CLI knows nothing else."""
    returned = get_type_hints(getattr(decktalk, command))["return"]
    assert returned.__name__ == STAGE_RESULTS[command]
    assert isinstance(returned.findings, property), f"{command}: findings must be a property"
    tally = get_type_hints(returned.findings.fget)["return"]
    assert tally is Findings, f"{command}: findings must give a Findings, not {tally}"
    hints = get_type_hints(returned.to_dict)
    assert hints.get("root") is Path, f"{command}: to_dict must take a root: Path"
    assert get_origin(hints["return"]) is dict, f"{command}: to_dict must give a dict"


def test_every_command_of_the_public_api_is_in_the_result_table():
    """A command that stops returning a result, or a new one that returns none, fails here."""
    commands = {name for name in decktalk.__all__ if callable(getattr(decktalk, name)) and name[0].islower()}
    assert commands - {"load_settings", "register_speech_provider"} == set(STAGE_RESULTS)
