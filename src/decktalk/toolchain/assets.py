"""What ships inside the wheel: the page runtime, the pinned KaTeX release, and the starter project.

    decktalk/runtime/decktalk-runtime.js  the page contract every deck loads
    decktalk/katex/                       the pinned KaTeX release with its licence and fonts
    decktalk/template/                    the starter project `decktalk init` writes

`decktalk init` copies the runtime and KaTeX beside a project's pages, so a project renders
equations with no network and no CDN tag.
"""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path

TEMPLATE_FILES = [
    ("decktalk.toml", "decktalk.toml"),
    ("script.md", "script.md"),
    ("cues.json", "cues.json"),
    ("media/markers.json", "media/markers.json"),
    ("gitignore", ".gitignore"),
    ("env.example", ".env.example"),
]
# The deck directory is copied whole, so every page and every asset beside it arrives. Each HTML
# page gets the project name filled in, and every other file, such as the bundled fonts, is
# copied byte for byte.
TEMPLATE_DECK = "deck"
RUNTIME_FILE = "decktalk-runtime.js"

# KaTeX typesets the [data-tex] elements. The pinned release ships inside the wheel under
# decktalk/katex with its licence, and `decktalk init` copies it into deck/katex/, so a project
# renders equations with no network and no CDN tag. The template's pages load it from there.
KATEX_VERSION = "0.18.7"
KATEX_DIR = "katex"
KATEX_FILES = ("katex.min.js", "katex.min.css", "LICENSE")
KATEX_FONT_DIR = "fonts"
_KATEX_FONT_URL = re.compile(r"url\((fonts/[^)]+\.woff2)\)")


def package_file(rel: str) -> Path:
    return Path(str(resources.files("decktalk").joinpath(rel)))


def runtime_path() -> Path:
    return package_file(f"runtime/{RUNTIME_FILE}")


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
    (dst / KATEX_FONT_DIR).mkdir(parents=True)
    for f in KATEX_FILES:
        shutil.copyfile(src / f, dst / f)
    for font in sorted((src / KATEX_FONT_DIR).glob("*.woff2")):
        shutil.copyfile(font, dst / KATEX_FONT_DIR / font.name)
    return dst
