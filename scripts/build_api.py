"""Generate src/decktalk/__init__.py, the root of the public API: about thirty entry points and the errors.

    uv run scripts/build_api.py --write    # write the package's __init__.py
    uv run scripts/build_api.py --check    # exit 1 if the committed file would change

`ROOT` is the one list of names the root carries, grouped by why a caller reaches for each. Every
other public type lives in a public module, `decktalk.results`, `decktalk.events`,
`decktalk.findings`, `decktalk.settings`, `decktalk.speech` or `decktalk.speech.sound`, whose own
`__all__` is its surface. `tests/contract/test_api.py` holds the closure over all of them: every
type a public field or a public method's signature names is importable from one of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import generated

ROOT_DIR = Path(__file__).resolve().parent.parent

TARGET = ROOT_DIR / "src" / "decktalk" / "__init__.py"

ROOT: dict[str, tuple[str, ...]] = {
    # The calls a caller starts from: open or make a project, explain a code, and read section numbers.
    "project": ("open", "Project", "Origin", "section_numbers"),
    "explain": ("explain",),
    # The machine a project runs on, the tools it drives and the way to stop a run it opened.
    "machine": ("Machine", "Toolchain", "init"),
    # The stage names every verb, every result and every event is told by.
    "pipeline": ("Stage",),
    # The judgement every call returns and the code and severity a caller filters it by.
    "findings": ("Finding", "Code", "Severity"),
    # The result every call returns and the price every call that buys states.
    "results": ("Result", "Cost"),
    # The stream every run writes to, one line of it, and the file sink a caller subscribes.
    "events": ("Events", "Event", "JsonlSink"),
    # The errors a caller catches, the code each carries, and the token that cancels a run.
    "errors": (
        "DeckTalkError",
        "InputError",
        "NotBuiltError",
        "ProviderError",
        "ToolError",
        "ProjectLocked",
        "ApprovalRequired",
        "Cancelled",
        "Cancel",
        "ErrorCode",
    ),
}
"""Every root name by the module it is imported from, in the order the imports are written."""

PUBLIC_MODULES = ("events", "findings", "results", "settings", "speech")
"""The public modules the root imports, so `decktalk.results` and the rest are attributes of the package."""

DOCSTRING = '''"""DeckTalk: narrated presentation videos, cut to the word.

A project is a directory. `decktalk.open(path)` returns a project, six verbs move it forward, and a
handful of calls report on it, cut a piece out of it or serve it. Every call returns a frozen result
whose findings are diagnostics with a code, a location, a severity and often a fix a caller can
apply. Every call opens a run and writes to one event stream, which any renderer subscribes to.
Nothing in this package prints, nothing reads the environment except `Machine.from_environment`, and
a path a result carries is always relative to the project root.

`__all__` is the root of the supported Python API: the entry points, the errors and the few types
every caller names. Every other public type is in a public module: `decktalk.results` for every
result and row, `decktalk.events` for every event, `decktalk.findings` for the fix and location
types, `decktalk.settings` for the settings tree, and `decktalk.speech` with `decktalk.speech.sound`
for the voice and sound protocols. A type a public name can hand you is a type you can import from
one of them. Everything else may move without notice.
"""'''

VERSION_IMPORT = "from .artifacts.stored import ENGINE_VERSION as __version__"
"""Where `__version__` comes from, which is the one reading of the engine version every cache key carries."""


def render() -> str:
    """The generated module as it is committed, with its imports sorted and wrapped by the project's ruff."""
    imports = [
        VERSION_IMPORT,
        f"from . import {', '.join(f'{name} as {name}' for name in PUBLIC_MODULES)}",
        *(f"from .{module} import {', '.join(names)}" for module, names in ROOT.items()),
    ]
    every = sorted({name for names in ROOT.values() for name in names} | {"__version__"})
    listed = "".join(f'    "{name}",\n' for name in every)
    return generated.ruff(
        "\n".join(
            [DOCSTRING, "", "from __future__ import annotations", "", *imports, "", f"__all__ = [\n{listed}]", ""]
        ),
        TARGET,
    )


def documents() -> dict[Path, str]:
    """The package's __init__.py, which is the one file this generator owns."""
    return {TARGET: render()}


if __name__ == "__main__":
    sys.exit(generated.run(documents))
