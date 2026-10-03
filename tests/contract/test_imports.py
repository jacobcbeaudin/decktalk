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

The ranks hold between packages and say nothing inside one, so two modules of one package could
import each other and pass. The imports are also read as a graph of modules, and that graph has no
cycle at all, however far round it goes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import rustworkx as rx

from support.paths import SRC

LAYERS: dict[str, tuple[str, ...]] = {
    # The words every layer above shares, which import nothing but each other, and the one rule by
    # which a file a person owns is replaced.
    "vocabulary": ("files", "pipeline", "findings", "secret", "errors", "locate", "page"),
    # The frozen models and the settings tree, which every layer above reads and none of them writes.
    # `logs` is the bridge from standard logging to the stream, which reads the stream and nothing above it.
    "models": ("results", "events", "logs", "tomlmap", "settings"),
    # One job each, and no knowledge of a project.
    "leaves": ("toolchain", "captions", "speech", "media", "pagescan", "template", "artifacts"),
    # The object a caller drives, and the stages it drives.
    "sdk": ("inputs", "machine", "explain", "stages", "project"),
    # The first client, and the package's public surface.
    "cli": ("cli", "__init__", "__main__"),
}
"""Every top-level module and package of decktalk under its layer, lowest first, each ranked by where it stands.

A module's rank is its place in the whole table read top to bottom, so a layer is always one run of
ranks and no two modules share one. `project` ranks above `stages` because it calls a stage by name
through `import_module`, which is a string and which no AST walk can see. Declaring the rank the
code really has is what keeps that one edge honest, and it is why `stages` may not import `project`
back.
"""

RANKS: dict[str, tuple[str, int]] = {
    name: (layer, rank)
    for rank, (layer, name) in enumerate((layer, name) for layer, names in LAYERS.items() for name in names)
}
"""Every module's layer and rank, read off `LAYERS` in order."""

ALLOWED_STAGE_EDGES: dict[tuple[str, str], str] = {
    # build runs the six stages in the order `PIPELINE` gives them.
    ("stages.build", "stages.narrate"): "by design",
    ("stages.build", "stages.cue"): "by design",
    ("stages.build", "stages.record"): "by design",
    ("stages.build", "stages.score"): "by design",
    ("stages.build", "stages.assemble"): "by design",
    ("stages.build", "stages.verify"): "by design",
    ("stages.build", "stages.storyboard"): "the checkpoint drawn before any credit is spent",
    ("stages.build", "stages.status"): "an unchanged build keeps what the kept record says it already made",
    # check rehearses what the stages downstream of it would judge, without producing any of it.
    ("stages.check", "stages.narrate"): "by design",
    ("stages.check", "stages.cue"): "by design",
    ("stages.check", "stages.storyboard"): "by design",
    ("stages.check", "stages.verify"): "by design",
    # The storyboard owns how a frozen page is opened, so the poster is drawn from a page opened the same way.
    ("stages.assemble", "stages.storyboard"): "the one way a frozen page is opened",
    ("stages.assemble", "stages.score"): "which sound files are the score's to buy, so an absent one is a warning",
    # The recorder owns the query a page section is opened at, so the storyboard opens the same page
    # by reading that one rule rather than spelling it a second time.
    ("stages.storyboard", "stages.record"): "the page URL the recorder owns",
    # One rule decides whether a recording still matches the project, and the report that names a
    # stale one asks the stage that wrote it rather than comparing file times of its own.
    ("stages.status", "stages.record"): "the rule that decides a recording is stale",
    # A take is named by narrate's digest alone, so the report that lists the takes no section plays
    # names each section's take the way narrate does rather than spelling a digest of its own.
    ("stages.status", "stages.narrate"): "the digest a section's take is named by",
    # The score is ready to mix once its bought music is joined, which the stage that joins it decides.
    ("stages.status", "stages.score"): "the rule that decides the score is ready to mix",
    # A clip is cut on the section clock, which is the one thing `words` computes.
    ("stages.clip", "stages.words"): "the section clock a clip is cut on",
    # Every stage that fans its sections out to workers shares one pool, so they all halt alike.
    ("stages.narrate", "stages.pool"): "the one pool every stage fans out to",
    ("stages.record", "stages.pool"): "the one pool every stage fans out to",
}
"""Every import that points sideways between stages, each with the reason it exists.

An exception here is designed and not accidental, so a stage that reaches its neighbour for anything
else fails until its reason is written down or the shared rule moves below both of them.
"""


ALLOWED_PRIVATE_MODULES: dict[tuple[str, str], str] = {
    # Typer publishes `typer.Context` and no name for the help formatter, the parameter, the command
    # or the refusals its parser raises, and Click's own classes are not the ones Typer makes.
    ("cli.app", "typer._click"): "the Click classes Typer carries and publishes no name for",
}
"""Every private module of another distribution that one module of decktalk reaches into, with why.

A module whose name starts with `_` is its distribution's own and may appear, move or vanish in any
release, whatever floor `pyproject.toml` declares. One module per reason holds the reach, so a
release that moves it breaks one import rather than every file that copied it.
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


def module_graph() -> rx.PyDiGraph:
    """Every import between two modules of decktalk as a graph, each edge carrying the import it stands for.

    A module's import of a package it lives inside is left out, because Python has begun that
    package before it runs any module in it, so that edge can never be the one that finds a module
    half made.
    """
    graph = rx.PyDiGraph()
    index = {name: graph.add_node(name) for name in sorted(module_names())}
    for edge in edges():
        if edge.target in index and edge.source != edge.target and not edge.source.startswith(f"{edge.target}."):
            graph.add_edge(index[edge.source], index[edge.target], edge)
    return graph


def unit(module: str) -> str:
    """The top-level module or package a dotted path belongs to."""
    return module.split(".", 1)[0]


def private(name: str) -> bool:
    """Whether a name belongs to the module that defines it. A dunder such as __version__ does not."""
    return name.startswith("_") and not name.startswith("__")


VENDOR = re.compile(r"eleven|xi-api-key", re.IGNORECASE)
"""Every spelling of a speech or sound vendor DeckTalk ships: its name, its model ids and its key header."""

VENDOR_ALLOWED: dict[str, str] = {}
"""Every module above `speech/` that may still name a vendor, each with why. None does.

`settings` ranks below `speech/` and holds each adapter's own table, which is where a vendor's
fields are declared, so it is not above the boundary and needs no row here.
"""


def test_no_module_above_speech_names_a_vendor():
    """Everything a vendor is particular about is declared by its adapter, so nothing above `speech/` names one.

    A module ranked above `speech` reads the adapter's declaration and never spells the vendor, in
    code, in a string or in a docstring, so a second voice or a change of vendor touches nothing above
    the boundary.
    """
    _, boundary = RANKS["speech"]
    bad = [
        f"{dotted(path)}:{number}: {line.strip()}"
        for path in modules()
        if RANKS[unit(dotted(path))][1] > boundary and dotted(path) not in VENDOR_ALLOWED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if VENDOR.search(line)
    ]
    assert bad == [], "\n".join(bad)


def test_every_vendor_allowance_is_still_needed():
    """An allowance for a module that names no vendor any more is a hole left open."""
    stale = [name for name in VENDOR_ALLOWED if not VENDOR.search((SRC / f"{name.replace('.', '/')}.py").read_text())]
    assert stale == [], stale


def test_every_module_is_placed_in_a_layer():
    """A new top-level module joins the table above, which is what makes the rule enforceable."""
    top = {unit(dotted(p)) for p in modules()}
    missing = sorted(top - set(RANKS))
    assert missing == [], f"{missing} have no row in LAYERS, so no rule holds over what they import."


def test_every_module_is_ranked_once():
    """A module listed twice would hold two ranks, and the later one would quietly win."""
    listed = [name for names in LAYERS.values() for name in names]
    assert len(listed) == len(RANKS), f"{sorted(n for n in RANKS if listed.count(n) > 1)} are listed twice."


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
        if source_unit == target_unit or target_unit not in RANKS or source_unit not in RANKS:
            continue
        source_layer, source_rank = RANKS[source_unit]
        target_layer, target_rank = RANKS[target_unit]
        if target_rank < source_rank:
            continue
        bad.append(
            f"{edge.where}: {edge.source} ({source_layer}, rank {source_rank}) imports "
            f"{edge.target} ({target_layer}, rank {target_rank})"
        )
    assert not bad, "\n".join(sorted(set(bad)))


def test_no_module_imports_itself_back_however_far_round():
    """A cycle is a module that may be read half made, and the ranks cannot see one inside a package."""
    graph = module_graph()
    cycles = [
        " -> ".join(graph.get_edge_data(a, b).where for a, b in pairwise([*cycle, cycle[0]]))
        for cycle in rx.simple_cycles(graph)
    ]
    assert not cycles, "\n".join(sorted(cycles))


def test_the_cycle_check_sees_a_cycle_inside_one_package():
    """The check above is worth its name only if it fails on the cycle the ranks let through.

    One import between two modules of one package is turned round and added, which is a cycle the
    rank comparison skips because both ends share a package.
    """
    graph = module_graph()
    source, target = next(
        (a, b)
        for a, b, edge in graph.weighted_edge_list()
        if unit(edge.source) == unit(edge.target) and not edge.target.startswith(f"{edge.source}.")
    )
    graph.add_edge(target, source, Edge(graph[target], graph[source], 1))
    assert not rx.is_directed_acyclic_graph(graph)


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


def private_modules() -> list[tuple[str, str, int]]:
    """Every import of a module outside decktalk whose dotted name has a private part, as source, name and line."""
    found: list[tuple[str, str, int]] = []
    for path in modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            found += [
                (dotted(path), name, node.lineno)
                for name in names
                if not name.startswith("decktalk") and any(private(part) for part in name.split("."))
            ]
    return found


def reached(name: str) -> str:
    """The private module an import reaches into, which is its name up to and including the first private part."""
    parts = name.split(".")
    first = next(index for index, part in enumerate(parts) if private(part))
    return ".".join(parts[: first + 1])


def test_no_module_reaches_into_another_distributions_private_module():
    """A private module is not part of the version range a floor declares, so one module holds each reach.

    A floor is a promise about public names. A private module can be missing at the floor and still
    present in the lockfile, and then every file that imports it fails on a fresh install at the floor
    while every test run from the lockfile passes.
    """
    bad = [
        f"{source.replace('.', '/')}.py:{line}: imports {name}, which is private to its distribution"
        for source, name, line in private_modules()
        if (source, reached(name)) not in ALLOWED_PRIVATE_MODULES
    ]
    assert not bad, "\n".join(sorted(bad))


def test_every_allowed_private_module_is_one_the_code_still_reaches():
    """An exception nothing uses is a hole left open, so the list only holds reaches that are really made."""
    made = {(source, reached(name)) for source, name, _ in private_modules()}
    stale = sorted(pair for pair in ALLOWED_PRIVATE_MODULES if pair not in made)
    assert stale == [], f"{stale} are allowed and no longer made, so the exception can go."
