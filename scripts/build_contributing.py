# /// script
# requires-python = ">=3.12"
# ///
"""Generate the module tree in CONTRIBUTING.md from the package itself.

    uv run scripts/build_contributing.py            # write the tree
    uv run scripts/build_contributing.py --check    # exit 1 if the committed tree would change

Every line is one module of `src/decktalk`, and its sentence is the first sentence of that module's
own docstring, so a module that is added, renamed or repurposed changes this tree by being written.
The order is the import order, read from the `LAYERS` table in `tests/test_imports.py`, which is the
table the suite enforces, so the tree a newcomer reads and the layering a pull request must obey are
the same table.

A directory of the package that holds no Python needs a line here, because a `.js` file has no
docstring to read. The script fails on a data directory it has never heard of, so one cannot be
added without a sentence about it.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "decktalk"
IMPORT_TEST = ROOT / "tests" / "test_imports.py"
TARGET = ROOT / "CONTRIBUTING.md"

START = "<!-- layout:start -->"
END = "<!-- layout:end -->"

# What each layer holds, in one clause, for the line that opens it. The names are the LAYERS table's.
LAYER_NOTES = {
    "vocabulary": "the words every layer above shares",
    "leaves": "one job each, and no knowledge of a project",
    "model": "one project, as everything above reads it",
    "stages": "one module per command, and the scaffold beside them",
    "CLI": "the command line, and the package's public surface",
}

# The directories of the package that hold no Python. A new one fails the run until it is named here.
DATA_DIRS = {
    "runtime": "decktalk-runtime.js, the page contract every deck loads, and decktalk-probe.js, the recorder's own",
    "katex": "The pinned KaTeX release the pages typeset with, copied into a project by `decktalk init`",
    "template": "The starter, the examples and the AGENTS.md that `decktalk init` writes",
    "skills": "The six packaged skills a project keeps in .agents/skills/",
}


def layers() -> dict[str, tuple[str, int]]:
    """The layer and rank of every top-level module, read from the table the test suite enforces."""
    tree = ast.parse(IMPORT_TEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "LAYERS" and node.value:
            return ast.literal_eval(node.value)
    raise SystemExit(f"{IMPORT_TEST.relative_to(ROOT)} has no LAYERS table to read the import order from.")


def summary(path: Path) -> str:
    """The first sentence of a module's docstring, which is the one line the tree has room for."""
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    first = doc.strip().split("\n\n")[0].replace("\n", " ").strip()
    if not first:
        raise SystemExit(f"{path.relative_to(ROOT)} has no docstring, so the module tree has no line for it.")
    return re.split(r"(?<=[.!?])\s", first)[0].strip()


def entries(directory: Path, depth: int) -> list[tuple[int, str, str]]:
    """One row per module and package under `directory`, deepest last, each as (depth, name, line)."""
    rows: list[tuple[int, str, str]] = []
    for path in sorted(directory.iterdir(), key=lambda p: (p.is_dir(), p.name)):
        if path.is_dir() and (path / "__init__.py").is_file():
            rows.append((depth, f"{path.name}/", summary(path / "__init__.py")))
            rows += entries(path, depth + 1)
        elif path.suffix == ".py" and path.name != "__init__.py":
            rows.append((depth, path.name, summary(path)))
    return rows


def package_rows() -> list[tuple[int, str, str]]:
    """Every row of the tree: the five layers in import order, then the directories that hold no Python."""
    rows: list[tuple[int, str, str]] = []
    ranked = sorted(layers().items(), key=lambda kv: (kv[1][1], kv[0]))
    for layer in dict.fromkeys(rank[0] for _, rank in ranked):
        rows.append((0, layer, LAYER_NOTES[layer]))
        for name, _ in [kv for kv in ranked if kv[1][0] == layer]:
            module, package = SRC / f"{name}.py", SRC / name
            if package.is_dir():
                rows.append((1, f"{name}/", summary(package / "__init__.py")))
                rows += entries(package, 2)
            else:
                rows.append((1, f"{name}.py", summary(module)))

    rows.append((0, "packaged data", "what ships in the wheel and holds no Python"))
    known = {p.name for p in SRC.iterdir() if p.is_dir() and not (p / "__init__.py").is_file()}
    known -= {"__pycache__"}  # a build leaves it behind, and no clone holds it
    if unknown := known - set(DATA_DIRS):
        raise SystemExit(f"{', '.join(sorted(unknown))} under {SRC.name}/ needs a line in DATA_DIRS.")
    rows += [(1, f"{name}/", DATA_DIRS[name]) for name in sorted(known)]
    return rows


def render_tree() -> str:
    """The tree as one text block, with every sentence in one column."""
    rows = package_rows()
    width = max(len(name) + 2 * depth for depth, name, _ in rows) + 2
    lines = ["```text", "src/decktalk/"]
    for depth, name, line in rows:
        indent = "  " * (depth + 1)
        lines.append(f"{indent}{name}".ljust(width + 2) + line)
    lines.append("```")
    return "\n".join(lines)


def render(page: str) -> str:
    head, _, rest = page.partition(START)
    _, _, tail = rest.partition(END)
    note = "<!-- Generated by scripts/build_contributing.py from src/decktalk. Do not edit by hand. -->"
    return f"{head}{START}\n{note}\n\n{render_tree()}\n{END}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed tree would change")
    args = ap.parse_args()
    current = TARGET.read_text(encoding="utf-8")
    if START not in current or END not in current:
        print(f"{TARGET.name} has no {START} ... {END} block to fill.")
        return 1
    page = render(current)
    if args.check:
        if current != page:
            print(f"{TARGET.name} is out of date. Run: uv run scripts/build_contributing.py")
            return 1
        print(f"{TARGET.name} is up to date.")
        return 0
    TARGET.write_text(page, encoding="utf-8")
    print(f"wrote {TARGET.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
