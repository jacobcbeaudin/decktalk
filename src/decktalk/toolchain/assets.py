"""What ships inside the wheel: the page runtime, the pinned KaTeX release, and the projects.

    decktalk/runtime/decktalk-runtime.js  the page contract every deck loads
    decktalk/runtime/decktalk-probe.js    the instrumentation a command injects, never in a deck
    decktalk/katex/                       the pinned KaTeX release with its licence and fonts
    decktalk/template/starter/            the starter project `decktalk init` writes
    decktalk/template/examples/           one directory per `decktalk init --example NAME`
    decktalk/template/AGENTS.md           written into a project that has none
    decktalk/skills/                      the six skills a project keeps in .agents/skills/

`decktalk init` copies the runtime and KaTeX beside a project's pages, so a project renders
equations with no network and no CDN tag. The probe is never copied, because a command injects it
into the page it opens. `scaffold/` decides what a project is made of, and this module only says
where each packaged thing lives.
"""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path

RUNTIME_FILE = "decktalk-runtime.js"
PROBE_FILE = "decktalk-probe.js"

SHIPPED_RUNTIMES = (
    "98519ba8be6e4560790322fc526aa6167098676ce0bfa9de11145d3b05faca22",  # v0.1.0
    "9ea4d183d3ee57a718b2c1238af72e8ddba855abb7143a5f8fb621c7a63b3458",  # v0.2.0 and v0.2.1
    "01e901c20434acbe0db729532a4ce57334979c8a9392f73af0e93c708779ce43",  # v0.3.0
    "095fd5df5efba829eb8e44d4bbdc3c79b1ef590c9bc2773897b2ffba55ffe4fc",  # v0.4.0
    "962e1d85c71590c22e22f0fd4b4012a3355fbf56089939029331d802e737c034",  # v0.4.1
    "8e56d23fcd755174405f7effbb331b73d3968e824c97041aed19da22673a5602",  # v0.5.0-rc1
    "9c5a49e0dfe6fe8a176809606459417f44c1124218b2b946563cba753e300f7a",  # v0.5.0-rc2
    "f362f904f6dfa8bf06bf2e5063f37dcb1dcd4299c0014b5cf7b6b2c0adc5034a",  # v0.5.0
)
"""The sha256 of the runtime each release tag shipped, which is how a copy nobody edited is told apart.

A copy with one of these digests is an engine's own bytes and holds none of the author's work, so
replacing it is safe. Each release adds its own digest once it is tagged.
"""

# KaTeX typesets the [data-tex] elements. The pinned release ships inside the wheel under
# decktalk/katex with its licence, and `decktalk init` copies it into deck/katex/, so a project
# renders equations with no network and no CDN tag. Every packaged page loads it from there.
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


def vendor_katex(deck_dir: Path) -> Path:
    """Copy the packaged KaTeX into deck/katex/, replacing whatever was there. Returns that directory."""
    src, dst = katex_dir(), deck_dir / KATEX_DIR
    shutil.rmtree(dst, ignore_errors=True)
    # The packaged folder holds exactly the release files, which tests/contract/test_wheel.py holds
    # against what git tracks, so the whole tree is the copy. copyfile leaves the package's modes behind.
    shutil.copytree(src, dst, copy_function=shutil.copyfile)
    return dst
