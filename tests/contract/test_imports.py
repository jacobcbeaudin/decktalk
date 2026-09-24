"""The shape of the package: which module may import which, and that every target it names is real.

`src/decktalk` is five layers deep, lowest to highest: **vocabulary**, **models**, **leaves**,
**sdk** and **cli**. A module may import from a strictly lower rank, or from inside its own package,
and from nothing else. The layers are wide enough to be ranked among themselves, so the table below
gives every top-level module and package one rank and one comparison enforces both the layer and the
order inside it.

The walk reads the AST rather than the imports Python happens to run, so an import hidden in a
function body counts exactly as much as one at the top of the file. It also resolves the alias form,
`from decktalk.stages import record`, which names a module without ever spelling its dotted path:
that form is how a stage reaches its neighbour and it was invisible to this guard before. The other
blind spot is closed by `test_every_import_target_resolves_to_a_ranked_module`, which fails on a
target that no longer exists, because an import of a deleted module used to pass the rank comparison
by being unrankable rather than by being allowed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from support.paths import REPO

SRC = REPO / "src" / "decktalk"

LAYERS: dict[str, tuple[str, int]] = {
    # The words every layer above shares, which import nothing but each other.
    "pipeline": ("vocabulary", 0),
    "findings": ("vocabulary", 1),
    "errors": ("vocabulary", 2),
    "locate": ("vocabulary", 3),
    "secret": ("vocabulary", 4),
    "page": ("vocabulary", 5),
    # The frozen models and the settings tree, which every layer above reads and none of them writes.
    "results": ("models", 6),
    "events": ("models", 7),
    "catalog": ("models", 8),
    "tomlmap": ("models", 9),
    "settings": ("models", 10),
    "explain": ("models", 11),
    # One job each, and no knowledge of a project.
    "toolchain": ("leaves", 12),
    "captions": ("leaves", 13),
    "speech": ("leaves", 14),
    "media": ("leaves", 15),
    "pagescan": ("leaves", 16),
    "template": ("leaves", 17),
    "artifacts": ("leaves", 18),
    # The object a caller drives, and the stages it drives.
    "inputs": ("sdk", 19),
    "machine": ("sdk", 20),
    "stages": ("sdk", 21),
    "project": ("sdk", 22),
    # The first client, and the package's public surface.
    "cli": ("cli", 23),
    "__init__": ("cli", 24),
    "__main__": ("cli", 25),
}
"""Every top-level module and package of decktalk, with its layer and its rank inside that layer.

`project` ranks above `stages` because it calls a stage by name through `import_module`, which is a
string and which no AST walk can see. Declaring the rank the code really has is what keeps that one
edge honest, and it is why `stages` may not import `project` back.
"""

ALLOWED_STAGE_EDGES: dict[tuple[str, str], str] = {
    # build runs the six stages in the order `PIPELINE` gives them.
    ("stages.build", "stages.narrate"): "by design",
    ("stages.build", "stages.cue"): "by design",
    ("stages.build", "stages.record"): "by design",
    ("stages.build", "stages.soundscape"): "by design",
    ("stages.build", "stages.assemble"): "by design",
    ("stages.build", "stages.verify"): "by design",
    ("stages.build", "stages.storyboard"): "the checkpoint drawn before any credit is spent",
    # check rehearses what the stages downstream of it would judge, without producing any of it.
    ("stages.check", "stages.narrate"): "by design",
    ("stages.check", "stages.cue"): "by design",
    ("stages.check", "stages.storyboard"): "by design",
    ("stages.check", "stages.verify"): "by design",
    # The recorder owns the query a page section is opened at, so the storyboard opens the same page
    # by reading that one rule rather than spelling it a second time.
    ("stages.storyboard", "stages.record"): "the page URL the recorder owns",
    # One rule decides whether a recording still matches the project, and the report that names a
    # stale one asks the stage that wrote it rather than comparing file times of its own.
    ("stages.status", "stages.record"): "the rule that decides a recording is stale",
    # A clip is cut on the section clock, which is the one thing `words` computes.
    ("stages.clip", "stages.words"): "the section clock a clip is cut on",
}
"""Every import that points sideways between stages, each with the reason it exists.

An exception here is designed and not accidental, so a stage that reaches its neighbour for anything
else fails until its reason is written down or the shared rule moves below both of them.
"""


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
    """Every module and package inside decktalk, so an alias can be told from an imported name."""
    return {dotted(p) for p in modules()}


def dotted(path: Path) -> str:
    """A source file as a dotted module path under decktalk, with __init__ standing for its package."""
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1]) if parts[-1] == "__init__" and len(parts) > 1 else ".".join(parts)


def package_of(path: Path) -> list[str]:
    """The dotted package a file lives in, as parts. A package's __init__ lives in the package itself."""
    return list(path.relative_to(SRC).with_suffix("").parts[:-1])


def base_of(node: ast.ImportFrom, package: list[str]) -> str | None:
    """The module a `from ... import` reads from, as a dotted path under decktalk, or None outside it.

    An empty string is the package itself, which is what `from decktalk import page` reads from, and
    a relative import counts one dot for the file's own package and one more for each level above it.
    """
    if node.level == 0:
        name = node.module or ""
        if not name.startswith("decktalk"):
            return None
        return name.removeprefix("decktalk").removeprefix(".")
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def targets_of(node: ast.ImportFrom, package: list[str], known: set[str]) -> list[str]:
    """Every module one `from ... import` names, which is the base and each alias that is a module.

    `from decktalk.stages import record` names a module through an alias and never spells its path,
    which is the form one stage reaches another by, so each alias is resolved and kept when it names
    a module of its own.
    """
    base = base_of(node, package)
    if base is None:
        return []
    found = [base] if base else []
    for alias in node.names:
        name = f"{base}.{alias.name}" if base else alias.name
        if name in known:
            found.append(name)
    return found


def edges() -> list[Edge]:
    """Every import inside decktalk, at module level and in a function body alike."""
    known = module_names()
    out: list[Edge] = []
    for path in modules():
        source, package = dotted(path), package_of(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                out += [Edge(source, target, node.lineno) for target in targets_of(node, package, known)]
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
    missing = sorted(top - set(LAYERS))
    assert missing == [], f"{missing} have no row in LAYERS, so no rule holds over what they import."


def test_every_layer_holds_a_run_of_consecutive_ranks():
    """A layer is a name for a run of ranks, so a module cannot be filed under one and ranked in another."""
    for layer in dict.fromkeys(name for name, _ in LAYERS.values()):
        ranks = sorted(rank for name, rank in LAYERS.values() if name == layer)
        assert ranks == list(range(ranks[0], ranks[-1] + 1)), f"the {layer} layer is not one run of ranks: {ranks}"


def test_every_rank_is_held_by_exactly_one_module():
    """Two modules at one rank could import each other, which is the sideways edge this table refuses."""
    ranks = [rank for _, rank in LAYERS.values()]
    assert sorted(ranks) == sorted(set(ranks)), "two modules share a rank, so neither is above the other."


def test_every_import_target_resolves_to_a_ranked_module():
    """An import of a module that is not there passed the rank comparison by being unrankable.

    This is the blind spot of must 11. A deleted module keeps its callers compiling until the line
    runs, and the guard that was meant to catch the edge read it as no edge at all.
    """
    known = module_names()
    bad = [
        f"{edge.where}: imports {edge.target}, which is not a module of decktalk"
        for edge in edges()
        if edge.target not in known
    ]
    assert not bad, "\n".join(sorted(set(bad)))


def test_no_import_points_up_a_layer_or_sideways_within_one():
    """Every import goes down a rank or stays inside its own package, at module level or in a body."""
    bad: list[str] = []
    for edge in edges():
        source_unit, target_unit = unit(edge.source), unit(edge.target)
        if source_unit == target_unit or target_unit not in LAYERS or source_unit not in LAYERS:
            continue
        source_layer, source_rank = LAYERS[source_unit]
        target_layer, target_rank = LAYERS[target_unit]
        if target_rank < source_rank:
            continue
        bad.append(
            f"{edge.where}: {edge.source} ({source_layer}, rank {source_rank}) imports "
            f"{edge.target} ({target_layer}, rank {target_rank})"
        )
    assert not bad, "\n".join(sorted(set(bad)))


def test_no_stage_imports_another_stage_but_the_ones_that_run_the_others():
    """A stage reaches its neighbours through the sdk and the leaves, never through another stage."""
    bad: list[str] = []
    for edge in edges():
        if not (edge.source.startswith("stages.") and edge.target.startswith("stages.")):
            continue
        source, target = ".".join(edge.source.split(".")[:2]), ".".join(edge.target.split(".")[:2])
        if source == target or (source, target) in ALLOWED_STAGE_EDGES:
            continue
        bad.append(f"{edge.where}: {source} imports {target}")
    assert not bad, "\n".join(sorted(set(bad)))


def test_every_allowed_stage_edge_is_an_edge_the_code_still_makes():
    """An exception nothing uses is a hole left open, so the list only holds edges that are really there."""
    made = {(".".join(e.source.split(".")[:2]), ".".join(e.target.split(".")[:2])) for e in edges()}
    stale = sorted(pair for pair in ALLOWED_STAGE_EDGES if pair not in made)
    assert stale == [], f"{stale} are allowed and no longer made, so the exception can go."


def test_no_module_imports_a_private_name_from_another_module():
    """A name whose first character is `_` belongs to its module, and a package's surface is its __init__."""
    known = module_names()
    bad: list[str] = []
    for path in modules():
        source, package = dotted(path), package_of(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            base = base_of(node, package)
            if base is None or base == source or base not in known:
                continue
            if private(base.split(".")[-1]):
                bad.append(f"{source.replace('.', '/')}.py:{node.lineno}: imports the private module {base}")
            for alias in node.names:
                if private(alias.name):
                    bad.append(f"{source.replace('.', '/')}.py:{node.lineno}: imports {alias.name} from {base}")
    assert not bad, "\n".join(sorted(set(bad)))
