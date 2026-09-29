"""The banner `scripts/build_runtime.py` writes on the runtime bundle, which is how a copy names its engine.

`decktalk init` copies the runtime into a project, and a person who opens that copy reads which
engine shipped it on its first line. The committed bundles are held to their sources by
`build_runtime.py --check`, which builds both sides the same way, so these tests are what holds the
banner to the first line once the formatter has run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from support.paths import REPO


def _generator() -> ModuleType:
    """`scripts/build_runtime.py` as a module, which is the only way to reach a file outside the package."""
    spec = importlib.util.spec_from_file_location("build_runtime", REPO / "scripts" / "build_runtime.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_runtime = _generator()

RUNTIME: Path = build_runtime.RUNTIME
"""The folder the bundles are committed in, read from the generator that writes them."""


def first_line(path: Path) -> str:
    """The first line of a committed bundle, which is where a banner has to be for a reader to find it."""
    with path.open(encoding="utf-8") as handle:
        return handle.readline().rstrip("\n")


MARK: str = json.loads(build_runtime.CONTRACT_JSON.read_text(encoding="utf-8"))["runtimeMark"]
"""The name the banner gives before the version, read from the committed contract."""


def test_the_runtime_bundle_opens_with_the_banner_of_this_engine() -> None:
    version = build_runtime.engine_version()
    assert first_line(RUNTIME / build_runtime.BANNERED) == f"/*! {MARK} {version} */"


def test_the_probe_carries_no_banner_because_no_project_copies_it() -> None:
    assert MARK not in first_line(RUNTIME / "decktalk-probe.js")
