"""The banner `scripts/build_runtime.py` writes on the runtime bundle, which is how a copy names its engine.

`decktalk init` copies the runtime into a project, and the check that the copy matches the engine
reads the copy's first line without a browser. These tests hold the committed bundles to the line the
generated page module formats, so the writer of the banner and its reader cannot drift apart. The
bundles themselves are held to their sources by `build_runtime.py --check` in the generated group.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from decktalk import page
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


def test_the_runtime_bundle_opens_with_the_banner_of_this_engine() -> None:
    version = build_runtime.engine_version()
    assert first_line(RUNTIME / build_runtime.BANNERED) == page.runtime_banner(version)


def test_the_writer_and_the_reader_spell_the_banner_the_same_way() -> None:
    data = {"runtimeMark": page.RUNTIME_MARK}
    assert build_runtime.banner(data) == page.runtime_banner(build_runtime.engine_version())


def test_a_banner_names_the_version_it_was_given() -> None:
    assert page.runtime_banner("9.9.9") == f"/*! {page.RUNTIME_MARK} 9.9.9 */"
    assert page.runtime_banner("9.9.9") != page.runtime_banner("9.9.10")


def test_the_probe_carries_no_banner_because_no_project_copies_it() -> None:
    assert page.RUNTIME_MARK not in first_line(RUNTIME / "decktalk-probe.js")
