"""The project scaffold and the tool cache.

`decktalk init DIR` writes the template project, with the packaged runtime and KaTeX beside
its pages. `decktalk install` fetches Chromium and ffmpeg into the per-user cache. `decktalk
doctor` reports on all of them.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path

from .errors import ConfigError, ToolError

log = logging.getLogger(__name__)

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


def cache_dir() -> Path:
    """The per-user cache directory, next to Playwright's ms-playwright folder. DECKTALK_CACHE_DIR overrides it."""
    override = os.environ.get("DECKTALK_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "decktalk"


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


def title_from(name: str) -> str:
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "Untitled"


def init(target: Path, *, name: str | None = None, force: bool = False) -> Path:
    """Write the template project into `target`. Returns the project directory."""
    target = target.resolve()
    name = name or target.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ConfigError(f"project name {name!r} must be letters, digits, dots, dashes or underscores")
    if target.exists() and any(target.iterdir()) and not force:
        raise ConfigError(f"{target} is not empty (pass force to write into it anyway)")

    def fill(text: str) -> str:
        return text.replace("__NAME__", name).replace("__TITLE__", title_from(name))

    for src_rel, dst_rel in TEMPLATE_FILES:
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(fill(package_file(f"template/{src_rel}").read_text(encoding="utf-8")), encoding="utf-8")
    deck_src = package_file(f"template/{TEMPLATE_DECK}")
    for src in sorted(deck_src.rglob("*")):
        if not src.is_file() or src.name.startswith(".") or "__pycache__" in src.parts:
            continue
        dst = target / TEMPLATE_DECK / src.relative_to(deck_src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".html":
            dst.write_text(fill(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
    shutil.copyfile(runtime_path(), target / TEMPLATE_DECK / RUNTIME_FILE)
    vendor_katex(target / TEMPLATE_DECK)
    return target


def install() -> None:
    """Fetch the headless Chromium and the pinned ffmpeg build."""
    log.info("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    if subprocess.call(cmd) != 0:
        raise ToolError("playwright install failed. See the output above.")
    from .media.ffmpeg import FFMPEG_VERSION, ffmpeg_paths, installed_pinned

    log.info("== ffmpeg %s", FFMPEG_VERSION)
    found = installed_pinned()
    if found:
        log.info("   cached  %s", found[0])
    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)


@dataclass(frozen=True)
class DoctorRow:
    """One component that `decktalk doctor` reports. Every component it reports is needed to build."""

    name: str
    ok: bool
    detail: str

    def __iter__(self) -> Iterator[str | bool]:
        return iter((self.name, self.ok, self.detail))

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def doctor() -> list[DoctorRow]:
    """One DoctorRow for each of python, chromium, ffmpeg, ffprobe, config and katex.

    Nothing is fetched or written. In particular the ffmpeg row looks for executables that are
    already on disk, because asking for them would download the pinned build.
    """
    rows = [DoctorRow("python", True, f"{sys.version.split()[0]} ({sys.executable})")]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
                rows.append(DoctorRow("chromium", True, b.version))
                b.close()
            except Exception as exc:
                rows.append(DoctorRow("chromium", False, f"{str(exc).splitlines()[0]}  -> run `decktalk install`"))
    except ImportError:
        rows.append(DoctorRow("chromium", False, "playwright package missing"))
    from .media.ffmpeg import installed_paths

    found = installed_paths()
    if found:
        rows.append(DoctorRow("ffmpeg", True, found[0]))
        rows.append(DoctorRow("ffprobe", True, found[1]))
    else:
        rows.append(DoctorRow("ffmpeg", False, "not fetched yet and none on PATH  -> run `decktalk install`"))
    from .settings import user_config_path

    cfg_path = user_config_path()
    rows.append(DoctorRow("config", True, str(cfg_path) if cfg_path.exists() else f"none (optional, at {cfg_path})"))
    missing = katex_missing()
    if missing:
        rows.append(DoctorRow("katex", False, f"{katex_dir()} lacks {', '.join(missing)}  -> reinstall decktalk"))
    else:
        rows.append(DoctorRow("katex", True, f"{KATEX_VERSION} in the wheel ({katex_dir()})"))
    return rows
