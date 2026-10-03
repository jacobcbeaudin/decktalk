"""What ships inside the wheel: the page runtime, the pinned KaTeX release, and the projects.

    decktalk/runtime/decktalk-runtime.js  the page contract every deck loads
    decktalk/runtime/decktalk-probe.js    the instrumentation a command injects, never in a deck
    decktalk/katex/                       the pinned KaTeX release with its licence and fonts
    decktalk/template/starter/            the starter project `decktalk init` writes
    decktalk/template/examples/           one directory per `decktalk init --example NAME`
    decktalk/template/AGENTS.md           written into a project that has none
    decktalk/skills/                      the six skills a project keeps in .agents/skills/

The origin every page is opened at serves the runtime and KaTeX from here, so a project holds no
copy of either, renders equations with no network and no CDN tag, and always plays the contract of
the engine that opens it. `engine_files` is the closed list of what it serves. The probe is never
on that list, because a command injects it into the page it opens. `scaffold/` decides what a
project is made of, and this module only says where each packaged thing lives.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import Path

RUNTIME_FILE = "decktalk-runtime.js"
PROBE_FILE = "decktalk-probe.js"

# KaTeX typesets the [data-tex] elements. The pinned release ships inside the wheel under
# decktalk/katex with its licence, and the origin serves it beside the runtime, so a project renders
# equations with no network and no CDN tag. Every packaged page loads it from there.
KATEX_VERSION = "0.18.7"
KATEX_DIR = "katex"
KATEX_FILES = ("katex.min.js", "katex.min.css", "LICENSE")
_KATEX_FONT_URL = re.compile(r"url\((fonts/[^)]+\.woff2)\)")


def package_file(rel: str) -> Path:
    return Path(str(resources.files("decktalk").joinpath(rel)))


def runtime_path() -> Path:
    return package_file(f"runtime/{RUNTIME_FILE}")


def probe_path() -> Path:
    """The recorder's instrumentation, which `media/browser.py` injects and no project ever holds."""
    return package_file(f"runtime/{PROBE_FILE}")


def katex_dir() -> Path:
    """The packaged KaTeX release: katex.min.js, katex.min.css, LICENSE and fonts/*.woff2."""
    return package_file(KATEX_DIR)


def katex_fonts(css: str) -> list[str]:
    """The woff2 files the stylesheet loads, as `fonts/<name>.woff2`, each once, in order of first use.

    Chromium takes the first source format it supports, and every browser DeckTalk targets
    supports woff2, so the woff and ttf fallbacks the stylesheet also names are not shipped.
    """
    return list(dict.fromkeys(_KATEX_FONT_URL.findall(css)))


def katex_missing(root: Path | None = None) -> list[str]:
    """Files of the KaTeX copy at `root` (the packaged one by default) that are absent."""
    root = root or katex_dir()
    missing = [f for f in KATEX_FILES if not (root / f).is_file()]
    if "katex.min.css" not in missing:
        css = (root / "katex.min.css").read_text(encoding="utf-8")
        missing += [f for f in katex_fonts(css) if not (root / f).is_file()]
    return missing


@cache
def engine_files() -> Mapping[str, Path]:
    """Every file the origin answers from the engine, keyed by its name under the engine's path.

    The list is closed and built from the package alone, so a request is answered by looking its
    name up here and never by joining it onto a directory, and no spelling of a name reaches a file
    that is not on it. The KaTeX fonts are the ones its stylesheet loads, which are the ones shipped.
    """
    katex = katex_dir()
    css = (katex / "katex.min.css").read_text(encoding="utf-8")
    names = (*KATEX_FILES, *katex_fonts(css))
    return {RUNTIME_FILE: runtime_path(), **{f"{KATEX_DIR}/{name}": katex / name for name in names}}
